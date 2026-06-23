---
name: adapter-discipline
description: LeadFlow's rule that every external service goes behind an adapter interface. Use whenever integrating a new external service (voice, SMS, email, payment, CRM, storage, sheets, queue, etc.), modifying an existing adapter, or writing pipeline code that touches anything outside the process. Use whenever the user mentions Bolna, Twilio, Razorpay, Stripe, gspread, Google Sheets, WhatsApp, SendGrid, or any third-party provider.
---

# Adapter Discipline — LeadFlow

Every external service is hidden behind an interface. The pipeline never calls a provider SDK directly.

## The rule
Pipeline code (`app/pipeline/*`) depends only on the abstract interfaces in `app/adapters/base.py` (and the sheet store in `app/adapters/sheets.py`). It never imports `twilio`, `gspread`, `razorpay`, `requests`, or any other provider SDK.

If a pipeline function needs to do something external, it asks an adapter to do it.

## The pattern (always)
For every new external service, three things land in the same commit:

1. **Interface** in `app/adapters/base.py` (or a new file if it warrants one).
2. **Mock implementation** that the demo uses — runs without credentials, returns plausible data.
3. **Real-stub implementation** with the SDK import and `raise NotImplementedError(...)` showing exactly where to wire it up.

Existing examples (study these before adding a new one):
- `Dialer` — `MockDialer` + `BolnaDialer` stub
- `Messenger` — `MockEmail`, `MockWhatsApp` (real stubs come when needed)
- `SheetStore` — `InMemorySheet` + `GoogleSheet` stub

## Why
- **Demo always runs.** Anyone clones the repo, runs `python -m scripts.run_demo`, sees it work. No accounts, no keys, no cost.
- **Going live is a swap, not a rewrite.** `MockDialer()` becomes `BolnaDialer(api_key=..., agent_id=...)`. Pipeline code does not change.
- **Tests stay fast and offline.** Tests use mocks; integration tests (if any) live outside `tests/` with explicit opt-in.

## When adding a new external service
Order of operations:
1. **Define the interface first.** What is the *minimum* method signature the pipeline needs? (e.g. `def send(to, language, attachments) -> bool`). Don't model the provider's full API — model what the pipeline needs.
2. **Write the mock.** It should return realistic-looking data, with believable randomness if relevant (success/failure mix, plausible delay if it matters for demo feel).
3. **Write the real stub.** Import the SDK, accept credentials in the constructor, raise `NotImplementedError` with a clear message ("Wire up X here") in every method.
4. **Wire it through.** The pipeline function takes the interface, not the implementation. The demo runner / `main.py` injects the mock.
5. **Update `.env.example`** with the credential variable names (empty values).
6. **Document the swap** in the README if it's a major surface.

## Constructor shape
- Mocks take no required arguments (or trivial ones like `simulate_delay`).
- Real stubs take credentials and identifiers as constructor args (`api_key`, `agent_id`, `spreadsheet_id`). Never read env vars inside the adapter — the wiring layer reads env and passes them in. Keeps the adapter testable.

## What not to do
- Don't put provider SDK imports in `app/pipeline/*`. Ever.
- Don't add a real provider call to the mock for "convenience" (e.g. mock dialer actually placing a call). Mocks stay offline.
- Don't model the provider's full API in the interface. Pipeline-facing interfaces stay minimal — one to three methods. Add methods only when a pipeline stage needs them.
- Don't skip the real stub because "I'll add it later." The stub is the contract — it documents the swap point and proves the interface is implementable for real.
- Don't put credentials in the interface or in the mock's constructor. Credentials belong only on the real implementation.
- Don't create an interface for something with one implementation that will never have another. (Reserved for genuine external boundaries.)

## Self-check before merging an external integration
- [ ] Pipeline code does not import the provider SDK
- [ ] Mock runs offline, no credentials needed
- [ ] Real stub exists with `NotImplementedError` and clear "wire up here" comment
- [ ] `.env.example` updated, real values only in `.env`
- [ ] Tests cover the mock implementation
- [ ] Demo runner still works end to end
