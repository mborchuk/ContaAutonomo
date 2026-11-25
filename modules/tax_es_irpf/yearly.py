"""The IRPF figures this module uses for a tax year.

The figures that change by year ship in `params/es_irpf_<year>.json`. A year
without its own file uses the latest earlier one. The owner checks the main
figures for a year in the Tax Data module (Taxes → Tax Data) and confirms
them or enters the new values; this module asks it through
`taxdata.year_values`. Without the Tax Data module, the shipped figures are
used and the warning stays. (Spanish Tax Forms and RETA Advisor have the
same small helpers: modules do not import each other.)
"""

TABLE_ID = 'es.irpf_figures'  # = table_checks.CHECK_IRPF['id']


def fields(params, year=None):
    """The figures the owner can change for a year, as Tax Data fields (the
    Modelo 130 rules that start in a later year only from that year)."""
    eds, m130 = params['eds'], params['modelo130']
    out = [
        {'key': 'dificil_pct', 'kind': 'percent',
         'label': 'Gastos de difícil justificación (% of the net)',
         'value': round(eds['dificil_justificacion_pct'] * 100, 2)},
        {'key': 'dificil_cap', 'kind': 'money',
         'label': 'Gastos de difícil justificación: annual cap (EUR)',
         'value': eds['dificil_justificacion_cap']},
        {'key': 'modelo130_rate', 'kind': 'percent',
         'label': 'Modelo 130: rate on the net (%)',
         'value': round(m130['rate'] * 100, 2)},
    ]
    reduced, exemption = m130.get('reduced_rate'), m130.get('exemption')
    if reduced and (year is None or year >= reduced['from_year']):
        out += [
            {'key': 'modelo130_reduced_rate', 'kind': 'percent',
             'label': 'Modelo 130: reduced rate (%)',
             'value': round(reduced['rate'] * 100, 2)},
            {'key': 'modelo130_reduced_below', 'kind': 'money',
             'label': 'Modelo 130: reduced rate when the net turnover two years before is '
                      'below (EUR)',
             'value': reduced['turnover_below']},
        ]
    if exemption and (year is None or year >= exemption['from_year']):
        out.append({'key': 'modelo130_exempt_below', 'kind': 'money',
                    'label': 'Modelo 130: none due when that net turnover is below (EUR) '
                             'and all payments were refunded',
                    'value': exemption['turnover_below']})
    return out


def override(values):
    """The values entered in Tax Data as an override of the params file."""
    out = {}
    if 'dificil_pct' in values:
        out.setdefault('eds', {})['dificil_justificacion_pct'] = values['dificil_pct'] / 100
    if 'dificil_cap' in values:
        out.setdefault('eds', {})['dificil_justificacion_cap'] = values['dificil_cap']
    if 'modelo130_rate' in values:
        out.setdefault('modelo130', {})['rate'] = values['modelo130_rate'] / 100
    if 'modelo130_reduced_rate' in values:
        out.setdefault('modelo130', {}).setdefault('reduced_rate', {})['rate'] = (
            values['modelo130_reduced_rate'] / 100)
    if 'modelo130_reduced_below' in values:
        out.setdefault('modelo130', {}).setdefault('reduced_rate', {})['turnover_below'] = (
            values['modelo130_reduced_below'])
    if 'modelo130_exempt_below' in values:
        out.setdefault('modelo130', {}).setdefault('exemption', {})['turnover_below'] = (
            values['modelo130_exempt_below'])
    return out


def validate(values):
    """A sentence when the entered values cannot be used, else None."""
    if 'dificil_pct' in values and not 0 <= values['dificil_pct'] < 100:
        return 'The difícil justificación percentage must be below 100 %.'
    if 'dificil_cap' in values and values['dificil_cap'] <= 0:
        return 'The annual cap must be above zero.'
    for key in ('modelo130_rate', 'modelo130_reduced_rate'):
        if key in values and not 0 < values[key] < 100:
            return 'The Modelo 130 rate must be between 0 and 100 %.'
    for key in ('modelo130_reduced_below', 'modelo130_exempt_below'):
        if key in values and values[key] <= 0:
            return 'A turnover limit must be above zero.'
    return None


def confirmed_values(functions, year):
    """What the owner confirmed for `year` in the Tax Data module:
    {'confirmed_on', 'values'}, or None (not confirmed, or no Tax Data
    module)."""
    if functions is None or not functions.available('taxdata.year_values'):
        return None
    try:
        status = functions.call('taxdata.year_values', table=TABLE_ID, year=year)
    except Exception:  # never break a tax page over a confirmation lookup
        return None
    return status if status.get('confirmed') else None


def notice_html(functions, year, table_year):
    """The Tax Data module's warning for a page that shows `year` with the
    `table_year` figures, or '' without that module."""
    if functions is None or not functions.available('taxdata.year_notice'):
        return ''
    try:
        return functions.call('taxdata.year_notice', table=TABLE_ID, year=year,
                              table_year=table_year).get('html') or ''
    except Exception:
        return ''
