"""How a person checks the RETA tables of a year (year_tables.TableCheck).
Link checked on 22 November 2026."""
from year_tables import TableCheck

SEG_SOCIAL_BASES = ('Seguridad Social: bases y tipos de cotización',
                    'https://www.seg-social.es/wps/portal/wss/internet/Trabajadores/'
                    'CotizacionRecaudacionTrabajadores/36537')

CHECK_RETA = TableCheck(
    title='the RETA brackets',
    holds=('The self-employed (RETA) brackets of the year: each band of monthly net income '
           'with its minimum and maximum contribution base, the contribution rate (31.50 % '
           'in 2026) and the generic deduction. They are set every year by the cotización '
           'order published in the BOE.'),
    steps=(
        'Open the Seguridad Social page below and find the self-employed scheme section '
        '("Régimen Especial de Trabajadores Autónomos") for the year.',
        'Compare each bracket (tramo) — net income from / to, base mínima, base máxima — with '
        'the bracket table on the RETA Advisor page.',
        'Check the contribution rate and the cotización order it names (for 2026: Orden '
        'PJC/297/2026).',
    ),
    links=(SEG_SOCIAL_BASES,),
    update=('Add the year to BRACKET_TABLES and CONTRIBUTION_RATES in '
            'modules/reta_advisor/brackets.py (the same values if unchanged) and bump '
            'BRACKET_TABLE_VERSION. See CONTRIBUTING.md → Annual Tax Data Update.'),
)
