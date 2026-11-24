"""
Invoice PDFs: the one place that builds an invoice's PDF in the template
chosen in Settings → General ("Invoice PDF Template", `Settings.invoice_template`).

Templates come from `ModuleManager.get_invoice_templates()`:
- the core's files in `invoice_templates/`, each with
  `generate_invoice_pdf(invoice, customer, settings[, Bank])` returning a
  buffer;
- modules, through `BaseModule.get_invoice_templates()` (the Invoice Designer):
  a template file (`path`) or a `render(invoice, customer, settings)` callable
  that returns the PDF bytes, or None when the template no longer exists.

Only a listed template is used. A chosen template that is not listed, a module
template that returns None, or a missing file falls back to the default
template, and the result says so (`fallback`). The PDF then passes through
every module's `filter_invoice_pdf` (the Verifactu QR on issued invoices).

Callers use the registry functions `invoice.render_pdf`,
`invoice.list_pdf_templates` and `invoice.list_blocks` (core_functions.py).
Storing the PDF stays with the caller.
"""
import importlib.util
import inspect
import os
from dataclasses import dataclass

DEFAULT_TEMPLATE = 'default_template'


@dataclass(frozen=True)
class RenderedPdf:
    pdf: bytes
    template: str       # id of the template that built it
    fallback: bool      # the chosen template was not available


class InvoicePdf:
    """`module_manager` lists the templates and filters the PDF; `Bank` is the
    app's model, passed to file templates that take it."""

    def __init__(self, module_manager, Bank=None):
        self._mm = module_manager
        self._Bank = Bank

    def templates(self):
        """Every template: [{'id', 'name', 'module_id', and 'path' or 'render'}]."""
        return self._mm.get_invoice_templates()

    def blocks(self):
        """The blocks modules add to designer layouts (BaseModule.get_invoice_blocks):
        [{'id', 'label', 'slots', 'render', ..., 'module_id'}]."""
        return self._mm.get_invoice_blocks()

    def _settings(self):
        model = self._mm.core._settings_model
        return model.query.first() if model is not None else None

    def render(self, invoice):
        """The invoice's PDF in the chosen template, as a RenderedPdf."""
        settings = self._settings()
        chosen = (settings.invoice_template if settings and settings.invoice_template
                  else DEFAULT_TEMPLATE)
        listed = {}
        for t in self.templates():      # the first of an id wins
            listed.setdefault(t['id'], t)
        used, pdf = chosen, None
        if chosen in listed:
            pdf = self._build(listed[chosen], invoice, settings)
        if pdf is None:
            used = DEFAULT_TEMPLATE
            default = listed.get(DEFAULT_TEMPLATE) or {
                'id': DEFAULT_TEMPLATE,
                'path': os.path.join(self._mm.app.root_path, 'invoice_templates',
                                     f'{DEFAULT_TEMPLATE}.py')}
            pdf = self._build(default, invoice, settings)
            if pdf is None:
                raise RuntimeError('The default invoice template is missing')
        pdf = self._mm.filter_invoice_pdf(invoice, pdf)
        return RenderedPdf(pdf=pdf, template=used, fallback=used != chosen)

    def _build(self, template, invoice, settings):
        """PDF bytes from one template, or None when it is gone."""
        customer = invoice.customer
        render = template.get('render')
        if callable(render):
            return render(invoice, customer, settings)
        path = template.get('path')
        if not path or not os.path.isfile(path):
            return None
        spec = importlib.util.spec_from_file_location(f"invoice_template_{template['id']}", path)
        code = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(code)
        generate = code.generate_invoice_pdf
        args = (invoice, customer, settings)
        if 'Bank' in inspect.signature(generate).parameters:
            args += (self._Bank,)
        out = generate(*args)
        if hasattr(out, 'getvalue'):
            return out.getvalue()
        return out.read() if hasattr(out, 'read') else bytes(out)
