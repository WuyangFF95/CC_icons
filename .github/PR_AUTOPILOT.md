# PR Autopilot — automated CR / Devin review-fix-merge loop

The autopilot runs the same loop I [@WuyangFF95] have been driving by hand
across PR#1 (6 rounds, 35 review items) and PR#12 (2 rounds, 28 review
items): read review comments → fix code → reply per-thread → wait for
re-review → repeat → squash-merge once `reviewDecision == APPROVED` AND
`mergeable == MERGEABLE`.

> **Status**: shipped in v0.1.3 PR#13. Disabled by default until
> `ANTHROPIC_API_KEY` is added to repo secrets — workflow no-ops without it.

PR Autopilot 自动化我（@WuyangFF95）在 PR#1（6 轮 35 评审项）和 PR#12（2
轮 28 评审项）手动跑的同一个循环：读评审 → 改代码 → 逐 thread 双语回复
→ 等再评 → 循环 → 在 `reviewDecision == APPROVED` + `mergeable == MERGEABLE`
时 squash-merge。

## How it works

```
┌─ event: PR opened / synchronized / review submitted / review comment ─┐
│                                                                       │
│   ┌────────────────┐                                                  │
│   │ classify       │  reads PR state via gh CLI; decides:             │
│   │  (3 sec)       │  • mode=fix   → unaddressed CR/Devin threads     │
│   └─────┬──────────┘  • mode=merge → APPROVED + MERGEABLE             │
│         │             • noop       → otherwise                        │
│   ┌─────▼──────────┐                                                  │
│   │ fix            │  prepare-bundle: dump unaddressed threads        │
│   │  (5–20 min)    │  → anthropics/claude-code-action: read repo +    │
│   │                │     bundle, write patches, commit + push         │
│   │                │  → reply-and-summarize: per-thread + summary     │
│   └────────────────┘                                                  │
│   ┌────────────────┐                                                  │
│   │ merge          │  squash merge with the PR title + body, post     │
│   │  (5 sec)       │  a "🤖 PR Autopilot squash-merged" notice        │
│   └────────────────┘                                                  │
└───────────────────────────────────────────────────────────────────────┘
```

The fix step never decides on its own to merge; that requires a separate
event (the next CR/Devin review submission, or a human re-review) that
flips `reviewDecision` to `APPROVED`. This is intentional — the autopilot
gates merging on a real third-party review, not on its own judgement.

修复步骤永远不会自己决定合并；合并需要单独的事件（下次 CR/Devin 评审、
或人类重新评审）把 `reviewDecision` 翻成 `APPROVED`。这是有意的——
autopilot 把合并门控交给真正的第三方评审，不是它自己的判断。

## Setup

1. **Add the Anthropic API key** to this repo's secrets:
   ```
   gh secret set ANTHROPIC_API_KEY -b "<your-key>"
   ```

2. **Verify** the workflow is enabled:
   ```
   gh workflow list
   gh workflow view "PR Autopilot"
   ```

3. **Test** on the next PR you open. The first run will dispatch on the
   `pull_request opened` event; subsequent runs trigger on
   `pull_request_review_comment` (CR / Devin reviews) and `synchronize`
   (your own pushes). Watch the Actions tab.

## Safety boundaries

The workflow embeds several hard rules:

- **`allow_paths` allowlist**: the autopilot can only touch
  `scripts/**`, `_journal-configs/**`, `_palettes/**`, `templates/**`,
  `SKILL.md`, `README.md`, `.github/workflows/**`. Any patch that strays
  is rejected by `claude-code-action`.
- **No self-loop**: the `triage` job skips events from
  `github-actions[bot]` and `claude[bot]`, so the autopilot's own pushes
  and replies don't re-trigger it.
- **Concurrency lock**: per-PR group with `cancel-in-progress: false` —
  in-flight fix jobs always finish; new comments queue.
- **Draft PRs are skipped**: `triage` only runs when the PR is OPEN and
  not a draft. Mark a PR draft to pause the autopilot mid-cycle.
- **Hard rules in `scripts/autopilot_prompts/fix_round.md`**: never push
  to main, never force-push, never edit the autopilot itself, never
  create new files in user-curated content directories, leave threads
  alone if you don't know how to fix them.

## Limitations

- **API cost**: each fix round dispatches one Claude call with the bundled
  threads + repo context. PR#12's two rounds at this scale cost roughly
  $0.50–$2 each via Anthropic API; budget accordingly.
- **No live CodeRabbit free tier**: GitHub Actions still works on public
  repos free, but CR's review free tier rate-limits at ~5 PRs/month.
- **No automatic test execution**: the autopilot relies on syntax /
  CLI-help / template-validity smoke tests inside the prompt, not a real
  test suite. If your PR has tests, the workflow should be extended to
  run `pytest` after the fix step.
- **Single-actor concurrency**: two PRs reviewed in parallel will each
  spawn their own autopilot run. The concurrency group is per-PR.

## Manually triggering a run

Useful when CR / Devin missed a comment or the workflow file was edited:

```
gh workflow run "PR Autopilot" -f pr_number=42
```

## Operational tips

- Watch the first 2–3 autopilot runs closely. If it tries to fix
  something it shouldn't, mark the PR draft to pause; once paused, edit
  the relevant thread to add `[autopilot: skip]` and the next run will
  treat it as acknowledged.
- The autopilot logs its full classification reason every step. If a
  run no-ops unexpectedly, look at the `triage` job's stdout — it
  prints `should_run / mode / reason` together.

## Future work

- **Self-healing**: when the autopilot's own push fails CI (e.g. ruff /
  mypy), have it pull the failure log and dispatch another fix round
  on its own commit.
- **Cost cap**: a per-PR token budget that exits the workflow with a
  clear "budget exhausted, awaiting human" message.
- **Provider plurality**: drop in MiniMax / GLM-5.1 alongside Claude as
  alternative fix-providers (per the SKILL.md §1.0a routing logic for
  whole-figure SVG generation; the autopilot's fix pattern is
  similarly LLM-writes-code-directly).
