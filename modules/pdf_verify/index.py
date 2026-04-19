"""
PDF Verify Module — detect and display digital signature info in PDF files.

Works with any signed PDF regardless of signing tool (DocuSign, Adobe Sign,
the built-in pdf_signature module, etc.).
"""

import io
import logging

from module_manager import BaseModule

logger = logging.getLogger('module.pdf_verify')

# Optional dependency — graceful degradation
try:
    from pypdf import PdfReader
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False

try:
    from asn1crypto import cms, x509, pem as asn1_pem
    ASN1_AVAILABLE = True
except ImportError:
    ASN1_AVAILABLE = False


def extract_signatures(pdf_bytes):
    """Extract digital signature info from PDF bytes.

    Returns a list of dicts, each with:
      - signer: common name or organization
      - issuer: certificate issuer
      - signed_at: signing time as ISO string or None
      - serial: certificate serial number
      - algorithm: signature algorithm
      - reason: signing reason (if present)
      - location: signing location (if present)
      - contact: signer contact info (if present)
    """
    if not PYPDF_AVAILABLE:
        return []

    results = []
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception:
        return []

    # Collect signature field values from AcroForm
    sig_objects = []
    if '/AcroForm' in reader.trailer.get('/Root', {}):
        acroform = reader.trailer['/Root']['/AcroForm']
        fields = acroform.get('/Fields', [])
        for field_ref in fields:
            field = field_ref.get_object() if hasattr(field_ref, 'get_object') else field_ref
            ft = field.get('/FT')
            if ft == '/Sig' and '/V' in field:
                sig_objects.append(field['/V'].get_object()
                                   if hasattr(field['/V'], 'get_object')
                                   else field['/V'])

    # Fallback: scan all objects for signature dicts
    if not sig_objects:
        try:
            for page in reader.pages:
                annots = page.get('/Annots', [])
                for annot_ref in annots:
                    annot = annot_ref.get_object() if hasattr(annot_ref, 'get_object') else annot_ref
                    if annot.get('/FT') == '/Sig' and '/V' in annot:
                        sig_obj = annot['/V']
                        if hasattr(sig_obj, 'get_object'):
                            sig_obj = sig_obj.get_object()
                        sig_objects.append(sig_obj)
        except Exception:
            pass

    for sig in sig_objects:
        info = _parse_sig_object(sig)
        if info:
            results.append(info)

    return results


def _parse_sig_object(sig):
    """Parse a PDF signature dictionary into a readable dict."""
    info = {
        'signer': '',
        'signer_email': '',
        'issuer': '',
        'signed_at': None,
        'serial': '',
        'algorithm': '',
        'valid_from': None,
        'valid_to': None,
        'reason': _pdf_str(sig.get('/Reason', '')),
        'location': _pdf_str(sig.get('/Location', '')),
        'contact': _pdf_str(sig.get('/ContactInfo', '')),
        'filter': _pdf_str(sig.get('/Filter', '')),
        'sub_filter': _pdf_str(sig.get('/SubFilter', '')),
        'cert_chain': [],
    }

    # Try to parse signing time from /M field
    m_date = sig.get('/M')
    if m_date:
        info['signed_at'] = _parse_pdf_date(str(m_date))

    # Parse PKCS#7 content if asn1crypto is available
    contents = sig.get('/Contents')
    if contents and ASN1_AVAILABLE:
        try:
            raw = bytes(contents)
            _parse_pkcs7(raw, info)
        except Exception as e:
            logger.debug('pdf_verify: failed to parse PKCS#7: %s', e)

    # Fallback signer name from /Name field
    if not info['signer']:
        info['signer'] = _pdf_str(sig.get('/Name', ''))

    return info if (info['signer'] or info['filter']) else None


def _parse_pkcs7(raw_bytes, info):
    """Parse PKCS#7 / CMS signed data to extract certificate details.

    Finds the leaf (end-entity) certificate that actually signed the document,
    not the root CA. Also builds the full certificate chain.
    """
    try:
        content_info = cms.ContentInfo.load(raw_bytes)
    except Exception:
        raw_bytes = raw_bytes.rstrip(b'\x00')
        content_info = cms.ContentInfo.load(raw_bytes)

    signed_data = content_info['content']

    # Extract signer info and identify which cert signed
    signer_serial = None
    signer_issuer = None
    signer_infos = signed_data['signer_infos']
    if signer_infos:
        si = signer_infos[0]
        algo = si['digest_algorithm']['algorithm']
        info['algorithm'] = _friendly_algorithm(
            algo.dotted if hasattr(algo, 'dotted') else '',
            algo.native if hasattr(algo, 'native') else ''
        )

        # Get signer identifier to match the correct certificate
        sid = si['sid']
        if sid.name == 'issuer_and_serial_number':
            signer_serial = sid.chosen['serial_number'].native
            signer_issuer = sid.chosen['issuer']

        # Signing time from authenticated attributes
        if si['signed_attrs']:
            for attr in si['signed_attrs']:
                if attr['type'].native == 'signing_time':
                    st = attr['values'][0].native
                    if st:
                        info['signed_at'] = st.isoformat()

    # Parse all certificates in the chain
    certs = signed_data['certificates']
    if not certs:
        return

    parsed_certs = []
    for cert_choice in certs:
        cert = cert_choice.chosen
        if not hasattr(cert, 'subject'):
            continue
        cert_info = _extract_cert_info(cert)
        parsed_certs.append((cert, cert_info))

    if not parsed_certs:
        return

    # Find the leaf certificate (the one that matches signer_serial, or
    # the one that is NOT an issuer of any other cert in the chain)
    leaf = None
    if signer_serial is not None:
        for cert, ci in parsed_certs:
            if hasattr(cert, 'serial_number') and cert.serial_number == signer_serial:
                leaf = (cert, ci)
                break

    if not leaf:
        # Heuristic: find cert whose subject is not the issuer of any other cert
        issuer_subjects = set()
        for cert, ci in parsed_certs:
            if hasattr(cert, 'issuer'):
                issuer_subjects.add(cert.issuer.human_friendly
                                    if hasattr(cert.issuer, 'human_friendly')
                                    else str(cert.issuer))
        for cert, ci in parsed_certs:
            subj = (cert.subject.human_friendly
                    if hasattr(cert.subject, 'human_friendly')
                    else str(cert.subject))
            if subj not in issuer_subjects:
                leaf = (cert, ci)
                break

    if not leaf:
        leaf = parsed_certs[0]

    # Fill info from leaf certificate
    _, leaf_info = leaf
    info['signer'] = leaf_info['subject']
    info['signer_email'] = leaf_info['email']
    info['issuer'] = leaf_info['issuer']
    info['serial'] = leaf_info['serial']
    info['valid_from'] = leaf_info['valid_from']
    info['valid_to'] = leaf_info['valid_to']

    # Build certificate chain (leaf first, root last)
    chain = []
    for _, ci in parsed_certs:
        chain.append(ci)
    # Sort: leaf first (non-self-signed), then intermediates, root last
    chain.sort(key=lambda c: (c['subject'] == c['issuer'], c['subject']))
    info['cert_chain'] = chain


def _extract_cert_info(cert):
    """Extract readable info from an asn1crypto certificate."""
    subject = (cert.subject.human_friendly
               if hasattr(cert.subject, 'human_friendly')
               else str(cert.subject))
    issuer = (cert.issuer.human_friendly
              if hasattr(cert.issuer, 'human_friendly')
              else str(cert.issuer))

    # Try to extract email from subject or SAN
    email = ''
    try:
        for attr in cert.subject:
            for name_val in attr:
                if name_val['type'].native == 'email_address':
                    email = str(name_val['value'])
    except Exception:
        pass

    if not email:
        try:
            san = cert.subject_alt_name_value
            if san:
                for name in san:
                    if name.name == 'rfc822_name':
                        email = str(name.chosen)
                        break
        except Exception:
            pass

    valid_from = None
    valid_to = None
    try:
        nb = cert['tbs_certificate']['validity']['not_before'].native
        na = cert['tbs_certificate']['validity']['not_after'].native
        if nb:
            valid_from = nb.isoformat()
        if na:
            valid_to = na.isoformat()
    except Exception:
        pass

    serial = str(cert.serial_number) if hasattr(cert, 'serial_number') else ''

    return {
        'subject': subject,
        'issuer': issuer,
        'email': email,
        'serial': serial,
        'valid_from': valid_from,
        'valid_to': valid_to,
    }


# Common OID to friendly name mapping
_ALGO_NAMES = {
    '2.16.840.1.101.3.4.2.1': 'SHA-256',
    '2.16.840.1.101.3.4.2.2': 'SHA-384',
    '2.16.840.1.101.3.4.2.3': 'SHA-512',
    '2.16.840.1.101.3.4.2.4': 'SHA-224',
    '1.3.14.3.2.26': 'SHA-1',
    '1.2.840.113549.2.5': 'MD5',
    '1.2.840.113549.1.1.11': 'SHA-256 with RSA',
    '1.2.840.113549.1.1.12': 'SHA-384 with RSA',
    '1.2.840.113549.1.1.13': 'SHA-512 with RSA',
    '1.2.840.113549.1.1.5': 'SHA-1 with RSA',
    '1.2.840.10045.4.3.2': 'ECDSA with SHA-256',
    '1.2.840.10045.4.3.3': 'ECDSA with SHA-384',
}


def _friendly_algorithm(oid, native_name):
    """Convert algorithm OID to human-readable name."""
    if oid in _ALGO_NAMES:
        return _ALGO_NAMES[oid]
    if native_name:
        return native_name.replace('_', ' ').title()
    return oid or 'Unknown'


def _pdf_str(val):
    """Convert a PDF object to a plain string."""
    if val is None:
        return ''
    s = str(val)
    if s.startswith('/'):
        s = s[1:]
    return s


def _parse_pdf_date(date_str):
    """Parse PDF date format D:YYYYMMDDHHmmSS into ISO string."""
    import re
    if not date_str:
        return None
    date_str = date_str.strip()
    if date_str.startswith('D:'):
        date_str = date_str[2:]
    # Remove timezone offset for simplicity
    date_str = re.sub(r"[+\-Z].*", '', date_str)
    try:
        from datetime import datetime
        if len(date_str) >= 14:
            dt = datetime.strptime(date_str[:14], '%Y%m%d%H%M%S')
        elif len(date_str) >= 8:
            dt = datetime.strptime(date_str[:8], '%Y%m%d')
        else:
            return None
        return dt.isoformat()
    except Exception:
        return None

class PDFVerifyModule(BaseModule):
    """Detect and display digital signature information in PDF files."""

    @property
    def module_id(self):
        return 'pdf_verify'

    @property
    def name(self):
        return 'PDF Signature Verify'

    @property
    def description(self):
        return ('Detect digital signatures in uploaded PDF files and display '
                'signer info. Works with DocuSign, Adobe Sign, and any '
                'standard PDF signature.')

    @property
    def version(self):
        return '1.0.0'

    @property
    def nav_items(self):
        return []

    def register_models(self, db):
        self._db = db

    def get_capabilities(self):
        """Expose capabilities for cross-module integration."""
        return [
            {
                'type': 'pdf_verify',
                'name': 'PDF Signature Verification',
                'accepts': ['pdf'],
                'action': lambda pdf_bytes, **kw: extract_signatures(pdf_bytes),
            },
            {
                'type': 'file_badge',
                'name': 'Signature Badge',
                'action': self._render_file_badge,
            },
            {
                'type': 'file_badge_script',
                'name': 'Signature Badge Script',
                'action': self._render_badge_script,
            },
        ]

    def _render_file_badge(self, df):
        """Return inline HTML badge placeholder for a document file."""
        return (f'<span id="sig-badge-{df.id}" style="margin-left:6px;" '
                f'data-storage-key="{df.file_path}" '
                f'data-filename="{df.original_filename or "file.pdf"}"></span>')

    def _render_badge_script(self):
        """Return JS script that loads signature badges via AJAX."""
        return '''<script>
document.addEventListener('DOMContentLoaded', function() {
    var badges = document.querySelectorAll('[id^="sig-badge-"]');
    if (!badges.length) return;
    badges.forEach(function(badge) {
        var key = badge.dataset.storageKey;
        var fname = badge.dataset.filename;
        var csrfMeta = document.querySelector('meta[name="csrf-token"]');
        var headers = {'Content-Type': 'application/json'};
        if (csrfMeta) headers['X-CSRFToken'] = csrfMeta.getAttribute('content');
        fetch('/pdf-verify/check', {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({storage_key: key})
        })
        .then(function(r) { return r.ok ? r.json() : null; })
        .then(function(data) {
            if (!data || !data.signatures || !data.signatures.length) return;
            var n = data.signatures.length;
            var signer = data.signatures[0].signer_email || data.signatures[0].signer || 'Unknown';
            var short = signer.length > 40 ? signer.substring(0, 40) + '\\u2026' : signer;
            var detailUrl = '/pdf-verify/details?key=' + encodeURIComponent(key)
                + '&name=' + encodeURIComponent(fname)
                + '&back=' + encodeURIComponent(window.location.pathname);
            badge.innerHTML = '<a href="' + detailUrl + '" style="display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:10px;font-size:11px;background:#e8f5e9;color:#2e7d32;text-decoration:none;border:1px solid #a5d6a7;" title="' + n + ' signature(s)">'
                + '\\u2705 Signed' + (n > 1 ? ' (' + n + ')' : '') + ' \\u2014 ' + short + '</a>';
        })
        .catch(function() {});
    });
});
</script>'''

    def register_routes(self, app):
        from flask import Blueprint, request, jsonify, render_template

        bp = Blueprint('pdf_verify', __name__,
                       template_folder='templates', url_prefix='/pdf-verify')
        login_required = self.core.login_required
        module = self

        @bp.route('/check', methods=['POST'])
        @login_required
        def check_signature():
            """AJAX endpoint: verify signatures in a file by storage key."""
            storage_key = request.json.get('storage_key', '') if request.is_json else ''
            if not storage_key:
                return jsonify({'error': 'No storage key'}), 400

            result = module.core.storage.get(storage_key)
            if not result:
                return jsonify({'error': 'File not found'}), 404

            pdf_bytes, _ = result
            sigs = extract_signatures(pdf_bytes)
            return jsonify({'signatures': sigs})

        @bp.route('/details')
        @login_required
        def signature_details():
            """Full-page signature details view."""
            storage_key = request.args.get('key', '')
            filename = request.args.get('name', 'file.pdf')
            back_url = request.args.get('back', '/')

            sigs = []
            error = None
            if not storage_key:
                error = 'No file specified.'
            else:
                result = module.core.storage.get(storage_key)
                if not result:
                    error = 'File not found in storage.'
                else:
                    pdf_bytes, _ = result
                    sigs = extract_signatures(pdf_bytes)

            return render_template('signature_details.html',
                                   signatures=sigs, filename=filename,
                                   error=error, back_url=back_url)

        app.register_blueprint(bp)
