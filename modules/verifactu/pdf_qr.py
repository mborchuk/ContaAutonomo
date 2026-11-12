"""Put the AEAT QR code on the first page of an invoice PDF.

Works with every invoice template: the first page's content is scaled down
slightly to free a band at the top, and the band gets — centred, as the AEAT
QR specification asks for portrait invoices — the label "QR tributario:", the
QR code (30 x 30 mm, error correction level M) and, for VERI*FACTU systems
only, the phrase "VERI*FACTU". Later pages are unchanged (the QR appears once).
"""
import io

from reportlab.lib.units import mm

from .aeat import QR_ERROR_CORRECTION, QR_LABEL, QR_SIZE_MM

QUIET_ZONE = 3 * mm          # spec: at least 2 mm blank around the QR (6 mm advised)
TEXT_SIZE = 9                # points
BAND_TOP_MARGIN = 6 * mm


def _band_height(phrase):
    lines = 2 if phrase else 1
    return BAND_TOP_MARGIN + lines * (TEXT_SIZE + 4) + QR_SIZE_MM * mm + 2 * QUIET_ZONE


MIN_SIZE_MM, MAX_SIZE_MM = 30, 40   # spec: between 30x30 and 40x40 mm


def clamp_size_mm(value):
    """A QR size in mm inside the range the AEAT specification allows."""
    try:
        size = int(value)
    except (TypeError, ValueError):
        return QR_SIZE_MM
    return max(MIN_SIZE_MM, min(MAX_SIZE_MM, size))


def qr_drawing(url, size_mm=QR_SIZE_MM):
    """ReportLab Drawing of the QR (error correction M), `size_mm` square."""
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    size = clamp_size_mm(size_mm) * mm
    widget = QrCodeWidget(url, barLevel=QR_ERROR_CORRECTION, barBorder=0)
    x0, y0, x1, y1 = widget.getBounds()
    drawing = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    drawing.add(widget)
    return drawing


def has_qr(pdf_bytes):
    """True when page 1 already shows the tax QR label (e.g. a designer
    template placed the QR itself), so it must not be stamped again."""
    from pypdf import PdfReader
    try:
        return QR_LABEL in (PdfReader(io.BytesIO(pdf_bytes)).pages[0].extract_text() or '')
    except Exception:  # unreadable PDF: let stamping decide
        return False


def _overlay(width, height, url, phrase):
    from reportlab.graphics import renderPDF
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    size = QR_SIZE_MM * mm
    drawing = qr_drawing(url, QR_SIZE_MM)

    centre = width / 2
    label_y = height - BAND_TOP_MARGIN - TEXT_SIZE
    qr_y = label_y - QUIET_ZONE - size
    c.setFont('Helvetica-Bold', TEXT_SIZE)
    c.drawCentredString(centre, label_y, QR_LABEL)
    renderPDF.draw(drawing, c, centre - size / 2, qr_y)
    if phrase:
        c.setFont('Helvetica-Bold', TEXT_SIZE)
        c.drawCentredString(centre, qr_y - QUIET_ZONE - TEXT_SIZE + 2, phrase)
    c.showPage()
    c.save()
    buf.seek(0)
    return buf


def stamp_qr(pdf_bytes, url, phrase=None):
    """Return a copy of `pdf_bytes` with the QR band on page 1."""
    from pypdf import PdfReader, PdfWriter, Transformation

    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(pdf_bytes)))
    page = writer.pages[0]
    box = page.mediabox
    width, height = float(box.width), float(box.height)
    left, bottom = float(box.left), float(box.bottom)

    scale = (height - _band_height(phrase)) / height
    # Shrink the existing content towards the bottom centre.
    page.add_transformation(
        Transformation().translate(-left, -bottom).scale(scale, scale)
        .translate(left + width * (1 - scale) / 2, bottom))
    overlay = PdfReader(_overlay(width, height, url, phrase)).pages[0]
    page.merge_transformed_page(overlay, Transformation().translate(left, bottom))

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
