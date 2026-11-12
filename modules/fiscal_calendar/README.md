# Fiscal Calendar (`fiscal_calendar`)

Spanish AEAT filing deadlines as a dashboard panel with configurable reminders.
The calendar is local, versioned data — AEAT publishes no machine-readable
calendar, so deadlines ship inside the module and are updated once a year.

## Contents

1. [Features](#features)
2. [Deadline data](#deadline-data)
3. [Reminders](#reminders)
4. [Integration with the filing workflow](#integration-with-the-filing-workflow)

## Features

- **Dashboard panel**: upcoming and currently-open filing windows
  (about 60 days ahead) for the forms the user actually files
- **Calendar page**: `/fiscal-calendar/` — full year view with window status
  (upcoming / open / closed)
- **Settings → Fiscal Calendar**: checkboxes for the forms you file; defaults
  are inferred from your uploaded tax-form history
- **Reminders**: daily scheduler job (08:00) fires at T-14, T-7 and T-1 before
  a filing window closes

## Deadline data

`calendar_data.py` generates deadline windows for any fiscal year from these
rules, stamped with `CALENDAR_VERSION`:

| Forms | Period | Filing window |
|-------|--------|---------------|
| 303, 130, 349, 111 | Q1–Q3 | Days 1–20 of the month after the quarter ends |
| 303, 130, 349, 111 | Q4 | 1–30 January of the following year |
| 390 | Annual | 1–30 January of the following year |
| 100 (Renta) | Annual | 1 April – 30 June of the following year |

- A window that ends on a Saturday or Sunday moves to the following Monday.
- Dates AEAT moves for other reasons (national holidays) go into
  `WINDOW_OVERRIDES`, keyed by `(year, form, period)`.
- Years in `VERIFIED_YEARS` were checked against the AEAT calendar. Other
  years are generated from the rules and marked *(verify)* in the calendar and
  the dashboard panel.

The annual check is described in `CONTRIBUTING.md` → *Annual Tax Data Update*.
This dataset is a convenience, not an authoritative source.

## Reminders

- Idempotent: reminder state is persisted, so restarts never double-send.
- Delivery: always written to the activity log, and additionally sent through
  any registered `notify` capability — for example, email when the
  `invoice_email` module is enabled. No direct coupling between the modules.

## Integration with the filing workflow

Each deadline entry carries `form`, `year` and `quarter`, which is exactly the
key the Tax Management obligations view uses to match recorded filings — a
recorded filing resolves the corresponding calendar obligation.
