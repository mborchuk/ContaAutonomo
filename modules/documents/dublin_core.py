"""Dublin Core metadata (ISO 15836) for a document — pure functions, no Flask.

Each document maps onto the fifteen Dublin Core elements; the record is
exported as `oai_dc` XML (the OAI-PMH container for simple Dublin Core) and as
plain values for the API. Elements without a value are left out, as the
standard allows (every element is optional and repeatable).

| Element     | From the document                                              |
|-------------|----------------------------------------------------------------|
| title       | name                                                           |
| creator     | creator (who issued or wrote it)                               |
| subject     | category, then each tag                                        |
| description | description                                                    |
| contributor | counterparty (the other party)                                 |
| date        | document date, ISO 8601                                        |
| type        | DCMI Type Vocabulary, from the files: Text, StillImage, Dataset |
| format      | the files' media types (IANA)                                  |
| identifier  | identifier (DOC-2026-0001), then the reference number           |
| language    | language (ISO 639-1, e.g. es)                                  |
| coverage    | the validity period, when both dates are set: date/expiry (ISO 8601) |
| rights      | rights                                                         |

publisher, source and relation are not kept by the app.
"""
import re
import xml.etree.ElementTree as ET

DC_NS = 'http://purl.org/dc/elements/1.1/'
OAI_DC_NS = 'http://www.openarchives.org/OAI/2.0/oai_dc/'
XSI_NS = 'http://www.w3.org/2001/XMLSchema-instance'
OAI_DC_SCHEMA = 'http://www.openarchives.org/OAI/2.0/oai_dc.xsd'

IDENTIFIER_PREFIX = 'DOC'

# Offered in the form; any ISO 639-1 code (two letters, optional region) is accepted.
LANGUAGES = (('es', 'Spanish'), ('ca', 'Catalan'), ('en', 'English'), ('fr', 'French'),
             ('de', 'German'), ('pt', 'Portuguese'), ('it', 'Italian'), ('uk', 'Ukrainian'))
RIGHTS = ('Internal', 'Confidential', 'Contains personal data', 'Public')

_LANGUAGE = re.compile(r'^[a-z]{2}(-[A-Z]{2})?$')

# IANA media types of the formats the module accepts.
MEDIA_TYPES = {
    'pdf': 'application/pdf', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
    'gif': 'image/gif', 'webp': 'image/webp', 'tif': 'image/tiff', 'tiff': 'image/tiff',
    'doc': 'application/msword',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'xls': 'application/vnd.ms-excel',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'csv': 'text/csv', 'txt': 'text/plain', 'xml': 'application/xml',
}
# DCMI Type Vocabulary term for a media type's top level.
_DCMI_TYPES = (('image/', 'StillImage'), ('text/csv', 'Dataset'),
               ('application/vnd.ms-excel', 'Dataset'),
               ('application/vnd.openxmlformats-officedocument.spreadsheetml', 'Dataset'))


def format_identifier(year, number):
    """'DOC-2026-0001'."""
    return f'{IDENTIFIER_PREFIX}-{year}-{number:04d}'


def next_identifier(year, existing):
    """The next identifier of `year` after the ones in `existing`."""
    prefix = f'{IDENTIFIER_PREFIX}-{year}-'
    numbers = [int(i[len(prefix):]) for i in existing
               if i and i.startswith(prefix) and i[len(prefix):].isdigit()]
    return format_identifier(year, max(numbers, default=0) + 1)


def clean_language(value):
    """An ISO 639-1 code ('es', 'en-GB'), or None when empty or not one."""
    value = (value or '').strip()
    if not value:
        return None
    if '-' in value:
        lang, region = value.split('-', 1)
        value = f'{lang.lower()}-{region.upper()}'
    else:
        value = value.lower()
    return value if _LANGUAGE.match(value) else None


def media_type(file_format):
    return MEDIA_TYPES.get((file_format or '').lower().lstrip('.'))


def dcmi_type(media_types):
    """'Text', 'StillImage' or 'Dataset' from the files' media types, or None."""
    types = []
    for mt in media_types:
        term = next((t for prefix, t in _DCMI_TYPES if mt.startswith(prefix)), 'Text')
        if term not in types:
            types.append(term)
    return types


def record(doc, file_formats=()):
    """[(element, value), ...] in Dublin Core order, empty values left out.
    `doc` has the module's Document fields; `file_formats` the files' extensions."""
    media = []
    for fmt in file_formats:
        mt = media_type(fmt)
        if mt and mt not in media:
            media.append(mt)
    tags = [t.strip().lstrip('#') for t in (doc.tags or '').split(',') if t.strip()]
    coverage = None
    if doc.document_date and doc.expiry_date:
        coverage = f'{doc.document_date.isoformat()}/{doc.expiry_date.isoformat()}'
    pairs = [('title', doc.name),
             ('creator', getattr(doc, 'creator', None)),
             ('subject', doc.category), *(('subject', t) for t in tags),
             ('description', doc.description),
             ('contributor', doc.counterparty),
             ('date', doc.document_date.isoformat() if doc.document_date else None),
             *(('type', t) for t in dcmi_type(media)),
             *(('format', m) for m in media),
             ('identifier', getattr(doc, 'identifier', None)),
             ('identifier', doc.reference_number),
             ('language', getattr(doc, 'language', None)),
             ('coverage', coverage),
             ('rights', getattr(doc, 'rights', None))]
    return [(element, str(value).strip()) for element, value in pairs
            if value is not None and str(value).strip()]


def as_dict(pairs):
    """{element: [values]} for JSON."""
    out = {}
    for element, value in pairs:
        out.setdefault(element, []).append(value)
    return out


def to_xml(pairs):
    """The record as an `oai_dc:dc` XML document (UTF-8 bytes)."""
    ET.register_namespace('oai_dc', OAI_DC_NS)
    ET.register_namespace('dc', DC_NS)
    ET.register_namespace('xsi', XSI_NS)
    root = ET.Element(f'{{{OAI_DC_NS}}}dc',
                      {f'{{{XSI_NS}}}schemaLocation': f'{OAI_DC_NS} {OAI_DC_SCHEMA}'})
    for element, value in pairs:
        ET.SubElement(root, f'{{{DC_NS}}}{element}').text = value
    ET.indent(root)
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)
