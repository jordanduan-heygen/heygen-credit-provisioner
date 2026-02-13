// =============================================================================
// HeyGen Credit Provisioner — Hackathon Edition (Polling Mode)
// =============================================================================
//
// SETUP:
//   1. Open your existing linked Google Sheet → Extensions → Apps Script
//   2. Delete everything in Code.gs and paste this entire file
//   3. In the editor, select "setup" from the function dropdown → click Run
//   4. Deploy → New deployment → Web app
//        Execute as: Me
//        Who has access: Anyone
//   5. Copy the web app URL and put it in config.json on the EC2 server
//   6. Done! The EC2 server polls this web app for new submissions.
//
// NO Script Properties needed — everything is self-contained.
//
// Sheet columns:
//   A=Timestamp  B=Name  C=Email  D=Team Name  E=Project Idea
//   F=SignedUp1  G=SignedUp2  H=LinkedIn  I=Resume  J=(unused)
//   K=Status (auto)  L=Count (auto)
//
// =============================================================================


// ---------------------------------------------------------------------------
// CONFIG — Constants
// ---------------------------------------------------------------------------

var COL_TIMESTAMP  = 1;   // A
var COL_NAME       = 2;   // B
var COL_EMAIL      = 3;   // C — Email
var COL_TEAM       = 4;   // D — Team Name
var COL_IDEA       = 5;   // E — Project Idea
var COL_SIGNEDUP1  = 6;   // F — Signed up (HeyGen)
var COL_SIGNEDUP2  = 7;   // G — Signed up (LiveAvatar)
var COL_LINKEDIN   = 8;   // H — LinkedIn (optional)
var COL_RESUME     = 9;   // I — Resume (optional) — DO NOT OVERWRITE
// Column J (10) is unused / legacy
var COL_STATUS     = 11;  // K — auto-filled by this script
var COL_COUNT      = 12;  // L — auto-filled by this script

var MAX_SUBMISSIONS_PER_EMAIL = 3;


// ---------------------------------------------------------------------------
// RATE LIMIT — Tracks per-email usage (case-insensitive)
// ---------------------------------------------------------------------------

function countPreviousSubmissions(sheet, email) {
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return 0;

  var emailRange  = sheet.getRange(2, COL_EMAIL, lastRow - 1, 1).getValues();
  var statusRange = sheet.getRange(2, COL_STATUS, lastRow - 1, 1).getValues();
  var normalizedEmail = email.toLowerCase().trim();
  var count = 0;

  for (var i = 0; i < emailRange.length; i++) {
    var rowEmail  = String(emailRange[i][0]).toLowerCase().trim();
    var rowStatus = String(statusRange[i][0]).toUpperCase().trim();
    if (rowEmail === normalizedEmail && rowStatus === 'SUCCESS') {
      count++;
    }
  }

  return count;
}


// ---------------------------------------------------------------------------
// WEB APP — doGet returns pending rows, doPost updates results
// ---------------------------------------------------------------------------

/**
 * GET handler — returns rows where Status (col I) is empty.
 * The EC2 server polls this endpoint.
 */
function doGet(e) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
  var lastRow = sheet.getLastRow();

  if (lastRow < 2) {
    return ContentService.createTextOutput(
      JSON.stringify({ status: 'ok', rows: [] })
    ).setMimeType(ContentService.MimeType.JSON);
  }

  var data = sheet.getRange(2, 1, lastRow - 1, COL_COUNT).getValues();
  var pendingRows = [];

  for (var i = 0; i < data.length; i++) {
    var rowNum = i + 2;
    var email = String(data[i][COL_EMAIL - 1]).trim();
    var status = String(data[i][COL_STATUS - 1]).trim();

    // Skip rows that already have a status
    if (status && status !== 'undefined') continue;

    // Skip rows with no email
    if (!email || email === 'undefined') continue;

    // Check rate limit
    var prevCount = countPreviousSubmissions(sheet, email);
    if (prevCount >= MAX_SUBMISSIONS_PER_EMAIL) {
      // Auto-mark as rate limited
      sheet.getRange(rowNum, COL_STATUS).setValue('RATE_LIMITED');
      sheet.getRange(rowNum, COL_COUNT).setValue(prevCount);
      continue;
    }

    // Mark as PENDING so it doesn't get picked up again
    sheet.getRange(rowNum, COL_STATUS).setValue('PENDING');

    pendingRows.push({
      row: rowNum,
      email: email,
      count: prevCount
    });
  }

  return ContentService.createTextOutput(
    JSON.stringify({ status: 'ok', rows: pendingRows })
  ).setMimeType(ContentService.MimeType.JSON);
}

/**
 * POST handler — receives results from the EC2 server.
 * Body: { "row": 5, "status": "SUCCESS", "count": 1 }
 */
function doPost(e) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];

  var body;
  try {
    body = JSON.parse(e.postData.contents);
  } catch (err) {
    return ContentService.createTextOutput(
      JSON.stringify({ status: 'error', message: 'invalid JSON' })
    ).setMimeType(ContentService.MimeType.JSON);
  }

  var rowNum = body.row;
  var status = body.status;
  var count  = body.count;

  if (!rowNum || !status) {
    return ContentService.createTextOutput(
      JSON.stringify({ status: 'error', message: 'missing row or status' })
    ).setMimeType(ContentService.MimeType.JSON);
  }

  sheet.getRange(rowNum, COL_STATUS).setValue(status);
  sheet.getRange(rowNum, COL_COUNT).setValue(count);

  return ContentService.createTextOutput(
    JSON.stringify({ status: 'ok' })
  ).setMimeType(ContentService.MimeType.JSON);
}


// ---------------------------------------------------------------------------
// SETUP — One-time configuration
// ---------------------------------------------------------------------------

/**
 * One-time setup: adds tracking column headers.
 * Run this from the editor, then deploy as web app.
 */
function setup() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];

  // Add headers for tracking columns (I and J)
  if (!sheet.getRange(1, COL_STATUS).getValue()) sheet.getRange(1, COL_STATUS).setValue('Status');
  if (!sheet.getRange(1, COL_COUNT).getValue())  sheet.getRange(1, COL_COUNT).setValue('Count');

  // Remove any existing onFormSubmit triggers (no longer needed)
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() === 'onFormSubmit') {
      ScriptApp.deleteTrigger(triggers[i]);
    }
  }

  SpreadsheetApp.getUi().alert(
    'Setup complete!\n\n' +
    '1. Columns I (Status) and J (Count) are ready.\n' +
    '2. Old form triggers have been removed.\n\n' +
    'Next: Deploy → New deployment → Web app\n' +
    '  Execute as: Me\n' +
    '  Who has access: Anyone\n\n' +
    'Copy the web app URL into config.json on your EC2 server.'
  );
}

/**
 * Helper to write status and count to the tracking columns.
 */
function writeStatus(sheet, row, status, count) {
  sheet.getRange(row, COL_STATUS).setValue(status);
  sheet.getRange(row, COL_COUNT).setValue(count);
}
