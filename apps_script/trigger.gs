/**
 * LeadFlow - Google Apps Script trigger.
 *
 * Bind this to the Leads sheet. It fires the backend webhook the instant a new
 * lead arrives (form submit or row edit), giving real-time ingestion without
 * any message queue.
 *
 * Setup:
 *   1. Extensions -> Apps Script, paste this in.
 *   2. Set BACKEND_URL below.
 *   3. Add an installable trigger: onFormSubmit (and/or onEdit) -> notifyBackend.
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

  UrlFetchApp.fetch(BACKEND_URL, {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify(payload),
    muteHttpExceptions: true,
  });
}
