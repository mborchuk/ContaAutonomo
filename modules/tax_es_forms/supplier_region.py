"""
Where an expense's supplier is — Spain, another EU country or outside the
EU — and whether the expense's reverse-charge setting agrees (Spanish VAT:
LIVA arts. 15, 69 and 84.Uno.2º).

Part of Spanish Tax Forms. The Modelo 303 draft uses it directly; other
modules ask through the registry function `vat.reverse_charge_check` (the
Expenses deduction checks do). Pure: no Flask, no database.

The region comes from the supplier's country when it is recognised, else
from the prefix of its VAT number (`IE…` is EU; `EU…` is a non-EU company
registered in the Union's one-stop shop; another letter prefix such as `GB`
or `CH` is outside the EU). Anything else is unknown, and nothing is said.
"""
import re

SPAIN, EU, NON_EU = 'es', 'eu', 'non_eu'

# EU member states other than Spain: ISO 3166 codes, VAT prefixes (Greece is
# EL) and common English and Spanish names.
_EU = {
    'at': 'austria', 'be': 'belgium', 'bg': 'bulgaria', 'hr': 'croatia',
    'cy': 'cyprus', 'cz': 'czechia', 'dk': 'denmark', 'ee': 'estonia',
    'fi': 'finland', 'fr': 'france', 'de': 'germany', 'gr': 'greece',
    'hu': 'hungary', 'ie': 'ireland', 'it': 'italy', 'lv': 'latvia',
    'lt': 'lithuania', 'lu': 'luxembourg', 'mt': 'malta', 'nl': 'netherlands',
    'pl': 'poland', 'pt': 'portugal', 'ro': 'romania', 'sk': 'slovakia',
    'si': 'slovenia', 'se': 'sweden',
}
_EU_NAMES = set(_EU) | set(_EU.values()) | {
    'el', 'czech republic', 'the netherlands', 'holland', 'alemania', 'austria',
    'bélgica', 'belgica', 'croacia', 'chipre', 'chequia', 'república checa',
    'republica checa', 'dinamarca', 'eslovaquia', 'eslovenia', 'finlandia',
    'francia', 'grecia', 'hungría', 'hungria', 'irlanda', 'italia', 'letonia',
    'lituania', 'luxemburgo', 'países bajos', 'paises bajos', 'holanda',
    'polonia', 'rumanía', 'rumania', 'suecia',
}
_SPAIN_NAMES = {'es', 'esp', 'spain', 'españa', 'espana', 'kingdom of spain'}
_NON_EU_NAMES = {
    'us', 'usa', 'u.s.', 'u.s.a.', 'united states', 'united states of america',
    'estados unidos', 'eeuu', 'ee.uu.', 'gb', 'uk', 'united kingdom',
    'great britain', 'england', 'reino unido', 'ch', 'switzerland', 'suiza',
    'no', 'norway', 'noruega', 'is', 'iceland', 'islandia', 'li',
    'liechtenstein', 'ua', 'ukraine', 'ucrania', 'ca', 'canada', 'canadá',
    'au', 'australia', 'nz', 'new zealand', 'nueva zelanda', 'jp', 'japan',
    'japón', 'japon', 'cn', 'china', 'in', 'india', 'il', 'israel', 'sg',
    'singapore', 'singapur', 'tr', 'turkey', 'türkiye', 'turquía', 'turquia',
    'ae', 'united arab emirates', 'emiratos árabes unidos', 'hk', 'hong kong',
    'kr', 'south korea', 'corea del sur', 'br', 'brazil', 'brasil', 'mx',
    'mexico', 'méxico', 'ar', 'argentina', 'rs', 'serbia', 'md', 'moldova',
    'moldavia', 'ge', 'georgia', 'am', 'armenia', 'ru', 'russia', 'rusia',
    'by', 'belarus', 'bielorrusia', 'ad', 'andorra', 'gi', 'gibraltar',
}
_VAT_PREFIX = re.compile(r'^([A-Za-z]{2})')

LABELS = {SPAIN: 'Spain', EU: 'another EU country', NON_EU: 'outside the EU'}


def _country_region(country):
    name = re.sub(r'\s+', ' ', (country or '').strip().lower())
    if not name:
        return None
    if name in _SPAIN_NAMES:
        return SPAIN
    if name in _EU_NAMES:
        return EU
    if name in _NON_EU_NAMES:
        return NON_EU
    return None


def _vat_region(vat_number):
    m = _VAT_PREFIX.match((vat_number or '').strip())
    if not m:
        return None
    prefix = m.group(1).lower()
    if prefix == 'es':
        return SPAIN
    if prefix == 'eu':            # non-Union OSS registration
        return NON_EU
    if prefix in _EU or prefix == 'el':
        return EU
    if prefix == 'xi':            # Northern Ireland: EU rules for goods only
        return None
    return NON_EU


def region_of(country=None, vat_number=None):
    """'es', 'eu', 'non_eu' or None (unknown)."""
    return _country_region(country) or _vat_region(vat_number)


def reverse_charge_problem(expense):
    """reverse_charge_check() for an expense and its contractor."""
    supplier = getattr(expense, 'contractor', None)
    if supplier is None:
        return None
    return reverse_charge_check(getattr(expense, 'reverse_charge', None) or '',
                                getattr(supplier, 'country', None),
                                getattr(supplier, 'vat_number', None),
                                getattr(supplier, 'name', None))


def reverse_charge_check(marked, country=None, vat_number=None, supplier=None):
    """A sentence when a reverse-charge mark ('eu' or 'non_eu') disagrees
    with where the supplier is, else None (also when the region is unknown).
    Plain values in, plain value out: the registry function
    `vat.reverse_charge_check` calls it."""
    if marked not in (EU, NON_EU):
        return None
    region = region_of(country, vat_number)
    if region is None or region == marked:
        return None
    name = supplier or 'The supplier'
    where = (country or '').strip() or f'VAT number {(vat_number or "").strip()}'
    if region == SPAIN:
        return (f'Marked as reverse charge from {LABELS[marked]}, but {name} is in '
                f'Spain ({where}). A Spanish supplier normally charges VAT on the '
                'invoice; reverse charge from abroad does not apply.')
    if region == NON_EU:
        return (f'Marked as reverse charge from another EU country, but {name} is '
                f'outside the EU ({where}). Choose "Supplier outside the EU": '
                'Modelo 303 boxes 12/13 instead of 10/11, and not in Modelo 349.')
    return (f'Marked as reverse charge from outside the EU, but {name} is in '
            f'another EU country ({where}). Choose "Supplier in another EU '
            'country": Modelo 303 boxes 10/11 instead of 12/13, and Modelo 349 '
            '(key I).')
