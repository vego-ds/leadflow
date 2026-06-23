---
name: python-style
description: Python coding conventions for LeadFlow. Apply whenever writing or modifying Python code in this project.
---

# Python Style — LeadFlow

Rules to follow when writing or editing Python in this repo. Keep code small, readable, and honest about what it does.

## Type hints
- All function parameters and return values must have type hints.
- Use built-in generics (`list[str]`, `dict[str, int]`), not `List`/`Dict` from `typing`.
- Use `Optional[X]` or `X | None` for nullable values — be explicit, don't silently default to `None`.
- For complex types, define a `dataclass` rather than nested dict/tuple annotations.

## Dataclasses over dicts
- Anything with a defined shape (Lead, CallOutcome, config payloads) is a `@dataclass`, not a dict.
- Use `dict` only for: external I/O (JSON in/out, Sheet rows), short-lived lookups, when the shape is genuinely dynamic.
- If you find yourself accessing `d["key"]` in more than one place, it should probably be a dataclass.

## Docstrings
- Write a docstring only when the function's purpose isn't obvious from its name and signature.
- One-line docstrings for simple functions. Multi-line only when behavior, side effects, or edge cases need explaining.
- No docstring boilerplate (no `Args:` / `Returns:` blocks unless they actually clarify something the signature doesn't).
- Module-level docstrings are encouraged when the file's role isn't self-evident.

## No print statements
- Never use `print()` in `app/` or `scripts/` for logging.
- Use `app.utils.logging.log()` instead.
- `print()` is acceptable only in the `_summary()`-style human-readable output at the end of a script.

## No premature abstraction
- Don't add a base class, interface, or config layer until there's a second concrete use case for it.
- Adapter interfaces (Dialer, Messenger, SheetStore) already exist — extend those rather than inventing new abstraction layers.
- One implementation = no interface. Two implementations = consider an interface. Three = definitely.
- No factory functions, no plugin systems, no dependency injection frameworks. Pass objects in directly.

## Imports
- Standard library, third-party, local — three groups, separated by a blank line.
- Use absolute imports (`from app.models import Lead`), not relative.
- `from __future__ import annotations` at the top of every file that uses type hints.

## Errors and validation
- Validate at boundaries (incoming webhook data, Sheet rows). Once inside the pipeline, trust your own types.
- Raise specific exceptions, not bare `Exception`.
- For expected failures (bad input, unreachable lead), return a result/None and log — don't raise.

## Function size
- If a function is longer than ~30 lines, look for one thing to extract.
- If it has more than 4 parameters, consider a dataclass.
- Pure functions (no side effects) preferred where possible; side-effecting functions should make their effect obvious from the name (`update_lead`, `log_event`, `send_email`).

## What not to do
- No `*args`/`**kwargs` unless genuinely needed (decorators, pass-through wrappers).
- No mutable default arguments (`def f(x=[])`).
- No global state except where unavoidable (the `_processed_payments` set in `conversion.py` is one such case — comment it).
- No clever one-liners that need explaining. Two clear lines beat one clever line.
