#!/usr/bin/env python3
"""
Verifactu and B2B e-invoicing obligation dates, with their legal source.

Pure data: no Flask, no database. The Verifactu status page and the module
READMEs show these dates. A date is "in_force" only when a norm published in
the BOE sets it; a date that was only announced is "announced" and shown as
"announced, not yet in force" until its norm is published.

Checked on 16 November 2026 against:
  - Real Decreto-ley 15/2025, de 2 de diciembre (BOE 3 December 2025,
    BOE-A-2025-24446), which amends the transitional dates of the Reglamento
    approved by Real Decreto 1007/2023 (Verifactu). AEAT note: "Ampliación del
    plazo de adaptación de los sistemas informáticos de facturación".
  - Ministerio de Hacienda, nota informativa of 5 October 2026: plan to move the
    remaining Verifactu obligations to October 2028. No norm carrying it out
    was in the BOE on the check date.
  - Real Decreto 238/2026, de 25 de marzo (BOE-A-2026-7295), disposición final
    cuarta, and Orden HAC/1028/2026, de 2 de octubre (BOE núm. 247, 5 October
    2026; in force 6 October 2026), which starts the 12/24/36-month periods of
    the B2B e-invoicing obligation. AEAT: "Calendario de implantación".

Update procedure: when the BOE publishes the norm for the October 2028
announcement, change the announced entry to "in_force" with its exact date
and BOE reference, move the 2027 Verifactu entries to "superseded", bump
OBLIGATIONS_VERSION and CHECKED_ON, and update modules/verifactu/README.md.
"""

from datetime import date

OBLIGATIONS_VERSION = "2026.11"
CHECKED_ON = date(2026, 11, 16)

IN_FORCE = 'in_force'
ANNOUNCED = 'announced'

STATUS_LABELS = {
    IN_FORCE: 'In force (BOE)',
    ANNOUNCED: 'Announced, not yet in force',
}

_RDL_15_2025 = ('Real Decreto-ley 15/2025, de 2 de diciembre (BOE-A-2025-24446), '
                'amending Real Decreto 1007/2023')
_HACIENDA_NOTE = ('Ministerio de Hacienda, nota informativa of 5 October 2026 — '
                  'no norm in the BOE yet')
_EINVOICE = ('Real Decreto 238/2026 (BOE-A-2026-7295), disposición final cuarta; '
             'Orden HAC/1028/2026 (BOE núm. 247, 5 October 2026)')

# Each entry: topic, who it applies to, the date (None when only a month is
# known), a display text for the date, status and source.
OBLIGATIONS = [
    {
        'topic': 'verifactu',
        'who': 'Taxpayers of the Impuesto sobre Sociedades',
        'date': date(2027, 1, 1),
        'date_text': '1 January 2027',
        'status': IN_FORCE,
        'source': _RDL_15_2025,
    },
    {
        'topic': 'verifactu',
        'who': 'Autónomos and every other obligated taxpayer',
        'date': date(2027, 7, 1),
        'date_text': '1 July 2027',
        'status': IN_FORCE,
        'source': _RDL_15_2025,
    },
    {
        'topic': 'verifactu',
        'who': 'All obligated taxpayers (planned new date)',
        'date': None,
        'date_text': 'October 2028',
        'status': ANNOUNCED,
        'source': _HACIENDA_NOTE,
    },
    {
        'topic': 'einvoice',
        'who': 'Businesses and professionals with a volume of operations above '
               '8 million EUR: issue, send and receive e-invoices; report '
               'invoice and payment status',
        'date': date(2027, 10, 6),
        'date_text': '6 October 2027',
        'status': IN_FORCE,
        'source': _EINVOICE,
    },
    {
        'topic': 'einvoice',
        'who': 'Every other business and professional, autónomos included: '
               'issue, send and receive e-invoices',
        'date': date(2028, 10, 6),
        'date_text': '6 October 2028',
        'status': IN_FORCE,
        'source': _EINVOICE,
    },
    {
        'topic': 'einvoice',
        'who': 'Individuals (autónomos) and entities under attribution of '
               'income: report invoice and payment status',
        'date': date(2029, 10, 6),
        'date_text': '6 October 2029',
        'status': IN_FORCE,
        'source': _EINVOICE,
    },
]


def obligations(topic=None):
    """Entries for one topic ('verifactu' or 'einvoice'), or all of them,
    each with its display `status_label`."""
    out = []
    for entry in OBLIGATIONS:
        if topic is not None and entry['topic'] != topic:
            continue
        enriched = dict(entry)
        enriched['status_label'] = STATUS_LABELS[entry['status']]
        out.append(enriched)
    return out
