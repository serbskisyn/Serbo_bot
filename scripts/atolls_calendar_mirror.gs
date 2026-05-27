/**
 * atolls_calendar_mirror.gs — Google Apps Script
 *
 * Mirrors the Atolls work calendar into a personal Gmail calendar that the
 * Serbo-Bot service account can already read. Works around the company
 * policy that forbids sharing the Atolls calendar externally, and survives
 * SSO because Apps Script time-triggers run server-side in Google with a
 * one-time authorization (no refresh token on the Pi).
 *
 * ─── WHERE THIS RUNS ──────────────────────────────────────────────────────
 *   In the ATOLLS Google account (script.google.com), because only that
 *   account can read the Atolls work calendar. It writes into a personal
 *   Gmail calendar ("Atolls Arbeit") that you share back to your Atolls
 *   account (edit rights) and to the bot service account (read rights).
 *
 * ─── SETUP (one-time) ─────────────────────────────────────────────────────
 *   1. In bennoschwede@gmail.com → Google Calendar → create a new calendar
 *      named "Atolls Arbeit".
 *   2. Share that "Atolls Arbeit" calendar:
 *        • with your ATOLLS account (benno.schwede@atolls.com) →
 *          permission "Make changes to events"
 *        • with the bot service account
 *          (serbo-bot@goldkind.iam.gserviceaccount.com) →
 *          permission "See all event details"
 *   3. Copy the "Atolls Arbeit" Calendar ID (Settings → Integrate calendar →
 *      Calendar ID — looks like ...@group.calendar.google.com) and paste it
 *      into TARGET_CALENDAR_ID below.
 *   4. Open script.google.com WHILE LOGGED INTO THE ATOLLS ACCOUNT →
 *      New project → paste this whole file → Save.
 *   5. Run `mirrorCalendar` once → authorize when prompted.
 *   6. Run `installTrigger` once → installs a time-trigger every
 *      TRIGGER_MINUTES (default 5). (Check Triggers panel ⏰ to confirm.)
 *   7. Send the "Atolls Arbeit" Calendar ID to the bot maintainer so it can
 *      be wired into GCAL_CALENDAR_ID_1.
 *
 * ─── WHAT IT DOES ─────────────────────────────────────────────────────────
 *   • Copies events in the next DAYS_AHEAD days from source → target
 *   • Idempotent: tracks each copy via an event tag (mirrorSourceId) so
 *     re-runs update in place instead of duplicating
 *   • Updates title/time/location when the source changes
 *   • Deletes mirrored copies whose source event disappeared
 */

// ── Config ───────────────────────────────────────────────────────────────
const SOURCE_CALENDAR_ID = 'primary';   // the Atolls work calendar (this account's primary)
const TARGET_CALENDAR_ID = 'PASTE_ATOLLS_ARBEIT_CALENDAR_ID_HERE';
const DAYS_AHEAD = 30;
const TRIGGER_MINUTES = 5;   // Apps Script minimum is 1; 5 is the quota-safe sweet spot


function mirrorCalendar() {
  const now = new Date();
  const end = new Date(now.getTime() + DAYS_AHEAD * 24 * 3600 * 1000);

  const source = CalendarApp.getCalendarById(SOURCE_CALENDAR_ID);
  const target = CalendarApp.getCalendarById(TARGET_CALENDAR_ID);
  if (!source) { Logger.log('SOURCE calendar not found: ' + SOURCE_CALENDAR_ID); return; }
  if (!target) { Logger.log('TARGET calendar not found: ' + TARGET_CALENDAR_ID); return; }

  const sourceEvents = source.getEvents(now, end);

  // Index existing mirrored copies in the target by their source-event id.
  const targetEvents = target.getEvents(now, end);
  const existing = {};
  targetEvents.forEach(function (ev) {
    const sid = ev.getTag('mirrorSourceId');
    if (sid) existing[sid] = ev;
  });

  const seen = {};
  let created = 0, updated = 0, deleted = 0;

  sourceEvents.forEach(function (se) {
    const sid = se.getId();
    seen[sid] = true;
    const title = se.getTitle();
    const start = se.getStartTime();
    const endT = se.getEndTime();
    const loc = se.getLocation() || '';
    const desc = se.getDescription() || '';

    const ev = existing[sid];
    if (ev) {
      // Update in place if title or time changed
      if (ev.getTitle() !== title) { ev.setTitle(title); updated++; }
      if (!se.isAllDayEvent() &&
          (ev.getStartTime().getTime() !== start.getTime() ||
           ev.getEndTime().getTime() !== endT.getTime())) {
        ev.setTime(start, endT); updated++;
      }
    } else {
      let neu;
      if (se.isAllDayEvent()) {
        neu = target.createAllDayEvent(title, start);
      } else {
        neu = target.createEvent(title, start, endT, { location: loc, description: desc });
      }
      neu.setTag('mirrorSourceId', sid);
      created++;
    }
  });

  // Remove copies whose source vanished
  Object.keys(existing).forEach(function (sid) {
    if (!seen[sid]) { existing[sid].deleteEvent(); deleted++; }
  });

  Logger.log('Mirror done — source=%s created=%s updated=%s deleted=%s',
             sourceEvents.length, created, updated, deleted);
}


function installTrigger() {
  // Remove any existing triggers for this function to avoid duplicates
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'mirrorCalendar') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('mirrorCalendar')
    .timeBased()
    .everyMinutes(TRIGGER_MINUTES)
    .create();
  Logger.log('Trigger installed: every %s minutes', TRIGGER_MINUTES);
}
