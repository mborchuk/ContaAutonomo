# Tax Data

Check the yearly tax tables in the app, and confirm them for a new year or
enter the new values. On by default.

Tax figures that change by year ship with the modules that use them, keyed by
year: the Modelo 303 and 130 boxes, the 390 casillas and the 347 threshold
(Spanish Tax Forms), the RETA brackets and contribution rate (RETA Advisor).
When a year has no table of its own, the module uses the latest earlier one
and its pages warn *"Figures for 2027 use the 2026 table"*.

**Taxes → Tax Data** lists every such table for the year you pick:

- its state: *2027 table in the app*, *Uses the 2026 table — not confirmed*,
  or *Confirmed 20/01/2027*;
- what it holds, numbered steps and links to the official AEAT, BOE and
  Seguridad Social pages;
- for the 303, 130 and 390 boxes, an automatic comparison with the official
  AEAT record layouts the app ships;
- **Confirm unchanged for 2027**, or the values (347 threshold; RETA rate,
  bracket limits and monthly quotas) and **Save for 2027**.

Once confirmed, the pages use the saved values for that year and stop
warning. **Remove the confirmation** undoes it. Box numbers cannot be edited:
a renumbered box needs an app update (`CONTRIBUTING.md` → *Annual Tax Data
Update*).

## For module authors

Declare a `year_table` capability (contract in the docstring of `index.py`)
and read the owner's confirmation with `taxdata.year_values(table, year)`;
show `taxdata.year_notice(table, year, table_year)` on the page that uses an
earlier year's table. Examples: `modules/tax_es_forms/yearly.py`,
`modules/reta_advisor/yearly.py`.

Data: table `tax_data_entry`, one row per confirmed table and year, holding
only the values that differ from the module's table. Nothing leaves the app.
