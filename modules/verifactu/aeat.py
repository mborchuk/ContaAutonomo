"""Official AEAT VERI*FACTU formats — pure functions, no Flask, no DB.

Sources (AEAT, sede electrónica / web de desarrolladores):
- "Detalle de las especificaciones técnicas para generación de la huella o
  hash de los registros de facturación", v0.1.2 (27/08/2024).
- "Detalle de las especificaciones técnicas del código «QR» de la factura y
  de la «URL» del servicio de cotejo…", v0.5.0 (10/12/2025).

See SPEC_COMPARISON.md for how the app's records differ today.
"""
import hashlib
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

# --- Huella (hash) of billing records -----------------------------------------

ALTA_FIELDS = ('IDEmisorFactura', 'NumSerieFactura', 'FechaExpedicionFactura',
               'TipoFactura', 'CuotaTotal', 'ImporteTotal', 'Huella',
               'FechaHoraHusoGenRegistro')
ANULACION_FIELDS = ('IDEmisorFacturaAnulada', 'NumSerieFacturaAnulada',
                    'FechaExpedicionFacturaAnulada', 'Huella',
                    'FechaHoraHusoGenRegistro')
NUMERIC_FIELDS = {'CuotaTotal', 'ImporteTotal'}


def _value(name, value):
    """Field value as it enters the hash: trimmed; numbers without trailing
    zeros in the decimals (123.10 and 123.1 hash the same); missing -> ''."""
    if value is None:
        return ''
    text = str(value).strip()
    if name in NUMERIC_FIELDS and text:
        try:
            number = Decimal(text)
        except InvalidOperation:
            return text
        text = format(number, 'f')
        if '.' in text:
            text = text.rstrip('0').rstrip('.')
    return text


def huella_input(fields, values):
    """'name1=value1&name2=value2…' in the official field order."""
    return '&'.join(f'{name}={_value(name, values.get(name))}' for name in fields)


def huella(fields, values):
    """SHA-256 of the UTF-8 input string, 64 uppercase hexadecimal characters."""
    return hashlib.sha256(huella_input(fields, values).encode('utf-8')).hexdigest().upper()


def huella_alta(values):
    return huella(ALTA_FIELDS, values)


def huella_anulacion(values):
    return huella(ANULACION_FIELDS, values)


# --- QR code ---------------------------------------------------------------------

QR_URLS = {
    ('verifactu', 'production'): 'https://www2.agenciatributaria.gob.es/wlpl/TIKE-CONT/ValidarQR',
    ('verifactu', 'test'): 'https://prewww2.aeat.es/wlpl/TIKE-CONT/ValidarQR',
    ('no_verifactu', 'production'):
        'https://www2.agenciatributaria.gob.es/wlpl/TIKE-CONT/ValidarQRNoVerifactu',
    ('no_verifactu', 'test'): 'https://prewww2.aeat.es/wlpl/TIKE-CONT/ValidarQRNoVerifactu',
}
QR_LABEL = 'QR tributario:'
VERIFACTU_PHRASE = 'VERI*FACTU'
QR_SIZE_MM = 30           # spec: between 30x30 and 40x40 mm
QR_ERROR_CORRECTION = 'M'  # spec: level M (ISO/IEC 18004:2015)


def _ascii(text):
    """Parameters may only contain printable ASCII (codes 32-126)."""
    bad = [c for c in text if not 32 <= ord(c) <= 126]
    if bad:
        raise ValueError(f'QR parameter contains non-printable-ASCII characters: {bad!r}')
    return text


def qr_url(nif, numserie, fecha, importe, mode='no_verifactu', environment='production'):
    """Cotejo URL for the invoice QR. `fecha` is a date; `importe` the total.

    Parameters are URL-encoded (UTF-8); the date is DD-MM-AAAA and the amount
    uses a dot as decimal separator.
    """
    base = QR_URLS[(mode, environment)]
    params = [
        ('nif', _ascii(str(nif or '').strip())),
        ('numserie', _ascii(str(numserie or '').strip())),
        ('fecha', fecha.strftime('%d-%m-%Y') if fecha else ''),
        ('importe', f'{Decimal(str(importe or 0)).quantize(Decimal("0.01"))}'),
    ]
    return base + '?' + '&'.join(f'{k}={quote(v, safe="")}' for k, v in params)
