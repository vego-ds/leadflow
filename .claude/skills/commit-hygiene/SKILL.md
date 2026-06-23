---
name: commit-hygiene
description: Git commit standards for LeadFlow. Use whenever staging, committing, or pushing changes. Use whenever the user mentions commits, git, pushing to GitHub, commit messages, or wants to save progress.
---

# Commit Hygiene — LeadFlow

How commits are shaped, named, and when to make them.

## One commit = one logical change
A commit answers a single "what did this change?" question. If the answer needs the word "and," split it.

Good:
- "Add ingest validation for phone format"
- "Wire scoring stage into demo runner"

Bad:
- "Update ingest and add tests and fix typo"
- "wip"
- "stuff"

## Commit message format
```
<type>: <short summary in imperative mood>

<optional body explaining why, not what>
```

Subject line: ≤ 60 chars, imperative ("Add", "Fix", "Refactor" — not "Added", "Fixes"), no trailing period.

Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`.

Examples:
- `feat: add stale-lead recovery sweep`
- `fix: prevent double-conversion on duplicate payment webhook`
- `refactor: extract counselor selection into its own function`
- `test: cover voicemail outcome in scoring`
- `docs: explain mock-to-live adapter swap in README`
- `chore: bump fastapi to 0.115`

## Body (when needed)
Use the body for *why*, not *what*. The diff already shows what.

```
fix: prevent double-conversion on duplicate payment webhook

Razorpay can retry webhooks. Without an idempotency check we were
marking the lead Converted twice and logging two payment_received
events.
```

## When to commit
Commit at every meaningful checkpoint: a stage works, a test passes, a refactor compiles. Small commits are easier to review, revert, and explain in interviews.

Don't commit:
- Broken code (tests failing, demo crashing).
- WIP scratchpads.
- Debug `print()` statements left behind.
- Anything in `.env`, `credentials.json`, `.venv/`, `__pycache__/` (the `.gitignore` covers these — but verify with `git status` before committing anyway).

## When to ASK before committing
Pause and confirm with the user before:
- Force-pushing or rewriting history (`git push --force`, `git reset --hard`, `git rebase` on shared branches).
- Committing changes that span more than ~5 files unrelated to the stated task.
- Adding a new dependency to `requirements.txt`.
- Touching `CLAUDE.md`, `.gitignore`, or anything under `.claude/skills/`.
- Anything that looks like a secret, key, token, or credential — even if it's "obviously" a placeholder.

## Verify before pushing
```bash
git status                   # nothing unexpected staged
git diff --staged            # eyeball the diff
git log --oneline -5         # last 5 commits look clean
```

## What not to do
- No `git add .` without checking `git status` first.
- No commits with messages like "fix", "update", "changes", "wip", "asdf".
- No bundling unrelated changes ("while I was in there..." gets its own commit).
- No force-pushing without explicit user approval.
