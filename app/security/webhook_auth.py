"""
HMAC-SHA256 webhook signature verification.

Both /webhook/new-lead (signed by Google Apps Script) and /webhook/call-result
(signed by Bolna, once their signing scheme is confirmed) use this module.

Signing protocol:
  message = f"{timestamp}.{raw_body}"   # timestamp is Unix seconds (str)
  signature = HMAC-SHA256(secret, message).hexdigest()

Clients send:
  X-LeadFlow-Timestamp: <unix_epoch_seconds>
  X-LeadFlow-Signature: <hex_digest>

Security properties:
  - Constant-time compare (hmac.compare_digest) prevents timing attacks.
  - Timestamp replay window (default 5 min) prevents replayed valid webhooks.

TODO (Bolna): Confirm Bolna's outbound signing scheme against their docs
  (see bolna_agent/agent_config.json for agent configuration). Bolna may use
  a different header name or signing format — adjust verify_signature() or
  add a bolna-specific verifier once confirmed. The dependency
  require_signed_webhook is already wired to /webhook/call-result; swap
  BOLNA_WEBHOOK_SECRET in settings when ready.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Callable

from fastapi import Depends, Header, HTTPException, Request


def verify_signature(
    payload_bytes: bytes,
    signature_header: str,
    timestamp_header: str,
    secret: str,
    max_age_seconds: int = 300,
) -> None:
    """Verify HMAC-SHA256 signature and timestamp freshness.

    Raises HTTPException 401 on failure. Never raises on success.

    Args:
        payload_bytes:    Raw request body bytes.
        signature_header: Value of X-LeadFlow-Signature header.
        timestamp_header: Value of X-LeadFlow-Timestamp header (Unix epoch str).
        secret:           HMAC secret key (from settings).
        max_age_seconds:  Maximum age of the timestamp before we reject (default 5 min).
    """
    # 1. Reject if no secret is configured (misconfiguration safeguard).
    if not secret:
        raise HTTPException(status_code=401, detail="Webhook signing not configured")

    # 2. Parse and validate timestamp.
    try:
        ts = int(timestamp_header)
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid timestamp")

    age = int(time.time()) - ts
    if abs(age) > max_age_seconds:
        raise HTTPException(
            status_code=401,
            detail=f"Timestamp out of window (age={age}s, max={max_age_seconds}s)",
        )

    # 3. Compute expected signature.
    message = f"{timestamp_header}.".encode() + payload_bytes
    expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()

    # 4. Constant-time compare.
    if not hmac.compare_digest(expected, signature_header):
        raise HTTPException(status_code=401, detail="Invalid signature")


def require_signed_webhook(secret: str) -> Callable:
    """Return a FastAPI dependency that verifies HMAC signature on every request.

    Usage:
        @app.post("/webhook/new-lead", dependencies=[Depends(require_signed_webhook(settings.webhook_signing_secret))])

    The dependency reads the raw request body and both HMAC headers, then
    delegates to verify_signature(). It raises 401 on any failure.
    """

    async def _dependency(
        request: Request,
        x_leadflow_signature: str = Header(..., alias="X-LeadFlow-Signature"),
        x_leadflow_timestamp: str = Header(..., alias="X-LeadFlow-Timestamp"),
    ) -> None:
        body = await request.body()
        verify_signature(
            payload_bytes=body,
            signature_header=x_leadflow_signature,
            timestamp_header=x_leadflow_timestamp,
            secret=secret,
        )

    return _dependency
