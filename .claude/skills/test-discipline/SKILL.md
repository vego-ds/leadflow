---
name: test-discipline
description: Testing rules for LeadFlow. Use whenever writing new pipeline stages, adapters, or any code that handles idempotency, validation, or state transitions. Use whenever the user mentions tests, test coverage, pytest, or a new feature that should be tested.
---

# Test Discipline — LeadFlow

When tests get written, what they cover, and how they're named.

## When to write a test
Write a test in the same change as the code, not later, for any of these:
- A new pipeline stage (ingest / outreach / scoring / booking / conversion / future).
- A new adapter implementation (mock or real).
- Any function that validates external input.
- Any function with idempotency guarantees (payment, deduplication, retry logic).
- Any state transition on `Lead` (status changes).
- A bug fix — write the test that would have caught it, then fix the code.

Don't write tests for:
- Plain data classes with no logic.
- Trivial pass-through functions.
- Logging utilities.

## What to cover per area
**Pipeline stages:** happy path + at least one failure path (invalid input, missing dependency, downstream error).

**Adapters:** the contract holds — return type matches the interface, mock returns plausible values, real-stub raises `NotImplementedError` cleanly.

**Validation/ingest:** every quarantine branch (missing name, bad phone, unknown language) gets its own test.

**Idempotency:** call the operation twice with the same key and assert it ran once. This is non-negotiable for payment and any future webhook handler.

**Scoring:** at least one test per signal weight (answered call boosts score, missing email reduces it, etc.) — so a future tweak that breaks a weight is caught.

## Naming
Test functions: `test_<subject>_<behavior>`. Examples:
- `test_valid_lead_is_accepted`
- `test_bad_phone_is_quarantined`
- `test_payment_is_idempotent`
- `test_answered_call_scores_higher`

The name should read like a sentence about what the system does, not what the test does. Bad: `test_ingest_1`. Good: `test_unknown_language_is_quarantined`.

## Structure
- One assertion focus per test. Multiple `assert` lines fine if they verify the same behavior.
- Arrange / Act / Assert — clearly separated, even without comments.
- Reuse fixtures via small helper functions (`_good_row()` in `test_pipeline.py`). No pytest fixtures yet — overkill for this size.
- Tests use `InMemorySheet` and mock adapters. No network, no filesystem, no time-sensitive sleeps.

## Running
```bash
pytest -q
```
All tests must pass before committing. No skipped tests without an inline comment explaining why and a tracking note.

## What not to do
- No tests that exist only to inflate coverage.
- No `time.sleep()` in tests.
- No tests that depend on order of execution.
- No mocking of code you wrote — mock only the boundary (the adapter), trust your own pipeline functions.
- No real API calls in tests, ever. If a test needs a real provider, it's an integration test and lives outside `tests/` with a clear opt-in flag.
