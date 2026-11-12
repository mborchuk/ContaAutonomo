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
