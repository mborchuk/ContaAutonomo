"""How a person checks this module's yearly tables, and the automatic part:
the box numbers the drafts use, compared with the official AEAT record layouts
the app ships (`aeat_layouts/`). The Tax Data module (Taxes → Tax Data) shows
these to the owner, who confirms each table for a new year or enters its new
values; `index.py` declares them as `year_table` capabilities.

Links checked on 22 November 2026.
"""
from .aeat_files import LAYOUT_FILES, UnsupportedYear, load_layout
from .boxes import MODELO_130_BOXES, MODELO_303_BOXES, MODELO_390_BOXES
from .yearly import resolve

AEAT_LAYOUTS_300 = ('AEAT record layouts, models 300–399',
                    'https://sede.agenciatributaria.gob.es/Sede/ayuda/disenos-registro/modelos-300-399.html')
AEAT_LAYOUTS_100 = ('AEAT record layouts, models 100–199',
                    'https://sede.agenciatributaria.gob.es/Sede/ayuda/disenos-registro/modelos-100-199.html')
AEAT_303 = ('AEAT Modelo 303', 'https://sede.agenciatributaria.gob.es/Sede/procedimientoini/G414.shtml')
AEAT_130 = ('AEAT Modelo 130', 'https://sede.agenciatributaria.gob.es/Sede/procedimientoini/G601.shtml')
AEAT_390 = ('AEAT Modelo 390', 'https://sede.agenciatributaria.gob.es/Sede/procedimientoini/G412.shtml')
AEAT_347 = ('AEAT Modelo 347', 'https://sede.agenciatributaria.gob.es/Sede/procedimientoini/GI27.shtml')
RD_1065_ART_33 = ('Real Decreto 1065/2007, art. 33 (BOE)',
                  'https://www.boe.es/buscar/act.php?id=BOE-A-2007-15984#a33')



def layout_boxes(model, year):
    """(layout name, layout year, {casillas}) of the official layout the app
    uses for `model` in `year`."""
    layout = load_layout(model, year)
    table_year, _file, _fallback = resolve(LAYOUT_FILES[model], year)
    boxes = {str(f[4]) for fields in layout['pages'].values() for f in fields if f[4]}
    return layout['layout'], table_year, boxes


def boxes_in_layout(model, boxes, year):
    """(ok, message): are the box numbers the app uses in the official AEAT
    record layout for `year`? None when the app has no layout for it."""
    try:
        name, layout_year, official = layout_boxes(model, year)
    except UnsupportedYear:
        return None, f'The app has no official Modelo {model} record layout for {year} to compare with.'
    missing = sorted(set(boxes) - official, key=lambda b: (len(b), b))
    if layout_year == year:
        which = f'the official AEAT record layout for {year} ({name})'
        later = ''
    else:
        which = f'{name}, the newest official AEAT record layout the app has (from {layout_year})'
        later = (f' If the AEAT published a newer layout for {year} (record layouts page '
                 'below), compare with that one.')
    if missing:
        return False, (f'Checked automatically: box(es) {", ".join(missing)} are not in '
                       f'{which}. The numbering changed — do not use these figures until the '
                       f'table is updated.')
    return True, (f'Checked automatically: all {len(boxes)} box numbers the app uses exist in '
                  f'{which}.{later} Check the labels as below.')


def _both(year):
    """303 and 130 together: the first problem, else both confirmations."""
    results = [boxes_in_layout('303', MODELO_303_BOXES, year),
               boxes_in_layout('130', MODELO_130_BOXES, year)]
    for ok, message in results:
        if ok is False:
            return ok, message
    if all(ok for ok, _ in results):
        return True, ' '.join(message for _, message in results)
    return None, ' '.join(message for _, message in results)


CHECK_303_130 = dict(
    id='es.boxes_303_130',
    name='Modelo 303 and 130 boxes',
    holds=('The box numbers and labels on the Modelo 303 and 130 draft pages. They change '
           'only when the AEAT changes the form.'),
    steps=(
        'Open the AEAT Modelo 303 page (and Modelo 130 page) below and open the form or its '
        'instructions (Ayuda → Instrucciones) for the year.',
        'Compare each box number and its meaning on the draft page (the Box column) with the '
        'official form. The 21 % VAT line is [07]/[09]; the result is [71] (303) and [19] (130).',
        'If the AEAT published a new record layout ("Diseño de registro") for the year, it is '
        'listed on the record layouts pages below.',
    ),
    links=(AEAT_303, AEAT_130, AEAT_LAYOUTS_300, AEAT_LAYOUTS_100),
    after_check=('Same numbers and meanings: confirm the year. A renumbered box needs an app '
                 'update — do not file from the drafts until then.'),
    auto=_both,
)


CHECK_390 = dict(
    id='es.casillas_390',
    name='Modelo 390 casillas',
    holds=('The casilla numbers and labels of the annual VAT summary. Each casilla is the sum '
           'of the four quarterly Modelo 303 drafts; the AEAT can add or renumber casillas.'),
    steps=(
        'Open the AEAT Modelo 390 page below and open the instructions (Ayuda → Instrucciones) '
        'or the form for the year.',
        'Compare each casilla number and its meaning in the "390 casilla" column of the draft '
        'page with the official form: 05/06 régimen ordinario 21 %, 551/552, 27/28, 33/34, 47, '
        '48–51, 597/598, 58/59, 64, 65, 84–86, 95, 97, 99, 103, 110, 108.',
        'The record layout for the year ("Diseño de registro", models 300–399 below) lists every '
        'casilla by number; the app compares with it automatically when it has that layout.',
    ),
    links=(AEAT_390, AEAT_LAYOUTS_300),
    after_check=('Same casillas: confirm the year. A new or renumbered casilla needs an app '
                 'update — do not file from the draft until then.'),
    auto=lambda year: boxes_in_layout('390', MODELO_390_BOXES, year),
)

CHECK_347 = dict(
    id='es.threshold_347',
    name='Modelo 347 threshold',
    holds=('The amount above which a customer or supplier must be declared: 3.005,06 euros a '
           'year, VAT included. It is set by Real Decreto 1065/2007, article 33.'),
    steps=(
        'Open article 33 of Real Decreto 1065/2007 (BOE, link below) and check that paragraph 1 '
        'still says "hayan superado la cifra de 3.005,06 euros durante el año natural".',
        'Open the AEAT Modelo 347 page below and check the instructions for the year for any '
        'other change (keys, exclusions).',
    ),
    links=(RD_1065_ART_33, AEAT_347),
    after_check=('Same amount: confirm the year. A new amount: enter it below and save — the '
                 'Modelo 347 check of that year uses it.'),
)
