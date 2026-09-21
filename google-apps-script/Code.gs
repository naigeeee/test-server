// ============================================================================
// Google Apps Script — Feedback Survey <-> Backend Sync
// ============================================================================
//
// SETUP INSTRUCTIONS:
//   1. Open your Google Sheet (company domain).
//   2. Go to Extensions > Apps Script.
//   3. Delete any existing code and paste this entire script.
//   4. In the left sidebar, click Project Settings (gear icon).
//   5. Under "Script Properties", add two properties:
//        - FEEDBACK_API_URL  =  https://test-server.ninjavan.apps.substrait.build/api/feedback/export
//        - FEEDBACK_API_KEY  =  <your FEEDBACK_API_KEY value>
//   6. Click "Run" next to the `onOpen` function to authorise the script.
//   7. Reload the Sheet — you will see a "Feedback Sync" menu in the menu bar.
//
// WHAT THIS SCRIPT DOES:
//   - Pulls all feedback from the backend API and writes it to the active Sheet.
//   - Supports incremental sync (only new/changed rows since last sync).
//   - Adds a custom menu: Feedback Sync > Sync Now / Full Export / Set Last Sync.
//   - Creates a daily time trigger to auto-sync every 24 hours.
// ============================================================================

var SHEET_NAME = "Feedback Responses";
var HEADER_ROW = ["ID", "Name", "Rating", "Category", "Comments", "Event Name", "Created At"];

// ── Menu & Triggers ──────────────────────────────────────────────────────────

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu("Feedback Sync")
    .addItem("Sync Now (incremental)", "syncNow")
    .addItem("Full Export (all rows)", "fullExport")
    .addSeparator()
    .addItem("Run Initial Setup", "setupDailyTrigger")
    .addToUi();
}

function setupDailyTrigger() {
  // Remove existing triggers for this script
  ScriptApp.getProjectTriggers().forEach(function(t) {
    if (t.getHandlerFunction() === "autoSync") {
      ScriptApp.deleteTrigger(t);
    }
  });
  // Create a new daily trigger at 08:00
  ScriptApp.newTrigger("autoSync")
    .timeBased()
    .everyDays(1)
    .atHour(8)
    .create();
  SpreadsheetApp.getUi().alert("Daily auto-sync trigger created (runs at 08:00).");
}

// ── Sync functions ───────────────────────────────────────────────────────────

function syncNow() {
  var lastSync = PropertiesService.getScriptProperties().getProperty("lastSyncTimestamp");
  if (!lastSync) {
    // First run — do a full export
    fullExport();
    return;
  }
  var data = fetchFeedback(lastSync);
  if (!data || data.length === 0) {
    SpreadsheetApp.getUi().alert("No new feedback since last sync.");
    return;
  }
  appendToSheet(data);
  PropertiesService.getScriptProperties().setProperty("lastSyncTimestamp", new Date().toISOString());
  SpreadsheetApp.getUi().alert("Synced " + data.length + " new row(s).");
}

function fullExport() {
  var data = fetchFeedback(null);
  if (!data || data.length === 0) {
    SpreadsheetApp.getUi().alert("No feedback found on the server.");
    return;
  }
  writeFullSheet(data);
  PropertiesService.getScriptProperties().setProperty("lastSyncTimestamp", new Date().toISOString());
  SpreadsheetApp.getUi().alert("Exported " + data.length + " feedback response(s) to the sheet.");
}

function autoSync() {
  // Called by time trigger — no UI alerts, just log
  var lastSync = PropertiesService.getScriptProperties().getProperty("lastSyncTimestamp");
  var data = fetchFeedback(lastSync);
  if (!data || data.length === 0) {
    Logger.log("autoSync: no new feedback.");
    return;
  }
  appendToSheet(data);
  PropertiesService.getScriptProperties().setProperty("lastSyncTimestamp", new Date().toISOString());
  Logger.log("autoSync: synced " + data.length + " row(s).");
}

// ── HTTP ─────────────────────────────────────────────────────────────────────

function fetchFeedback(since) {
  var props = PropertiesService.getScriptProperties();
  var apiUrl = props.getProperty("FEEDBACK_API_URL");
  var apiKey = props.getProperty("FEEDBACK_API_KEY");

  if (!apiUrl || !apiKey) {
    SpreadsheetApp.getUi().alert(
      "Missing configuration.\n\n" +
      "Go to Project Settings > Script Properties and set:\n" +
      "  FEEDBACK_API_URL\n  FEEDBACK_API_KEY"
    );
    return null;
  }

  var url = apiUrl;
  if (since) {
    url += "?since=" + encodeURIComponent(since);
  }

  var options = {
    "method": "GET",
    "headers": {
      "x-api-key": apiKey
    },
    "muteHttpExceptions": true
  };

  var response = UrlFetchApp.fetch(url, options);
  var code = response.getResponseCode();

  if (code !== 200) {
    Logger.log("API error " + code + ": " + response.getContentText());
    return null;
  }

  var json = JSON.parse(response.getContentText());
  return json.feedback || [];
}

// ── Sheet I/O ────────────────────────────────────────────────────────────────

function getOrCreateSheet() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(SHEET_NAME);
    sheet.appendRow(HEADER_ROW);
    sheet.getRange(1, 1, 1, HEADER_ROW.length).setFontWeight("bold").setBackground("#f0fdfa");
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function writeFullSheet(data) {
  var sheet = getOrCreateSheet();
  // Clear everything below the header
  if (sheet.getLastRow() > 1) {
    sheet.deleteRows(2, sheet.getLastRow() - 1);
  }
  if (data.length === 0) return;
  var rows = data.map(function(fb) {
    return [
      fb.id || "",
      fb.respondent_name || "",
      fb.rating || "",
      fb.category || "",
      fb.comments || "",
      fb.event_name || "",
      fb.created_at || ""
    ];
  });
  sheet.getRange(2, 1, rows.length, HEADER_ROW.length).setValues(rows);
}

function appendToSheet(data) {
  var sheet = getOrCreateSheet();
  if (data.length === 0) return;

  // Get existing IDs to avoid duplicates
  var existingIds = {};
  if (sheet.getLastRow() > 1) {
    var idCol = sheet.getRange(2, 1, sheet.getLastRow() - 1, 1).getValues();
    idCol.forEach(function(row) { existingIds[row[0]] = true; });
  }

  var newRows = [];
  data.forEach(function(fb) {
    if (!existingIds[fb.id]) {
      newRows.push([
        fb.id || "",
        fb.respondent_name || "",
        fb.rating || "",
        fb.category || "",
        fb.comments || "",
        fb.event_name || "",
        fb.created_at || ""
      ]);
    }
  });

  if (newRows.length > 0) {
    sheet.getRange(sheet.getLastRow() + 1, 1, newRows.length, HEADER_ROW.length).setValues(newRows);
  }
}
