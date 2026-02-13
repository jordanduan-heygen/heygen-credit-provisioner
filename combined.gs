// =============================================================================
// HeyGen Credit Provisioner — Hackathon Edition
// =============================================================================
//
// SETUP:
//   1. Open your existing linked Google Sheet → Extensions → Apps Script
//   2. Delete everything in Code.gs and paste this entire file
//   3. Go to Project Settings → Script Properties and add:
//        SERVER_URL  = http://3.144.17.55:5000
//   4. In the editor, select "setup" from the function dropdown → click Run
//   5. Approve the authorization prompt
//   6. Done! Submit the form to test.
//
// Sheet columns:
//   A=Timestamp  B=Name  C=Email  D=LinkedIn  E=Team  F=Idea  G=SignedUp  H=Actioned
//   I=Status (auto)  J=Count (auto)
//
// =============================================================================


// ---------------------------------------------------------------------------
// CONFIG — Constants and secrets
// ---------------------------------------------------------------------------

// Column indices matching your actual sheet (1-based)
var COL_TIMESTAMP = 1;  // A
var COL_NAME      = 2;  // B
var COL_EMAIL     = 3;  // C — Email
var COL_LINKEDIN  = 4;  // D
var COL_TEAM      = 5;  // E
var COL_IDEA      = 6;  // F
var COL_SIGNEDUP  = 7;  // G — "I have signed up for..."
var COL_ACTIONED  = 8;  // H — manual "Actioned? Y/N"
var COL_STATUS    = 9;  // I — auto-filled by this script
var COL_COUNT     = 10; // J — auto-filled by this script

var MAX_SUBMISSIONS_PER_EMAIL = 3;

/**
 * Returns the required secrets from Script Properties.
 */
function getConfig() {
  var props = PropertiesService.getScriptProperties();
  var serverUrl = props.getProperty('SERVER_URL');

  if (!serverUrl) {
    throw new Error(
      'Missing Script Properties. Ensure SERVER_URL is set in ' +
      'Project Settings → Script Properties. Example: http://3.144.17.55:5000'
    );
  }

  return {
    serverUrl: serverUrl
  };
}


// ---------------------------------------------------------------------------
// RATE LIMIT — Tracks per-email usage (case-insensitive)
// ---------------------------------------------------------------------------

/**
 * Counts rows where the email matches AND status is SUCCESS.
 */
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

/**
 * Checks whether the email is allowed another submission.
 */
function checkRateLimit(sheet, email) {
  var currentCount = countPreviousSubmissions(sheet, email);
  return {
    allowed: currentCount < MAX_SUBMISSIONS_PER_EMAIL,
    currentCount: currentCount
  };
}


// ---------------------------------------------------------------------------
// SLACK SERVICE — Posts via EC2 Playwright automation server
// ---------------------------------------------------------------------------

/**
 * Sends the HeyGen command by calling the EC2 Playwright server.
 * The server types the message in Slack as a real user, which triggers
 * the HeyGen Bot to respond.
 */
function postSlackMessage(email) {
  var config = getConfig();
  var url = config.serverUrl + '/send';

  var payload = {
    email: email,
    api_sub: 'True',
    api_quota: '1000',
    days: '3'
  };

  var options = {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  };

  try {
    var response = UrlFetchApp.fetch(url, options);
    var body = JSON.parse(response.getContentText());

    if (!body.ok) {
      Logger.log('Server error: ' + body.error);
    }

    return { ok: body.ok, error: body.error };
  } catch (err) {
    Logger.log('Server request failed: ' + err.message);
    return { ok: false, error: err.message };
  }
}


// ---------------------------------------------------------------------------
// MAIN — Trigger, setup, retry, helpers
// ---------------------------------------------------------------------------

/**
 * Installable trigger handler for form submissions.
 * Uses LockService to serialize concurrent submissions and prevent race conditions.
 */
function onFormSubmit(e) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
  var row = e.range.getRow();

  // Always read email directly from column C — most reliable method
  var email = String(sheet.getRange(row, COL_EMAIL).getValue()).trim();
  Logger.log('Row ' + row + ' — email from sheet: "' + email + '"');

  if (!email) {
    writeStatus(sheet, row, 'ERROR: empty email', 0);
    return;
  }

  // Acquire a script-level lock to serialize rate-limit checks
  var lock = LockService.getScriptLock();
  try {
    lock.waitLock(30000);
  } catch (err) {
    writeStatus(sheet, row, 'ERROR: lock timeout', 0);
    return;
  }

  try {
    var rateCheck = checkRateLimit(sheet, email);
    if (!rateCheck.allowed) {
      writeStatus(sheet, row, 'RATE_LIMITED', rateCheck.currentCount);
      return;
    }

    Logger.log('Sending to automation server for: ' + email);
    var result = postSlackMessage(email);
    Logger.log('Server response: ' + JSON.stringify(result));

    if (result.ok) {
      writeStatus(sheet, row, 'SUCCESS', rateCheck.currentCount + 1);
    } else {
      writeStatus(sheet, row, 'ERROR: ' + (result.error || 'unknown'), rateCheck.currentCount);
    }
  } catch (err) {
    Logger.log('Exception: ' + err.message);
    writeStatus(sheet, row, 'ERROR: ' + err.message, 0);
  } finally {
    lock.releaseLock();
  }
}

/**
 * One-time setup: creates the installable trigger and adds tracking column headers.
 * Run this from the editor after setting Script Properties.
 */
function setup() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];

  // Add headers for tracking columns (I and J)
  if (!sheet.getRange(1, COL_STATUS).getValue()) sheet.getRange(1, COL_STATUS).setValue('Status');
  if (!sheet.getRange(1, COL_COUNT).getValue())  sheet.getRange(1, COL_COUNT).setValue('Count');

  // Remove existing onFormSubmit triggers to avoid duplicates
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() === 'onFormSubmit') {
      ScriptApp.deleteTrigger(triggers[i]);
    }
  }

  // Create the installable trigger
  ScriptApp.newTrigger('onFormSubmit')
    .forSpreadsheet(SpreadsheetApp.getActiveSpreadsheet())
    .onFormSubmit()
    .create();

  SpreadsheetApp.getUi().alert(
    'Setup complete!\n\n' +
    '1. Trigger "onFormSubmit" has been created.\n' +
    '2. Columns I (Status) and J (Count) are ready.\n\n' +
    'Make sure you have set SERVER_URL in Project Settings → Script Properties.\n' +
    'Example: http://3.144.17.55:5000'
  );
}

/**
 * Manual retry for a row that previously failed.
 * Run from editor: select retryRow → Run → enter row number when prompted.
 */
function retryRow(rowNumber) {
  if (!rowNumber) {
    var ui = SpreadsheetApp.getUi();
    var response = ui.prompt('Retry Row', 'Enter the row number to retry:', ui.ButtonSet.OK_CANCEL);
    if (response.getSelectedButton() !== ui.Button.OK) return;
    rowNumber = parseInt(response.getResponseText(), 10);
  }

  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];

  if (rowNumber < 2) {
    Logger.log('Row must be >= 2 (row 1 is headers)');
    return;
  }

  var email = String(sheet.getRange(rowNumber, COL_EMAIL).getValue()).trim();
  if (!email) {
    Logger.log('No email found in row ' + rowNumber);
    return;
  }

  var currentStatus = String(sheet.getRange(rowNumber, COL_STATUS).getValue());
  if (currentStatus === 'SUCCESS') {
    Logger.log('Row ' + rowNumber + ' already succeeded — skipping to prevent duplicate grant.');
    return;
  }

  var rateCheck = checkRateLimit(sheet, email);
  if (!rateCheck.allowed) {
    writeStatus(sheet, rowNumber, 'RATE_LIMITED', rateCheck.currentCount);
    Logger.log('Row ' + rowNumber + ' rate-limited (' + rateCheck.currentCount + '/' + MAX_SUBMISSIONS_PER_EMAIL + ')');
    return;
  }

  var result = postSlackMessage(email);

  if (result.ok) {
    writeStatus(sheet, rowNumber, 'SUCCESS', rateCheck.currentCount + 1);
    Logger.log('Row ' + rowNumber + ' retried successfully.');
  } else {
    writeStatus(sheet, rowNumber, 'ERROR: ' + (result.error || 'unknown'), rateCheck.currentCount);
    Logger.log('Row ' + rowNumber + ' retry failed: ' + result.error);
  }
}

/**
 * Test function — sends a test message to verify the server works.
 * Run this from the editor before testing with the form.
 */
function testWebhook() {
  Logger.log('Testing automation server...');
  var result = postSlackMessage('test@example.com');
  Logger.log('Result: ' + JSON.stringify(result));
}

/**
 * Helper to write status and count to the tracking columns.
 */
function writeStatus(sheet, row, status, count) {
  sheet.getRange(row, COL_STATUS).setValue(status);
  sheet.getRange(row, COL_COUNT).setValue(count);
}
