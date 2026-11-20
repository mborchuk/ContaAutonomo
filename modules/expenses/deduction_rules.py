#!/usr/bin/env python3
"""
Deduction checks on expenses (G-6) — a rule table with legal citations.

Pure: no Flask, no database. Each rule looks at one expense (plus, for the
meal rule, the other meal expenses of the same day) and returns notices.
A notice says that an expense breaks a rule or looks under-claimed; it never
changes the expense — the functions only read attributes.

Rules checked on 16 November 2026 against:
  - Ley 35/2006 (LIRPF), art. 30.2.5ª b) (suministros of a home partly used
    for the activity: 30 % of the share of floor area used) and c) (the
    taxpayer's own manutención, limits of the RIRPF, paid electronically,
    in hostelería establishments);
  - Real Decreto 439/2007 (RIRPF), art. 9.A.3.a) (manutención: 26.67 / 53.34
    EUR a day in Spain, 48.08 / 91.35 EUR abroad, without / with an overnight
    stay) and art. 22.4 (an asset used partly for private purposes is not
    affected to the activity; cars only with exclusive use, save the listed
    exceptions);
  - Ley 37/1992 (LIVA), art. 95.Tres.2ª (VAT on cars presumed 50 % deductible
    when used in the activity); arts. 15, 69 and 84.Uno.2º (reverse charge:
    services from another EU country are an intra-Community acquisition,
    Modelo 303 boxes 10/11 and Modelo 349; from outside the EU, boxes 12/13).
AEAT, "Manual práctico de Renta — rendimientos de actividades económicas en
estimación directa: gastos fiscalmente deducibles".

Estimate, not tax advice: every notice names its rule and source, and the
owner decides.
"""

from collections import defaultdict

from supplier_region import reverse_charge_problem

RULES_VERSION = "2026.2"
CHECKED_ON = "2026-11-20"

# Severities, from strongest to weakest.
EXCEEDS = 'exceeds'            # above the legal limit
CHECK = 'check'                # allowed only under a condition the app cannot see
UNDER_CLAIMED = 'under_claimed'

SEVERITY_LABELS = {
    EXCEEDS: 'Breaks a rule',
    CHECK: 'Check',
    UNDER_CLAIMED: 'Looks under-claimed',
}

# Categories each rule applies to (lower case; the expense category is free text).
HOME_SUPPLY_CATEGORIES = {
    'utilities', 'suministros', 'electricity', 'electricidad', 'luz', 'gas',
    'water', 'agua', 'internet',
}
MEAL_CATEGORIES = {
    'meals', 'meal', 'dietas', 'dieta', 'manutención', 'manutencion',
    'restaurant', 'restaurants', 'restaurante', 'comidas',
}
VEHICLE_CATEGORIES = {
    'vehicle', 'vehicles', 'car', 'fuel', 'vehículo', 'vehiculo', 'combustible',
    'gasolina', 'parking', 'tolls', 'peajes',
}

HOME_SUPPLY_SHARE = 0.30        # LIRPF art. 30.2.5ª b)
VEHICLE_VAT_PRESUMPTION = 50.0  # LIVA art. 95.Tres.2ª

# RIRPF art. 9.A.3.a): (without overnight stay, with overnight stay), EUR/day.
MEAL_LIMITS = {
    'spain': (26.67, 53.34),
    'abroad': (48.08, 91.35),
}

RULES = [
    {
        'id': 'home_supplies',
        'title': 'Home-office supplies',
        'summary': ('Supplies of a home used partly for the activity (water, gas, '
                    'electricity, telephone, internet) are deductible at 30 % of '
                    'the share of the floor area used for the activity, unless a '
                    'different percentage is proven.'),
        'source': 'Ley 35/2006 (LIRPF), art. 30.2.5ª b)',
        'categories': HOME_SUPPLY_CATEGORIES,
    },
    {
        'id': 'meal_limits',
        'title': 'Dietas (own meals) limits',
        'summary': ('Your own meals while working are deductible in hostelería '
                    'establishments, paid electronically, up to 26.67 EUR a day in '
                    'Spain (53.34 with an overnight stay) and 48.08 EUR abroad '
                    '(91.35 with an overnight stay).'),
        'source': 'Ley 35/2006 (LIRPF), art. 30.2.5ª c); RD 439/2007 (RIRPF), art. 9.A.3.a)',
        'categories': MEAL_CATEGORIES,
    },
    {
        'id': 'vehicle_use',
        'title': 'Vehicle costs',
        'summary': ('A car and its costs are deductible in IRPF only with exclusive '
                    'use for the activity (save transport, driving schools, sales '
                    'agents and similar). Its VAT is presumed 50 % deductible when it '
                    'is used in the activity; more needs proof.'),
        'source': 'RD 439/2007 (RIRPF), art. 22.4; Ley 37/1992 (LIVA), art. 95.Tres.2ª',
        'categories': VEHICLE_CATEGORIES,
    },
    {
        'id': 'reverse_charge_region',
        'title': 'Reverse charge and supplier country',
        'summary': ('An expense marked reverse charge declares its VAT by where the '
                    'supplier is: another EU country in Modelo 303 boxes 10/11 (and '
                    'Modelo 349), outside the EU in boxes 12/13. The mark must match '
                    "the supplier's country or VAT number."),
        'source': 'Ley 37/1992 (LIVA), arts. 15, 69 and 84.Uno.2º; Modelo 303 instructions',
        'categories': None,       # any category
    },
]

RULES_BY_ID = {rule['id']: rule for rule in RULES}


def _category(expense):
    return (getattr(expense, 'category', None) or '').strip().lower()


def _claimed_pct(expense):
    """Deductible share the expense claims, in % (0 when not deductible)."""
    if getattr(expense, 'deductible', True) is False:
        return 0.0
    pct = getattr(expense, 'deductible_pct', None)
    return 100.0 if pct is None else float(pct)


def _notice(rule_id, severity, message, expense):
    rule = RULES_BY_ID[rule_id]
    return {
        'rule_id': rule_id, 'severity': severity,
        'severity_label': SEVERITY_LABELS[severity],
        'title': rule['title'], 'message': message, 'source': rule['source'],
        'expense_id': getattr(expense, 'id', None),
    }


def check_home_supplies(expense, home_office_area_pct=None):
    """Cap = 30 % × the share of the home used for the activity. Without
    that share the highest possible cap (30 %, whole home) is used and only
    claims above it are flagged."""
    if _category(expense) not in HOME_SUPPLY_CATEGORIES:
        return []
    claimed = _claimed_pct(expense)
    area = home_office_area_pct if home_office_area_pct not in (None, '') else None
    cap = round(HOME_SUPPLY_SHARE * (100.0 if area is None else float(area)), 2)
    if claimed > cap + 0.005:
        where = (f'{float(area):g} % of the home used for the activity'
                 if area is not None else 'no home-office share set (cap for the whole home)')
        return [_notice('home_supplies', EXCEEDS,
                        f'Claims {claimed:g} % deductible; the legal percentage is '
                        f'{cap:g} % ({where}). A higher share needs proof.', expense)]
    if area is not None and claimed + 0.005 < cap:
        return [_notice('home_supplies', UNDER_CLAIMED,
                        f'Claims {claimed:g} % deductible; with {float(area):g} % of the '
                        f'home used for the activity the law allows {cap:g} %.', expense)]
    return []


def check_meal_day(day_total_eur, abroad, expense):
    """Notices for one day of meal expenses totalling `day_total_eur`."""
    without, with_overnight = MEAL_LIMITS['abroad' if abroad else 'spain']
    place = 'abroad' if abroad else 'in Spain'
    total = round(day_total_eur, 2)
    if total > with_overnight:
        return [_notice('meal_limits', EXCEEDS,
                        f'Meals of this day total {total:.2f} EUR, above the highest '
                        f'limit {place} ({with_overnight:.2f} EUR with an overnight '
                        f'stay). The excess is not deductible.', expense)]
    if total > without:
        return [_notice('meal_limits', CHECK,
                        f'Meals of this day total {total:.2f} EUR, above the limit '
                        f'{place} without an overnight stay ({without:.2f} EUR); only '
                        f'deductible up to {with_overnight:.2f} EUR if you stayed '
                        f'overnight.', expense)]
    return []


def check_vehicle(expense):
    if _category(expense) not in VEHICLE_CATEGORIES:
        return []
    claimed = _claimed_pct(expense)
    if claimed > VEHICLE_VAT_PRESUMPTION + 0.005:
        return [_notice('vehicle_use', CHECK,
                        f'Claims {claimed:g} % deductible. Above 50 % the VAT needs '
                        'proof of exclusive business use, and the cost counts in IRPF '
                        'only with exclusive use.', expense)]
    if claimed + 0.005 < VEHICLE_VAT_PRESUMPTION:
        return [_notice('vehicle_use', UNDER_CLAIMED,
                        f'Claims {claimed:g} % deductible. If the vehicle is used in the '
                        'activity, its VAT is presumed 50 % deductible.', expense)]
    return []


def check_reverse_charge_region(expense):
    """The reverse-charge mark disagrees with the supplier's region
    (supplier_region.py): EU marked for a US supplier, and so on."""
    problem = reverse_charge_problem(expense)
    return [_notice('reverse_charge_region', CHECK, problem, expense)] if problem else []


def _meal_day_totals(expenses, to_eur):
    """{(date, abroad): total EUR} for deductible meal expenses."""
    totals = defaultdict(float)
    for exp in expenses:
        if _category(exp) not in MEAL_CATEGORIES or _claimed_pct(exp) <= 0:
            continue
        d = getattr(exp, 'expense_date', None)
        if d is None:
            continue
        totals[(d, _is_abroad(exp))] += to_eur(exp)
    return totals


def _is_abroad(expense):
    """An expense in another currency is taken as spent abroad."""
    return (getattr(expense, 'currency', None) or 'EUR').upper() != 'EUR'


def check_expenses(expenses, home_office_area_pct=None, to_eur=None):
    """{expense_id: [notice, ...]} for every expense with a notice.

    `expenses` must include every meal expense of the days being checked, so
    the daily meal total is complete. `to_eur(expense)` gives the gross EUR
    amount (default: the stored amount).
    """
    to_eur = to_eur or (lambda e: getattr(e, 'amount', 0.0) or 0.0)
    expenses = list(expenses)
    day_totals = _meal_day_totals(expenses, to_eur)
    out = {}
    for exp in expenses:
        notices = []
        notices += check_home_supplies(exp, home_office_area_pct)
        notices += check_vehicle(exp)
        notices += check_reverse_charge_region(exp)
        if _category(exp) in MEAL_CATEGORIES and _claimed_pct(exp) > 0 \
                and getattr(exp, 'expense_date', None) is not None:
            notices += check_meal_day(day_totals[(exp.expense_date, _is_abroad(exp))],
                                      _is_abroad(exp), exp)
        if notices:
            out[getattr(exp, 'id', None)] = notices
    return out
