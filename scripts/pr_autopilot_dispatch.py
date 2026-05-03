#!/usr/bin/env python3
"""
pr_autopilot_dispatch.py — orchestrator for the PR Autopilot workflow.

Three subcommands match the three GHA job stages:

  classify           Decide whether the autopilot should run, and in what
                     mode (`fix` vs `merge`). Emits GitHub Actions outputs.
  prepare-bundle     Collect every unaddressed CR / Devin review thread
                     into a single JSON bundle the fix step consumes.
  reply-and-summarize  After Claude pushes the fix commit, post a
                     bilingual reply on each addressed thread + a
                     top-level summary on the PR.

Each subcommand is independently testable; the workflow YAML wires them
together. Designed so the autopilot's behaviour is auditable from the
script (you read what it does) rather than buried in a workflow log.

Usage from GHA:
  python scripts/pr_autopilot_dispatch.py classify \\
      --event pull_request_review --pr 42 --emit-github-output
  python scripts/pr_autopilot_dispatch.py prepare-bundle \\
      --pr 42 --output /tmp/bundle.json
  python scripts/pr_autopilot_dispatch.py reply-and-summarize \\
      --pr 42 --bundle /tmp/bundle.json --commit-sha abc1234
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Bots whose review comments the autopilot acts on.
REVIEW_BOTS = ("coderabbitai[bot]", "devin-ai-integration[bot]")

# Bots / users whose own comments the autopilot must NOT loop on.
SELF_LOOP_ACTORS = ("github-actions[bot]", "claude[bot]")

# Severity prefixes the autopilot recognizes from CR's standard body.
SEVERITY_PRIORITY = {
    "🔴": 0,  # critical
    "🟠": 1,  # major
    "🟡": 2,  # minor
}


# ---------------------------------------------------------------------------
# gh CLI helpers
# ---------------------------------------------------------------------------


def _gh(args: list[str]) -> str:
    """Run `gh <args>` with strict error mode; return stdout."""
    if not shutil.which("gh"):
        raise RuntimeError("gh CLI not found on PATH")
    result = subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=True
    )
    return result.stdout


def _gh_json(args: list[str]) -> Any:
    return json.loads(_gh(args))


# ---------------------------------------------------------------------------
# classify: should we run, and what mode?
# ---------------------------------------------------------------------------


@dataclass
class Classification:
    should_run: bool
    pr_number: int | None
    mode: str  # 'fix' | 'merge' | 'noop'
    reason: str

    def emit_github_output(self) -> None:
        out = os.environ.get("GITHUB_OUTPUT")
        if not out:
            return
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"should_run={'true' if self.should_run else 'false'}\n")
            f.write(f"pr_number={self.pr_number or ''}\n")
            f.write(f"mode={self.mode}\n")


def classify(event: str, pr_number: int | None) -> Classification:
    if pr_number is None or pr_number <= 0:
        return Classification(False, None, "noop", "no PR number resolved from event")

    pr = _gh_json([
        "pr", "view", str(pr_number),
        "--json", "state,isDraft,reviewDecision,mergeable,headRefName,number",
    ])
    if pr.get("state") != "OPEN":
        return Classification(False, pr_number, "noop", f"PR is {pr.get('state')}")
    if pr.get("isDraft"):
        return Classification(False, pr_number, "noop", "PR is draft")

    review_decision = pr.get("reviewDecision") or ""
    mergeable = pr.get("mergeable") or ""
    if review_decision == "APPROVED" and mergeable == "MERGEABLE":
        return Classification(True, pr_number, "merge",
                              "APPROVED + MERGEABLE — squash-merge")

    # Otherwise look for unaddressed CR/Devin threads.
    unaddressed = _list_unaddressed_threads(pr_number)
    if unaddressed:
        return Classification(True, pr_number, "fix",
                              f"{len(unaddressed)} unaddressed review thread(s)")
    return Classification(False, pr_number, "noop",
                          "no unaddressed threads + not APPROVED yet (waiting for review)")


# ---------------------------------------------------------------------------
# bundle: collect unaddressed review threads
# ---------------------------------------------------------------------------


@dataclass
class ThreadComment:
    id: int
    user: str
    path: str | None
    line: int | None
    body: str
    in_reply_to_id: int | None = None
    created_at: str = ""

    @classmethod
    def from_api(cls, raw: dict) -> "ThreadComment":
        return cls(
            id=raw["id"],
            user=(raw.get("user") or {}).get("login", ""),
            path=raw.get("path"),
            line=raw.get("line"),
            body=raw.get("body") or "",
            in_reply_to_id=raw.get("in_reply_to_id"),
            created_at=raw.get("created_at") or "",
        )


def _list_unaddressed_threads(pr_number: int) -> list[ThreadComment]:
    """Return root review comments by review bots that have NO author reply yet.

    "Author reply" means a reply by anyone EXCEPT the review bots — i.e. the
    PR author or the autopilot's own claude[bot]. We intentionally accept
    a reply by either as "this thread has been addressed", because both
    flows ultimately reflect the autopilot's work.
    """
    raw = _gh_json([
        "api", "--paginate",
        f"repos/{_repo()}/pulls/{pr_number}/comments",
    ])
    by_id: dict[int, ThreadComment] = {}
    children: dict[int, list[ThreadComment]] = {}
    for r in raw:
        c = ThreadComment.from_api(r)
        by_id[c.id] = c
        if c.in_reply_to_id is not None:
            children.setdefault(c.in_reply_to_id, []).append(c)

    unaddressed: list[ThreadComment] = []
    for c in by_id.values():
        if c.in_reply_to_id is not None:
            continue
        if c.user not in REVIEW_BOTS:
            continue
        # Has any reply by a non-review-bot?
        replies = children.get(c.id, [])
        if any(r.user not in REVIEW_BOTS for r in replies):
            continue
        unaddressed.append(c)

    # Sort by severity (critical first), then created_at
    def severity_rank(c: ThreadComment) -> tuple[int, str]:
        for marker, rank in SEVERITY_PRIORITY.items():
            if marker in c.body:
                return (rank, c.created_at)
        return (3, c.created_at)
    unaddressed.sort(key=severity_rank)
    return unaddressed


def prepare_bundle(pr_number: int, output: Path) -> dict:
    threads = _list_unaddressed_threads(pr_number)
    pr = _gh_json([
        "pr", "view", str(pr_number),
        "--json", "number,title,headRefName,baseRefName,body,additions,deletions,changedFiles",
    ])
    bundle = {
        "pr": pr,
        "thread_count": len(threads),
        "threads": [
            {
                "id": t.id,
                "user": t.user,
                "path": t.path,
                "line": t.line,
                "body": t.body,
                "created_at": t.created_at,
            }
            for t in threads
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(bundle, indent=2, ensure_ascii=False),
                      encoding="utf-8")
    print(f"Wrote bundle: {output}")
    print(f"  threads: {len(threads)}")
    print(f"  PR: #{pr['number']} ({pr['changedFiles']} files, "
          f"+{pr['additions']}/-{pr['deletions']})")
    return bundle


# ---------------------------------------------------------------------------
# reply-and-summarize: after Claude pushes the fix commit
# ---------------------------------------------------------------------------


def reply_and_summarize(
    pr_number: int,
    bundle_path: Path,
    commit_sha: str,
    disposition_path: Path | None = None,
) -> None:
    """Reply per-thread according to a machine-readable disposition map.

    The disposition file is written by the fix step (the LLM) and
    contains one entry per thread it considered:

      {
        "<thread_id>": {
          "kind": "fix" | "ack" | "skip",
          "reply": "<bilingual reply body>"
        },
        ...
      }

    - `kind=fix`  → post the reply (typically referencing `commit_sha`).
                    Thread is now considered addressed.
    - `kind=ack`  → post the reply explaining why we acknowledged
                    without code change (out-of-scope, future work).
                    Thread is considered addressed.
    - `kind=skip` → DO NOT POST anything. The thread stays unaddressed
                    so the next round of review surfaces it again
                    (matches `fix_round.md`'s "leave it alone if you
                    don't know how to fix it" rule).

    Threads not present in the disposition map default to `skip` —
    safer than blanket "Addressed" replies that silently bury reviews.
    """
    bundle = json.loads(bundle_path.read_text())
    threads = bundle.get("threads", [])
    if not threads:
        print("No threads in bundle; nothing to reply to.")
        return

    dispositions: dict[str, dict] = {}
    if disposition_path is not None and disposition_path.exists():
        try:
            dispositions = json.loads(disposition_path.read_text())
        except json.JSONDecodeError as exc:
            print(f"[warn] disposition file unparseable, falling back to "
                  f"skip-all: {exc}", file=sys.stderr)
            dispositions = {}

    fixed = 0
    acked = 0
    skipped = 0
    for t in threads:
        d = dispositions.get(str(t["id"])) or {}
        kind = d.get("kind", "skip")
        if kind == "skip":
            skipped += 1
            continue
        body = d.get("reply") or _default_reply(kind, commit_sha)
        try:
            _gh([
                "api", "-X", "POST",
                f"repos/{_repo()}/pulls/{pr_number}/comments/{t['id']}/replies",
                "-f", f"body={body}",
            ])
            if kind == "fix":
                fixed += 1
            else:
                acked += 1
        except subprocess.CalledProcessError as exc:
            print(f"[warn] failed to reply to thread {t['id']}: {exc.stderr}",
                  file=sys.stderr)

    # Top-level summary.
    summary = (
        f"## 🤖 PR Autopilot · round addressed in `{commit_sha}`\n\n"
        f"- **fixed**: {fixed}\n"
        f"- **acknowledged (no code change)**: {acked}\n"
        f"- **skipped (will re-surface next review pass)**: {skipped}\n\n"
        f"修复 {fixed} · 致谢未改 {acked} · 暂搁 {skipped}\n\n"
        f"<sub>The autopilot will wait for the next review pass; if "
        f"`reviewDecision` flips to APPROVED + MERGEABLE, the next workflow "
        f"run will squash-merge this PR. Skipped threads remain "
        f"unaddressed on purpose so review attention isn't silently buried.</sub>"
    )
    try:
        _gh(["pr", "comment", str(pr_number), "--body", summary])
    except subprocess.CalledProcessError as exc:
        print(f"[warn] failed to post summary: {exc.stderr}", file=sys.stderr)


def _default_reply(kind: str, commit_sha: str) -> str:
    """Fallback reply body when the LLM disposition doesn't specify one."""
    if kind == "fix":
        return (
            f"Addressed in `{commit_sha}` by PR Autopilot.\n\n"
            f"已在 `{commit_sha}` 由 PR Autopilot 处理。"
        )
    return (
        f"Acknowledged by PR Autopilot — no code change in this round. "
        f"See commit `{commit_sha}` for context.\n\n"
        f"PR Autopilot 致谢，本轮无代码改动。详见提交 `{commit_sha}`。"
    )


# ---------------------------------------------------------------------------
# misc
# ---------------------------------------------------------------------------


def _repo() -> str:
    """Return `<owner>/<repo>` from the GitHub Actions env or git remote."""
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_class = sub.add_parser("classify", help="decide should_run / mode")
    p_class.add_argument("--event", required=True,
                         help="GitHub event name (e.g. pull_request_review)")
    p_class.add_argument("--pr", type=int, default=None)
    p_class.add_argument("--emit-github-output", action="store_true")

    p_bundle = sub.add_parser("prepare-bundle",
                              help="dump unaddressed review threads to JSON")
    p_bundle.add_argument("--pr", type=int, required=True)
    p_bundle.add_argument("--output", type=Path, required=True)

    p_reply = sub.add_parser("reply-and-summarize",
                             help="post per-thread + summary comments")
    p_reply.add_argument("--pr", type=int, required=True)
    p_reply.add_argument("--bundle", type=Path, required=True)
    p_reply.add_argument("--commit-sha", required=True)
    p_reply.add_argument("--disposition-file", type=Path, default=None,
                         help="JSON map { '<thread_id>': {kind, reply} } "
                              "produced by the fix step. Threads not listed "
                              "default to skip (no reply posted).")

    args = parser.parse_args()

    if args.cmd == "classify":
        result = classify(args.event, args.pr)
        print(f"should_run={result.should_run} mode={result.mode} "
              f"pr={result.pr_number} reason={result.reason}")
        if args.emit_github_output:
            result.emit_github_output()
        return 0

    if args.cmd == "prepare-bundle":
        prepare_bundle(args.pr, args.output)
        return 0

    if args.cmd == "reply-and-summarize":
        reply_and_summarize(
            args.pr, args.bundle, args.commit_sha,
            disposition_path=args.disposition_file,
        )
        return 0

    parser.error(f"unknown subcommand: {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
