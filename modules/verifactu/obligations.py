#!/usr/bin/env python3
"""
Verifactu and B2B e-invoicing obligation dates, with their legal source.

Pure data: no Flask, no database. The Verifactu status page and the module
READMEs show these dates. A date is "in_force" only when a norm published in
the BOE sets it; a date that was only announced is "announced" and shown as
"announced, not yet in force" until its norm is published. A date a later
norm replaced stays in the table as "superseded", so the change can be seen.

Checked on 25 November 2026 against:
  - Real Decreto-ley 43/2026, de 24 de noviembre (BOE 25 November 2026,
    BOE-A-2026-24701; in force 26 November 2026), disposición final primera,
    which rewrites disposición final cuarta of Real Decreto 1007/2023: every
    obligated taxpayer of its article 3.1 must have adapted its invoicing
    system when the period of letter b) of disposición final cuarta.1 of
    Real Decreto 238/2026 ends — 24 months after Orden HAC/1028/2026 came
    into force (6 October 2026), the B2B e-invoicing date for autónomos.
    This carries out the Ministerio de Hacienda nota informativa of
    5 October 2026 and replaces the 2027 dates below.
  - Real Decreto-ley 15/2025, de 2 de diciembre (BOE 3 December 2025,
    BOE-A-2025-24446): the 2027 dates, now superseded.
  - Real Decreto 238/2026, de 25 de marzo (BOE-A-2026-7295), disposición final
    cuarta, and Orden HAC/1028/2026, de 2 de octubre (BOE núm. 247, 5 October
    2026; in force 6 October 2026), which starts the 12/24/36-month periods of
    the B2B e-invoicing obligation. AEAT: "Calendario de implantación".

Update procedure: when a norm in the BOE moves a date, add the new entry as
"in_force" with its exact date and BOE reference, mark the entry it replaces
"superseded" with `superseded_by`, bump OBLIGATIONS_VERSION and CHECKED_ON,
and update modules/verifactu/README.md.
"""

from datetime import date

OBLIGATIONS_VERSION = "2026.11.25"
CHECKED_ON = date(2026, 11, 25)

IN_FORCE = 'in_force'
ANNOUNCED = 'announced'
SUPERSEDED = 'superseded'

STATUS_LABELS = {
    IN_FORCE: 'In force (BOE)',
    ANNOUNCED: 'Announced, not yet in force',
    SUPERSEDED: 'Superseded',
}

_RDL_15_2025 = ('Real Decreto-ley 15/2025, de 2 de diciembre (BOE-A-2025-24446), '
                'amending Real Decreto 1007/2023')
_RDL_43_2026 = ('Real Decreto-ley 43/2026, de 24 de noviembre (BOE-A-2026-24701), '
                'disposición final primera, amending Real Decreto 1007/2023: the end of '
                'the period of Real Decreto 238/2026, disposición final cuarta.1.b)')
_RDL_43_2026_SHORT = 'Real Decreto-ley 43/2026 (BOE-A-2026-24701)'
_EINVOICE = ('Real Decreto 238/2026 (BOE-A-2026-7295), disposición final cuarta; '
             'Orden HAC/1028/2026 (BOE núm. 247, 5 October 2026)')

# Each entry: topic, who it applies to, the date (None when only a month is
# known), a display text for the date, status and source; a superseded entry
# names the norm that replaced it.
OBLIGATIONS = [
    {
        'topic': 'verifactu',
        'who': 'Every obligated taxpayer: Impuesto sobre Sociedades taxpayers, '
               'autónomos and everyone else',
        'date': date(2028, 10, 6),
        'date_text': '6 October 2028',
        'status': IN_FORCE,
        'source': _RDL_43_2026,
    },
    {
        'topic': 'verifactu',
        'who': 'Taxpayers of the Impuesto sobre Sociedades',
        'date': date(2027, 1, 1),
        'date_text': '1 January 2027',
        'status': SUPERSEDED,
        'source': _RDL_15_2025,
        'superseded_by': _RDL_43_2026_SHORT,
    },
    {
        'topic': 'verifactu',
        'who': 'Autónomos and every other obligated taxpayer',
        'date': date(2027, 7, 1),
        'date_text': '1 July 2027',
        'status': SUPERSEDED,
        'source': _RDL_15_2025,
        'superseded_by': _RDL_43_2026_SHORT,
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


def obligations(topic=None, include_superseded=True):
    """Entries for one topic ('verifactu' or 'einvoice'), or all of them,
    each with its display `status_label`; superseded ones last."""
    out = []
    for entry in OBLIGATIONS:
        if topic is not None and entry['topic'] != topic:
            continue
        if entry['status'] == SUPERSEDED and not include_superseded:
            continue
        enriched = dict(entry)
        enriched['status_label'] = STATUS_LABELS[entry['status']]
        out.append(enriched)
    return sorted(out, key=lambda e: e['status'] == SUPERSEDED)
