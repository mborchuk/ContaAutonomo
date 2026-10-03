"""Depreciation (amortización) of equipment for IRPF — pure, no Flask/DB.

An expense that is equipment (bien de inversión) is not deducted in full in
the quarter it is bought. Its net cost is spread over the following quarters:

- linear, `annual_rate_pct` of the cost per year (owner default 25%);
- a full quarter is counted from the quarter of purchase, whatever the day;
- never more than the cost in total.

The defaults match how the owner's gestor filed Modelo 130 in 2026 (an asset
bought on 3 July contributed cost × 25% / 4 to Q3). Items below the settings
threshold are not depreciated; the module deducts them in full.

Estimate, not tax advice.
"""


def _quarter_index(year, quarter):
    return year * 4 + (quarter - 1)


def depreciation_in_year(cost, bought, year, quarter, annual_rate_pct):
    """Depreciation of one asset that falls in `year`, from Q1 to `quarter`.

    Args:
        cost: net cost in EUR (VAT excluded, deductible share applied).
        bought: purchase date.
        year, quarter: the cumulative window is 1 January → end of `quarter`.
        annual_rate_pct: linear rate per year, e.g. 25.0.
    """
    if cost <= 0 or annual_rate_pct <= 0:
        return 0.0
    per_quarter = cost * annual_rate_pct / 100.0 / 4.0
    start = _quarter_index(bought.year, (bought.month - 1) // 3 + 1)

    def accumulated(through_index):
        quarters = max(0, through_index - start + 1)
        return min(cost, per_quarter * quarters)

    before_year = accumulated(_quarter_index(year, 1) - 1)
    up_to_quarter = accumulated(_quarter_index(year, quarter))
    return max(0.0, up_to_quarter - before_year)
