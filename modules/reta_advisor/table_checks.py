"""How a person checks the RETA tables of a year — shown by the Tax Data
module (Taxes → Tax Data), where the owner confirms them or enters the new
values. Link checked on 22 November 2026."""

SEG_SOCIAL_BASES = ('Seguridad Social: bases y tipos de cotización',
                    'https://www.seg-social.es/wps/portal/wss/internet/Trabajadores/'
                    'CotizacionRecaudacionTrabajadores/36537')

CHECK_RETA = dict(
    id='es.reta',
    name='RETA brackets and contribution rate',
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
    after_check=('Same values: confirm the year. New values: enter them below and save — '
                 'the RETA Advisor uses them for that year. A different number of brackets '
                 'needs an app update.'),
)
