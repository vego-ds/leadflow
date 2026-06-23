---
name: secrets-discipline
description: Rules for handling secrets, credentials, and sensitive data in LeadFlow. Use whenever touching API keys, tokens, credentials, .env files, .gitignore, configuration, or any third-party integration (Bolna, Google Sheets, payment providers, email/WhatsApp providers). Use whenever the user mentions security, secrets, leaks, or accidentally committing sensitive data.
---

# Secrets Discipline — LeadFlow

Never let a secret reach the repo. Treat every key as live.

## The non-negotiables
- **No real keys, tokens, passwords, or credentials in code, config files, comments, tests, fixtures, or commit messages — ever.**
- **No real keys in `.env.example`.** That file is committed; it shows the *shape* of `.env`, never its contents.
- **All secrets live in `.env`**, which is gitignored. The app loads them via environment variables.
- **Before adding any new file type to the repo, check `.gitignore` first.** If the new file might ever contain a credential, add it to `.gitignore` before creating it.

## What counts as a secret
- API keys (Bolna, OpenAI, Twilio, Razorpay, Stripe, SendGrid, etc.)
- OAuth client secrets, refresh tokens, access tokens
- Service account JSON files (Google Cloud, AWS IAM)
- Webhook signing secrets
- Database connection strings with passwords
- Phone numbers, email addresses, or names of real leads (even in test data — use synthetic only)
- Internal company URLs, hostnames, or identifiers

If unsure whether something is sensitive: assume yes and ask.

## Where things go
| Item | Location |
|---|---|
| Real keys/secrets | `.env` (gitignored) |
| Placeholder/shape of `.env` | `.env.example` (committed) |
| Google service account JSON | `credentials.json` (gitignored) |
| Reference to a key in code | `os.environ["BOLNA_API_KEY"]` — read at runtime |
| Synthetic test data | `data/sample_leads.csv` — fake names/phones only |

## Before every commit
Run these mental checks (and actually `git diff --staged` to verify):
1. Any string that looks like `sk-...`, `AKIA...`, a long hex/base64 blob, or a UUID-like token? → stop.
2. Any real phone number, email, or person's name from your company's actual leads? → stop, replace with synthetic.
3. Any new file extension (`.json`, `.pem`, `.key`, `.p12`) that could carry secrets? → confirm it's in `.gitignore`.
4. Any URL pointing to an internal/private system? → replace with `example.com` placeholder.

## When adding a new integration
Order of operations:
1. Add the variable name to `.env.example` with an empty value and a one-line comment.
2. Add the file/folder pattern to `.gitignore` if the integration uses credential files (like Google's `credentials.json`).
3. Read the secret in code via `os.environ[...]` or `os.getenv(...)`. Never default to a real value.
4. Put your real value only in `.env` (which is gitignored).
5. Confirm `git status` does not show `.env` or the credential file.

## If a secret leaks
Tell the user immediately. Don't try to "fix it quietly" with another commit — the secret is already in git history. Steps:
1. **Rotate the leaked credential at the provider right now** (revoke the key, generate a new one).
2. Remove it from the working tree.
3. Tell the user the full extent (which file, which commit, whether already pushed).
4. Let the user decide whether to rewrite history (`git filter-repo` / BFG) or leave the rotated key as-is.

A rotated key in old history is harmless. An unrotated key removed from history is not — the rotation matters more than the rewrite.

## What not to do
- Don't put real keys "just temporarily" in code to test something. Use `.env` from the start.
- Don't paste secrets into chat, logs, or error messages.
- Don't write secrets into Sheet rows or Event log entries.
- Don't commit a credential file with `git add -f` to bypass `.gitignore`. Ever.
- Don't echo secrets in shell commands that get logged (`echo $BOLNA_API_KEY` in a script that pipes to a log).
