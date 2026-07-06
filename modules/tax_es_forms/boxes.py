#!/usr/bin/env python3
"""
Modelo 303 / 130 box mapping — versioned config data.

CAVEMAN NOTE: box numbers change across AEAT form revisions. So keep them in
this one file, stamp version, cite source. When AEAT change form, edit here
only. This is ESTIMATE aid, not tax advice — always check against current
official model before filing.

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
BOX_TABLE_VERSION = "2026.0"

# --- Modelo 303 (IVA, quarterly, régimen general) ---
# Only the principal boxes an estimación-directa autónomo transcribes.
MODELO_303_BOXES = {
    "01": "Base imponible IVA devengado — régimen general (tipo general)",
    "03": "Cuota IVA devengado — régimen general (tipo general)",
    "27": "Total cuota IVA devengado",
    "28": "Base — cuotas IVA soportado deducible (operaciones interiores corrientes)",
    "29": "Cuota IVA soportado deducible (operaciones interiores corrientes)",
    "45": "Total a deducir",
    "46": "Resultado régimen general (27 − 45)",
    "71": "Resultado de la liquidación",
}

# --- Modelo 130 (IRPF pago fraccionado, estimación directa) ---
# Cumulative year-to-date basis (as the official model requires).
MODELO_130_BOXES = {
    "01": "Ingresos computables (acumulado del ejercicio)",
    "02": "Gastos deducibles (acumulado del ejercicio)",
    "03": "Rendimiento (01 − 02)",
    "04": "20% del rendimiento (casilla 03, si es positivo)",
    "05": "Pagos fraccionados de trimestres anteriores",
    "07": "Resultado — pago fraccionado del trimestre (04 − 05)",
}
