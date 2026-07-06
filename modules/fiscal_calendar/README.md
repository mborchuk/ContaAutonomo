# fiscal_calendar — Spanish Fiscal Calendar & Reminders (F3)

Caveman summary: local data, no external API. Show upcoming AEAT filing windows
for the forms user file, remind before they close.

## What it does
- Dashboard panel (`get_dashboard_panels`): next ~60 days of filing windows for
  the user's selected forms, with window-open highlighting.
- Full calendar page: `/fiscal-calendar/`.
- Settings → Fiscal Calendar: pick which forms you file. Defaults inferred from
  distinct `TaxForm.form_type` in the DB.
- Daily scheduler reminder job (`fiscal_calendar.reminders`, 08:00): fires at
  T-14 / T-7 / T-1 before a window closes. Idempotent — reminder state stored in
  `fiscal_calendar_config`, so restarts don't double-send.
- Reminders write the activity log AND ride any `notify` capability (F8 email,
  webhooks) discovered via `find_capabilities('notify')` — zero coupling.

## Data (F3-D1) — STANDING RULE
Deadlines live in [`calendar_data.py`](calendar_data.py), stamped
`CALENDAR_VERSION`, generated from per-form window rules for `COVERED_YEARS`.
**Verify against the official AEAT calendar** before each fiscal year. Annual
update = edit `COVERED_YEARS` / the window rules in that one file.

Windows encoded (régimen general autónomo): quarterly 303/130/349/111 (day 1–20
of the month after quarter end; Q4 files 1–30 January of next year); annual 390
(1–30 Jan) and 100 renta (1 Apr–30 Jun).

## Overlap note
Core `app.py` still has a hardcoded `_get_upcoming_tax_deadlines()` shown on the
dashboard. This module is the richer, filterable replacement; the core list can
be retired in a later cleanup (out of F3 scope — F3 does not touch core).

## Resolvable by F10
Each deadline carries `form` + `year` + `quarter` so F10's filing workflow can
match a recorded `TaxForm` and mark the deadline resolved (✓).
