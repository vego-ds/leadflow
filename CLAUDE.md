# CLAUDE.md — LeadFlow

Context for Claude Code. Read this file before working on the project structure.

## 1. System Overview & Architecture
**LeadFlow** is an EdTech lead-to-conversion pipeline that closes the gap between lead intent and lead response instantly using plain Python and FastAPI with no database.

- **System of Record:** Google Sheets with 4 tabs: `Leads`, `Needs Review`, `Counselors`, and `Events`.
- **Lightweight Infrastructure:** Deliberately uses no heavy machinery (No Redis, Celery, or worker queues). It relies completely on Python's built-in `BackgroundTasks` for concurrent processing.
- **Pipeline Funnel:** Lead In → Voice Call + Email + WhatsApp (dispatched concurrently within seconds) → Screening Outcome → Score Matrix → Counselor Auto-assigned → Human Zone Interaction → Payment Clearance → Converted status.

## 2. Adapter Discipline (Service Swapping)
Every single external service must hide behind an abstract class interface inside `app/adapters/base.py`. The core pipeline logic must never import vendor SDKs directly.

- **The Pattern:** For every integration, you must provide:
  1. An **Interface** detailing the baseline signature contract.
  2. A **Mock Implementation** that runs offline with zero credentials and outputs plausible synthetic data for local pipeline testing.
  3. A **Real Stub Implementation** containing third-party code packages (Twilio, Bolna, Razorpay, Stripe, gspread, SendGrid) that raises a `NotImplementedError` outlining where live wiring lands.
- **Constructor Discipline:** Mock classes accept no parameters. Real stubs accept setup keys directly via arguments—never read environment configurations inside the adapter class itself.

## 3. Testing Discipline
- **The Core Rule:** Always write a clean test file using mock adapters (no real network or database dependencies) whenever adding or altering a pipeline feature, and ensure all tests pass with `pytest` before saving.

## 4. Secrets Discipline (Security Checklist)
- **Never commit a secret:** No real developer tokens, auth signatures, passwords, or personal test numbers belong in your code, comments, or repository files.
- **The Dotenv Pattern:** All configuration constants must reside strictly in an uncommitted `.env` file loaded via environment boundaries.
- **Blueprint Updates:** If you introduce an integration dependency, add the blank placeholder field layout to `.env.example` along with a descriptive one-line explanation comment.

## 5. Project Layout
```text
app/models.py          Lead schema + status lifecycle
app/main.py            FastAPI webhooks
app/pipeline/          ingest · outreach · scoring · booking · conversion
app/adapters/          mock + real-stub integrations
bolna_agent/           multilingual voice agent config
apps_script/           Sheets real-time trigger
scripts/run_demo.py    end-to-end demo runner
tests/                 validation, scoring, idempotency

## 6. Terminal Quick Commands
```bash
python -m scripts.run_demo     # Run the pipeline end-to-end demo
pytest                         # Run your tests
uvicorn app.main:app --reload  # Start local web engine mode