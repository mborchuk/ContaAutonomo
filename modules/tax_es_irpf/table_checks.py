"""How a person checks the IRPF figures of a year — shown by the Tax Data
module (Taxes → Tax Data), where the owner confirms them or enters the new
values. Links checked on 25 November 2026."""

BOE_RDL_43_2026 = ('Real Decreto-ley 43/2026 (BOE-A-2026-24701)',
                   'https://www.boe.es/diario_boe/txt.php?id=BOE-A-2026-24701')
AEAT_EDS = ('AEAT Renta manual: provisiones and gastos de difícil justificación',
            'https://sede.agenciatributaria.gob.es/Sede/ayuda/manuales-videos-folletos/'
            'manuales-practicos/irpf-2025/c07-rendimientos-actividades-economicas-'
            'estimacion-directa/fase-1-determinacion-rendimiento-neto/'
            'gastos-fiscalmente-deducibles/provisiones.html')
AEAT_130 = ('AEAT Modelo 130', 'https://sede.agenciatributaria.gob.es/Sede/procedimientoini/G601.shtml')

CHECK_IRPF = dict(
    id='es.irpf_figures',
    name='IRPF figures (estimación directa simplificada)',
    holds=('The percentage of the net deducted as gastos de difícil justificación and its '
           'annual cap (10 % and 3,000 EUR in 2026, set by Real Decreto-ley 43/2026; 5 % and '
           '2,000 EUR before), and the Modelo 130 rate on the net (20 %). From 2027 also the '
           'reduced Modelo 130 rate (19 % when the net turnover two years before was below '
           '250,000 EUR) and the turnover below which no Modelo 130 is due when all '
           'payments were refunded (85,000 EUR). The IRPF Estimator, the Modelo 130 draft '
           'and the renta estimate use them.'),
    steps=(
        'Search the BOE for the year\'s IRPF changes (Ley de Presupuestos or a Real '
        'Decreto-ley) that mention "gastos de difícil justificación" or "pagos '
        'fraccionados".',
        'Compare the percentage and the cap with the AEAT Renta manual for the year, '
        'section "estimación directa simplificada".',
        'Check the Modelo 130 rate in the AEAT form instructions (casilla 04).',
    ),
    links=(BOE_RDL_43_2026, AEAT_EDS, AEAT_130),
    after_check=('Same values: confirm the year. New values: enter them below and save — '
                 'the IRPF Estimator uses them for that year. Brackets, mínimos and '
                 'regional scales still need an app update.'),
)
