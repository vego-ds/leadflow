/**
 * LeadFlow - Google Apps Script trigger.
 *
 * Bind this to the Leads sheet. It fires the backend webhook the instant a new
 * lead arrives (form submit or row edit), giving real-time ingestion without
 * any message queue.
 *
 * HMAC signing:
 *   Each request is signed with HMAC-SHA256 over "{timestamp}.{body}" using
 *   the WEBHOOK_SIGNING_SECRET stored in Script Properties (never hardcoded).
 *   The backend verifies this via app/security/webhook_auth.py.
 *
 * Setup:
 *   1. Extensions -> Apps Script, paste this in.
 *   2. Set BACKEND_URL below.
 *   3. In Project Settings -> Script Properties, add:
 *        WEBHOOK_SIGNING_SECRET = <your secret>
 *   4. Add an installable trigger: onFormSubmit (and/or onEdit) -> notifyBackend.
 */

const BACKEND_URL = "https://your-backend.example.com/webhook/new-lead";

function notifyBackend(e) {
  const sheet = e.range ? e.range.getSheet() : e.source.getActiveSheet();
  if (sheet.getName() !== "Leads") return;

  const row = e.range ? e.range.getRow() : sheet.getLastRow();
  const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  const values = sheet.getRange(row, 1, 1, sheet.getLastColumn()).getValues()[0];

  const payload = {};
  headers.forEach((h, i) => { payload[h] = values[i]; });

  const body = JSON.stringify(payload);
  const timestampSeconds = Math.floor(Date.now() / 1000).toString();

  // Read signing secret from Script Properties — never hardcode it.
  const secret = PropertiesService.getScriptProperties().getProperty("WEBHOOK_SIGNING_SECRET");
  if (!secret) {
    throw new Error("WEBHOOK_SIGNING_SECRET not set in Script Properties");
  }

  const signature = _hmacSha256Hex(secret, timestampSeconds + "." + body);

  UrlFetchApp.fetch(BACKEND_URL, {
    method: "post",
    contentType: "application/json",
    payload: body,
    headers: {
      "X-LeadFlow-Signature": signature,
      "X-LeadFlow-Timestamp": timestampSeconds,
    },
    muteHttpExceptions: true,
  });
}

/**
 * Compute HMAC-SHA256 of message with key, returning lowercase hex.
 * Uses Google Apps Script's built-in Utilities.computeHmacSha256Signature.
 */
function _hmacSha256Hex(key, message) {
  const keyBytes = Utilities.newBlob(key).getBytes();
  const msgBytes = Utilities.newBlob(message).getBytes();
  const sigBytes = Utilities.computeHmacSha256Signature(msgBytes, keyBytes);
  return sigBytes.map(b => ("0" + (b & 0xff).toString(16)).slice(-2)).join("");
}
