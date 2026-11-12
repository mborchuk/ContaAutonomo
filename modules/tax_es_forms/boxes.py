#!/usr/bin/env python3
"""
Modelo 303 / 130 box mapping — versioned config data, keyed by fiscal year.

Box numbers change across AEAT form revisions, so they live in this one file
with a version stamp and the source to check. This is an estimate aid, not tax
advice — always check against the current official model before filing.

Annual update (each January): compare the boxes below with the new official
models. If nothing changed, add the new year pointing at the same tables; if
anything changed, add new dictionaries for that year. Years without a table
use the latest earlier one and the drafts show a "verify" banner.

Standing rule (F5-D1): box numbers below are the standard régimen general /
estimación directa boxes. Verify against the current official AEAT models
before each fiscal year:
  - Modelo 303 (IVA):  https://sede.agenciatributaria.gob.es (Modelo 303)
  - Modelo 130 (IRPF pago fraccionado): https://sede.agenciatributaria.gob.es (Modelo 130)

Scope of this calculator (v1) — out of scope regimes listed in the UI fine print:
  - régimen simplificado (módulos)
  - recargo de equivalencia
  - prorrata / regla de prorrata
  - criterio de caja (cash basis) — we use devengo (accrual) only
  - intra-EU acquisitions / inversión del sujeto pasivo detail boxes
"""

# Bump when box definitions are reviewed / changed.
BOX_TABLE_VERSION = "2026.1"

# --- Modelo 303 (IVA, quarterly, régimen general) ---
# Only the principal boxes an estimación-directa autónomo transcribes.
MODELO_303_BOXES = {
    "01": "Base imponible IVA devengado — régimen general (tipo general)",
    "03": "Cuota IVA devengado — régimen general (tipo general)",
    "10": "Base — adquisiciones intracomunitarias de bienes y servicios (inversión del sujeto pasivo)",
    "11": "Cuota — adquisiciones intracomunitarias de bienes y servicios",
    "12": "Base — otras operaciones con inversión del sujeto pasivo (proveedores fuera de la UE)",
    "13": "Cuota — otras operaciones con inversión del sujeto pasivo",
    "27": "Total cuota IVA devengado",
    "28": "Base — cuotas IVA soportado deducible (operaciones interiores corrientes)",
    "29": "Cuota IVA soportado deducible (operaciones interiores corrientes)",
    "30": "Base — cuotas IVA soportado deducible (bienes de inversión)",
    "31": "Cuota IVA soportado deducible (bienes de inversión)",
    "36": "Base — IVA deducible en adquisiciones intracomunitarias (corrientes)",
    "37": "Cuota — IVA deducible en adquisiciones intracomunitarias (corrientes)",
    "38": "Base — IVA deducible en adquisiciones intracomunitarias (bienes de inversión)",
    "39": "Cuota — IVA deducible en adquisiciones intracomunitarias (bienes de inversión)",
    "45": "Total a deducir",
    "46": "Resultado régimen general (27 − 45)",
    "59": "Entregas intracomunitarias de bienes y servicios (clientes UE)",
    "120": "Operaciones no sujetas por reglas de localización (clientes fuera de la UE)",
    "110": "Cuotas a compensar pendientes de periodos anteriores",
    "78": "Cuotas a compensar de periodos anteriores aplicadas en este periodo",
    "87": "Cuotas a compensar pendientes para periodos posteriores (110 − 78)",
    "71": "Resultado de la liquidación (46 − 78)",
}

# --- Modelo 130 (IRPF pago fraccionado, estimación directa) ---
# Cumulative year-to-date basis (as the official model requires).
MODELO_130_BOXES = {
    "01": "Ingresos computables (acumulado del ejercicio)",
    "02": "Gastos deducibles (acumulado del ejercicio)",
    "03": "Rendimiento (01 − 02)",
    "04": "20% del rendimiento (casilla 03, si es positivo)",
    "05": "Pagos fraccionados de trimestres anteriores",
    "06": "Retenciones e ingresos a cuenta (acumulado del ejercicio)",
    "07": "Resultado — pago fraccionado del trimestre (04 − 05 − 06)",
}


# Tables per fiscal year. Add a year after checking the official models.
BOX_TABLES = {
    2026: {"303": MODELO_303_BOXES, "130": MODELO_130_BOXES},
}


def boxes_for_year(year):
    """Return (table_year, {"303": ..., "130": ...}, is_fallback)."""
    from year_tables import resolve
    return resolve(BOX_TABLES, year)


# --- Modelo 390 (IVA, resumen anual) ---
# Casillas verified against the official AEAT "Instrucciones modelo 390 —
# 2025" (Sede AEAT, Procedimiento G412). Only the casillas this app can fill
# are listed; each one is the sum of the four quarterly 303 drafts (see
# calculator.compute_modelo_390). A fiscal year without its own table uses the
# latest one and the draft shows a "verify" banner.
MODELO_390_BOXES = {
    "05": "Régimen ordinario — base imponible al 21 %",
    "06": "Régimen ordinario — cuota al 21 %",
    "551": "Adquisiciones intracomunitarias de servicios — base imponible al 21 %",
    "552": "Adquisiciones intracomunitarias de servicios — cuota al 21 %",
    "27": "IVA devengado en otros supuestos de inversión del sujeto pasivo — base",
    "28": "IVA devengado en otros supuestos de inversión del sujeto pasivo — cuota",
    "33": "Total bases imponibles IVA",
    "34": "Total cuotas IVA",
    "47": "Total cuotas de IVA y recargo de equivalencia",
    "48": "IVA deducible — operaciones interiores, bienes y servicios corrientes — base",
    "49": "IVA deducible — operaciones interiores, bienes y servicios corrientes — cuota",
    "50": "IVA deducible — operaciones interiores, bienes de inversión — base",
    "51": "IVA deducible — operaciones interiores, bienes de inversión — cuota",
    "597": "IVA deducible — adquisiciones intracomunitarias de servicios — base",
    "598": "IVA deducible — adquisiciones intracomunitarias de servicios — cuota",
    "58": "IVA deducible — adquisiciones intracomunitarias, bienes de inversión — base",
    "59": "IVA deducible — adquisiciones intracomunitarias, bienes de inversión — cuota",
    "64": "Suma de deducciones",
    "65": "Resultado régimen general (47 − 64)",
    "84": "Suma de resultados",
    "85": "Compensación de cuotas del ejercicio anterior (aplicadas en el ejercicio)",
    "86": "Resultado de la liquidación (84 − 85)",
    "95": "Total resultados a ingresar en las autoliquidaciones del ejercicio",
    "97": "Resultado de la última autoliquidación a compensar",
    "99": "Volumen de operaciones — operaciones en régimen general",
    "103": "Volumen de operaciones — entregas intracomunitarias de bienes y servicios",
    "110": "Volumen de operaciones — operaciones no sujetas por reglas de localización",
    "108": "Volumen total de operaciones (99 + 103 + 110)",
}

# Display order on the draft page.
MODELO_390_ORDER = ["05", "06", "551", "552", "27", "28", "33", "34", "47",
                    "48", "49", "50", "51", "597", "598", "58", "59", "64", "65",
                    "84", "85", "86", "95", "97", "99", "103", "110", "108"]

# Not covered by the 390 draft (listed on the page).
MODELO_390_OUT_OF_SCOPE = [
    ("Rates other than 21 % (casillas 01–04, 700–703, 667–670): invoices at "
     + "other rates are flagged and must be split by hand"),
    ("Intra-EU acquisitions of goods (casillas 21–26, 56–57): purchases are "
     + "reported as services (551/552, 597/598) — move goods if you bought any"),
    ("Recargo de equivalencia, régimen simplificado, criterio de caja, "
     + "bienes usados, agencias de viaje, operaciones intragrupo"),
    ("Importaciones (casillas 52–55, 659), prorrata (522) and regularizaciones "
     + "of bienes de inversión (63)"),
    ("Exempt operations (104, 105), operaciones específicas (section 11) and "
     + "tributación por razón del territorio (87–91)"),
    "Taxpayers in the SII do not file Modelo 390",
]

MODELO_390_TABLES = {
    2025: MODELO_390_BOXES,
}


def modelo_390_boxes_for_year(year):
    """Return (table_year, labels, is_fallback) for the 390 casillas."""
    from year_tables import resolve
    return resolve(MODELO_390_TABLES, year)
