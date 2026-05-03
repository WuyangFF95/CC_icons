#!/usr/bin/env python3
"""
apply_committable_suggestions.py — apply CR / GitHub-native suggestion blocks
WITHOUT calling any LLM.

Two subcommands:

  apply           Walk every unaddressed CR / Devin review thread, extract
                  any ```suggestion``` block, apply it directly to the
                  file at the indicated line range, commit. Optionally
                  emit `--emit-applied` JSON listing what landed.
                  Does NOT push and does NOT reply — those are gated on
                  push success in the workflow.

  reply-applied   Post a per-thread reply on every successfully-applied
                  suggestion. Run only after the workflow's push step
                  confirmed the commit landed remotely.

Exit codes (apply):
  0  applied at least one suggestion
  1  fatal error (auth, repo state, etc.)
  2  ran cleanly but nothing to apply (workflow keeps going to Tier 1/2)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


# Same allow-list the workflow's Path guard step enforces. Tier 0 enforces
# it itself too so a malicious / mis-aimed suggestion can't escape into
# tests/ or other unrelated trees before it even reaches the path guard.
ALLOWED_PATH_RE = re.compile(
    r"^(?:scripts/|_journal-configs/|_palettes/|templates/|"
    r"\.github/workflows/|SKILL\.md$|README\.md$)"
)
DENY_PATHS = frozenset({
    ".github/workflows/pr-autopilot.yml",
    "scripts/pr_autopilot_dispatch.py",
    "scripts/apply_committable_suggestions.py",  # don't let suggestions edit ourselves
})

# `\n?` makes the trailing newline before the closing fence optional, so
# empty / line-deletion suggestions (```suggestion\n``` with no body) match.
SUGGESTION_RE = re.compile(
    r"```suggestion\s*\n(?P<body>.*?)\n?```",
    re.DOTALL,
)


def _gh(args: list[str]) -> str:
    if not shutil.which("gh"):
        raise RuntimeError("gh CLI not found on PATH")
    out = subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=True
    )
    return out.stdout


def _gh_json(args: list[str]) -> object:
    return json.loads(_gh(args))


def _repo() -> str:
    env = os.environ.get("GITHUB_REPOSITORY")
    if env:
        return env
    remote = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if remote.endswith(".git"):
        remote = remote[:-4]
    if "github.com" in remote:
        return remote.split("github.com", 1)[1].lstrip(":/")
    raise RuntimeError(f"could not derive owner/repo from remote: {remote}")


@dataclass
class Suggestion:
    comment_id: int
    path: str
    start_line: int
    end_line: int
    new_text: str


def _is_path_allowed(path: str) -> bool:
    if path in DENY_PATHS:
        return False
    return bool(ALLOWED_PATH_RE.match(path))


def _extract_suggestion(comment: dict) -> Suggestion | None:
    body = comment.get("body") or ""
    match = SUGGESTION_RE.search(body)
    if not match:
        return None
    line = comment.get("line")
    start_line = comment.get("start_line") or line
    if line is None or start_line is None:
        return None
    path = comment.get("path")
    if not path:
        return None
    return Suggestion(
        comment_id=comment["id"],
        path=path,
        start_line=int(start_line),
        end_line=int(line),
        new_text=match.group("body").rstrip("\n"),
    )


def _apply_one(sug: Suggestion) -> bool:
    """Replace lines [start_line, end_line] with new_text. Return True iff the
    file actually changed (not just rewritten with identical bytes)."""
    if not _is_path_allowed(sug.path):
        print(f"  [skip {sug.comment_id}] {sug.path} not in allow-list "
              f"(scripts/ / _journal-configs/ / _palettes/ / templates/ / "
              f"SKILL.md / README.md / .github/workflows/, minus the "
              f"autopilot's own files)", file=sys.stderr)
        return False
    target = Path(sug.path)
    if not target.exists():
        print(f"  [skip {sug.comment_id}] file missing: {sug.path}",
              file=sys.stderr)
        return False
    lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    if sug.start_line < 1 or sug.end_line > len(lines):
        print(f"  [skip {sug.comment_id}] line range "
              f"{sug.start_line}-{sug.end_line} outside file "
              f"{sug.path} (len={len(lines)})",
              file=sys.stderr)
        return False

    new_lines = sug.new_text.splitlines(keepends=True)
    last_orig_line = lines[sug.end_line - 1]
    if last_orig_line.endswith(("\n", "\r\n")) and new_lines and not new_lines[-1].endswith(("\n", "\r\n")):
        new_lines[-1] = new_lines[-1] + "\n"

    out_text = "".join(lines[: sug.start_line - 1] + new_lines + lines[sug.end_line:])
    old_text = "".join(lines)
    if out_text == old_text:
        # No-op suggestion (text already matches). Returning False here
        # avoids an empty `git commit` and keeps stats honest.
        print(f"  [skip {sug.comment_id}] no-op: text already matches",
              file=sys.stderr)
        return False
    target.write_text(out_text, encoding="utf-8")
    return True


def _list_unaddressed_threads(pr_number: int) -> list[dict]:
    raw = _gh_json([
        "api", "--paginate",
        f"repos/{_repo()}/pulls/{pr_number}/comments",
    ])
    by_id: dict[int, dict] = {c["id"]: c for c in raw}
    children: dict[int, list[dict]] = {}
    for c in raw:
        rt = c.get("in_reply_to_id")
        if rt is not None:
            children.setdefault(rt, []).append(c)

    review_bots = ("coderabbitai[bot]", "devin-ai-integration[bot]")
    out: list[dict] = []
    for c in by_id.values():
        if c.get("in_reply_to_id"):
            continue
        if (c.get("user") or {}).get("login") not in review_bots:
            continue
        replies = children.get(c["id"], [])
        if any(
            (r.get("user") or {}).get("login") not in review_bots
            for r in replies
        ):
            continue
        out.append(c)
    return out


# ---------------------------------------------------------------------------
# subcommand: apply
# ---------------------------------------------------------------------------


def cmd_apply(pr_number: int, dry_run: bool, emit_applied: Path | None) -> int:
    threads = _list_unaddressed_threads(pr_number)
    print(f"Scanning {len(threads)} unaddressed thread(s) on PR #{pr_number}...")

    suggestions: list[Suggestion] = []
    for t in threads:
        sug = _extract_suggestion(t)
        if sug is not None:
            suggestions.append(sug)

    print(f"Extracted {len(suggestions)} committable suggestion block(s).")
    if not suggestions:
        return 2

    # Sort by (path, -start_line) so multiple suggestions targeting the same
    # file are applied bottom-up. Without this, an earlier (lower-line)
    # suggestion's net delta would shift later suggestions' line numbers
    # under their feet and silently corrupt the wrong region.
    suggestions.sort(key=lambda s: (s.path, -s.start_line))

    applied: list[Suggestion] = []
    for sug in suggestions:
        if dry_run:
            print(f"  [dry] {sug.path}:{sug.start_line}-{sug.end_line} "
                  f"(comment {sug.comment_id})")
            continue
        if _apply_one(sug):
            applied.append(sug)
            print(f"  [applied] {sug.path}:{sug.start_line}-{sug.end_line} "
                  f"(comment {sug.comment_id})")

    if dry_run:
        print(f"\n[dry-run] would apply {len(suggestions)} suggestions to "
              f"{len({s.path for s in suggestions})} file(s).")
        return 0

    if not applied:
        return 2

    touched = sorted({s.path for s in applied})
    subprocess.run(["git", "add", "--", *touched], check=True)
    subprocess.run(
        ["git", "commit", "-m",
         f"fix: apply {len(applied)} committable suggestion(s) "
         f"from PR #{pr_number}\n"
         f"fix: 应用 PR #{pr_number} 的 {len(applied)} 条 committable suggestion\n\n"
         "Auto-applied by scripts/apply_committable_suggestions.py — these "
         "are GitHub's native ```suggestion``` blocks emitted by reviewers, "
         "applied verbatim with no LLM in the loop.\n"
         "由 scripts/apply_committable_suggestions.py 自动应用 —— 这些是 "
         "评审方发的 GitHub 原生 ```suggestion``` 块，逐字应用，无 LLM 介入。"],
        check=True,
    )

    if emit_applied is not None:
        emit_applied.parent.mkdir(parents=True, exist_ok=True)
        emit_applied.write_text(
            json.dumps([asdict(s) for s in applied], ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"Emitted: {emit_applied}")

    print(f"\nApplied {len(applied)} suggestion(s) across "
          f"{len(touched)} file(s); commit ready to push.")
    return 0


# ---------------------------------------------------------------------------
# subcommand: reply-applied  (only run after push lands)
# ---------------------------------------------------------------------------


def cmd_reply_applied(pr_number: int, applied_path: Path, commit_sha: str) -> int:
    if not applied_path.exists():
        print(f"[reply-applied] no applied file at {applied_path}; "
              f"nothing to ack", file=sys.stderr)
        return 0
    try:
        records = json.loads(applied_path.read_text())
    except json.JSONDecodeError as exc:
        print(f"[reply-applied] could not parse {applied_path}: {exc}",
              file=sys.stderr)
        return 1
    if not records:
        return 0

    ack = (
        f"Auto-applied verbatim in `{commit_sha}` by "
        f"`apply_committable_suggestions.py` — no LLM in the loop, "
        f"so this fix is exactly what your committable suggestion said.\n\n"
        f"由 `apply_committable_suggestions.py` 在 `{commit_sha}` 自动按字面"
        f"应用 —— 无 LLM 参与，这条修订就是你 committable suggestion 的原文。"
    )
    posted = 0
    for r in records:
        try:
            _gh([
                "api", "-X", "POST",
                f"repos/{_repo()}/pulls/{pr_number}/comments/"
                f"{r['comment_id']}/replies",
                "-f", f"body={ack}",
            ])
            posted += 1
        except subprocess.CalledProcessError as exc:
            print(f"  [warn] reply failed on {r['comment_id']}: {exc.stderr}",
                  file=sys.stderr)
    print(f"Posted {posted} ack reply/replies.")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply CR/GitHub committable suggestion blocks "
                    "without any LLM call.",
    )
    sub = parser.add_subparsers(dest="cmd")

    p_apply = sub.add_parser("apply", help="apply suggestions + commit (no push, no reply)")
    p_apply.add_argument("--pr", type=int, required=True)
    p_apply.add_argument("--dry-run", action="store_true")
    p_apply.add_argument("--emit-applied", type=Path, default=None,
                         help="write a JSON list of applied suggestions")

    p_reply = sub.add_parser("reply-applied",
                             help="post per-thread ack on each applied suggestion")
    p_reply.add_argument("--pr", type=int, required=True)
    p_reply.add_argument("--applied", type=Path, required=True)
    p_reply.add_argument("--commit-sha", required=True)

    # Backward-compat: the old top-level args (--pr / --dry-run / --reply / --emit-applied)
    # still work and route to `apply`. The workflow has been updated to use
    # the new subcommand form, but human invocations (and any old workflow
    # snapshots) keep functioning.
    parser.add_argument("--pr", type=int, default=None,
                        help="(deprecated) shorthand for `apply --pr ...`")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reply", action="store_true",
                        help="(deprecated) old single-step apply+reply mode; "
                             "split into `apply` + `reply-applied` for safety")
    parser.add_argument("--emit-applied", type=Path, default=None,
                        help="(deprecated, alias) write a JSON list of "
                             "applied suggestions")

    args = parser.parse_args()

    if args.cmd == "apply":
        return cmd_apply(args.pr, args.dry_run, args.emit_applied)
    if args.cmd == "reply-applied":
        return cmd_reply_applied(args.pr, args.applied, args.commit_sha)

    # Top-level / no-subcommand legacy mode
    if args.pr is not None:
        return cmd_apply(args.pr, args.dry_run, args.emit_applied)

    parser.error("specify a subcommand: apply | reply-applied")
    return 2


if __name__ == "__main__":
    sys.exit(main())
