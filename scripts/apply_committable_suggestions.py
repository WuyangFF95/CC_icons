#!/usr/bin/env python3
"""
apply_committable_suggestions.py — apply CR / GitHub-native suggestion blocks
WITHOUT calling any LLM.

CodeRabbit reviews almost always include a `<details>` block with a
````suggestion …``` patch that GitHub natively understands (the
"Commit suggestion" button in the UI does the same thing). This script
walks every unaddressed review thread, extracts the first suggestion
block when present, and applies it directly to the file at the
indicated line range — git-only, no API key, no token spend.

Typically clears 50–80 % of round-1 review comments for free; the
remaining longer-form comments still need human or LLM judgment.

Usage:
  python scripts/apply_committable_suggestions.py --pr 13
  python scripts/apply_committable_suggestions.py --pr 13 --dry-run

Exit codes:
  0  applied at least one suggestion (or none to apply with --dry-run)
  1  fatal error (auth, repo state, etc.)
  2  ran cleanly but nothing applied
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


SUGGESTION_RE = re.compile(
    r"```suggestion\s*\n(?P<body>.*?)\n```",
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
    start_line: int  # 1-based, inclusive
    end_line: int    # 1-based, inclusive (single-line if == start_line)
    new_text: str    # the replacement block (no trailing newline)


def _extract_suggestion(comment: dict) -> Suggestion | None:
    """Return the first ```suggestion``` block in this review comment, or None."""
    body = comment.get("body") or ""
    match = SUGGESTION_RE.search(body)
    if not match:
        return None

    # GitHub stores the comment's anchor as `line` (single-line) or
    # `start_line` + `line` (multi-line range). `original_*` would point
    # at the pre-edit line numbers; we want the current ones.
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


def _apply_one(svg_path: Path, sug: Suggestion) -> bool:
    """Replace lines [start_line, end_line] (1-based inclusive) with new_text."""
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
    # GitHub's suggestion semantics: replace [start_line, end_line] inclusive.
    # Preserve a trailing newline if the last replaced line had one.
    last_orig_line = lines[sug.end_line - 1]
    if last_orig_line.endswith(("\n", "\r\n")) and new_lines and not new_lines[-1].endswith(("\n", "\r\n")):
        new_lines[-1] = new_lines[-1] + "\n"

    out = lines[: sug.start_line - 1] + new_lines + lines[sug.end_line:]
    target.write_text("".join(out), encoding="utf-8")
    return True


def _list_unaddressed_threads(pr_number: int) -> list[dict]:
    """Return CR / Devin review comments that have no human/bot reply yet."""
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply CR/GitHub committable suggestion blocks "
                    "without any LLM call.",
    )
    parser.add_argument("--pr", type=int, required=True, help="PR number")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would change without writing files")
    parser.add_argument("--reply", action="store_true",
                        help="also post a per-thread reply ack on each "
                             "applied suggestion")
    args = parser.parse_args()

    threads = _list_unaddressed_threads(args.pr)
    print(f"Scanning {len(threads)} unaddressed thread(s) on PR #{args.pr}...")

    suggestions: list[Suggestion] = []
    for t in threads:
        sug = _extract_suggestion(t)
        if sug is not None:
            suggestions.append(sug)

    print(f"Extracted {len(suggestions)} committable suggestion block(s).")
    if not suggestions:
        return 2

    applied: list[Suggestion] = []
    for sug in suggestions:
        if args.dry_run:
            print(f"  [dry] {sug.path}:{sug.start_line}-{sug.end_line} "
                  f"(comment {sug.comment_id})")
            continue
        ok = _apply_one(Path("."), sug)
        if ok:
            applied.append(sug)
            print(f"  [applied] {sug.path}:{sug.start_line}-{sug.end_line} "
                  f"(comment {sug.comment_id})")

    if args.dry_run:
        print(f"\n[dry-run] would apply {len(suggestions)} suggestions to "
              f"{len({s.path for s in suggestions})} file(s).")
        return 0

    if not applied:
        return 2

    # Stage + commit only the touched files (don't sweep up unrelated edits).
    touched = sorted({s.path for s in applied})
    subprocess.run(["git", "add", "--", *touched], check=True)
    subprocess.run(
        ["git", "commit", "-m",
         f"fix: apply {len(applied)} committable suggestion(s) "
         f"from PR #{args.pr}\n"
         f"fix: 应用 PR #{args.pr} 的 {len(applied)} 条 committable suggestion\n\n"
         "Auto-applied by scripts/apply_committable_suggestions.py — these "
         "are GitHub's native ```suggestion``` blocks emitted by reviewers, "
         "applied verbatim with no LLM in the loop.\n"
         "由 scripts/apply_committable_suggestions.py 自动应用 —— 这些是 "
         "评审方发的 GitHub 原生 ```suggestion``` 块，逐字应用，无 LLM 介入。"],
        check=True,
    )

    if args.reply:
        commit_sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        for sug in applied:
            ack = (
                f"Auto-applied verbatim in `{commit_sha}` by "
                f"`apply_committable_suggestions.py` — no LLM in the loop, "
                f"so this fix is exactly what your committable suggestion said.\n\n"
                f"由 `apply_committable_suggestions.py` 在 `{commit_sha}` 自动按字面"
                f"应用 —— 无 LLM 参与，这条修订就是你 committable suggestion 的原文。"
            )
            try:
                _gh([
                    "api", "-X", "POST",
                    f"repos/{_repo()}/pulls/{args.pr}/comments/"
                    f"{sug.comment_id}/replies",
                    "-f", f"body={ack}",
                ])
            except subprocess.CalledProcessError as exc:
                print(f"  [warn] reply failed on {sug.comment_id}: {exc.stderr}",
                      file=sys.stderr)

    print(f"\nApplied {len(applied)} suggestion(s) across "
          f"{len(touched)} file(s); commit ready to push.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
