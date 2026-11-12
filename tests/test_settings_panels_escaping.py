"""C-1 — module settings panels are auto-escaped and never echo stored secrets."""
import pytest
from werkzeug.datastructures import MultiDict

from settings_forms import secret_from_form

XSS = '"><script>alert(1)</script>'
SECRET = 'S3cr3t-value-that-must-not-leak'


@pytest.fixture
def request_ctx(app):
    with app.test_request_context('/settings'):
        yield


@pytest.fixture
def standalone(loaded_modules):
    """Instantiate a module that the test session does not enable, without
    registering its hooks, so other tests are not affected."""
    from app import db
    made = {}

    def make(module_id):
        if module_id in loaded_modules.modules:
            return loaded_modules.modules[module_id]
        if module_id not in made:
            inst = loaded_modules.discovered[module_id](loaded_modules.core)
            inst.register_models(db)
            db.create_all()
            made[module_id] = inst
        return made[module_id]
    return make


def _assert_safe(html, raw_values=(XSS,)):
    assert '<script>alert(1)</script>' not in html
    for value in raw_values:
        assert value not in html


# --- secret_from_form ---------------------------------------------------------

def test_secret_from_form_keeps_replaces_and_clears():
    assert secret_from_form(MultiDict({'pw': ''}), 'pw', 'old') == 'old'
    assert secret_from_form(MultiDict({'pw': '  '}), 'pw', 'old') == 'old'
    assert secret_from_form(MultiDict({'pw': 'new'}), 'pw', 'old') == 'new'
    assert secret_from_form(MultiDict({'pw': '', 'pw__clear': '1'}), 'pw', 'old') == ''


# --- escaping -------------------------------------------------------------------

def test_invoice_email_panel_escapes_and_hides_password(loaded_modules, request_ctx):
    mod = loaded_modules.modules['invoice_email']
    cfg = mod._smtp_config()
    cfg.update(host=XSS, password=SECRET, from_addr='a@example.com')
    mod._save_smtp_config(cfg)
    html = mod.get_settings_html(None)
    _assert_safe(html, (XSS, SECRET))
    assert '&#34;&gt;&lt;script&gt;' in html
    assert 'Stored — leave blank to keep' in html

    mod.save_settings(None, MultiDict({'invoice_email_submitted': '1', 'smtp_host': 'h',
                                       'smtp_from': 'a@example.com', 'smtp_password': ''}))
    assert mod._smtp_config()['password'] == SECRET


def test_backup_panel_escapes_path_and_hides_password(loaded_modules, request_ctx):
    from app import Settings, db
    mod = loaded_modules.modules['backup']
    cfg = mod._get_config()
    cfg.backup_path = XSS
    cfg.encrypt_method = 'custom'
    cfg.custom_password = SECRET
    db.session.commit()
    html = mod.get_settings_html(Settings.query.first())
    _assert_safe(html, (XSS, SECRET))

    if not Settings.query.first():
        db.session.add(Settings())
        db.session.commit()
    mod.save_settings(Settings.query.first(), MultiDict({
        'bk_encrypt_method': 'custom', 'bk_backup_path': '', 'bk_custom_password': ''}))
    assert mod._get_config().custom_password == SECRET
    cfg = mod._get_config()
    cfg.encrypt_method = 'key'
    cfg.custom_password = ''
    cfg.backup_path = ''
    db.session.commit()


def test_irpf_panel_escapes_categories(loaded_modules, request_ctx):
    from app import db
    mod = loaded_modules.modules['tax_es_irpf']
    profile = mod._get_profile()
    profile.equipment_categories = XSS
    db.session.commit()
    _assert_safe(mod.get_settings_html(None))
    profile.equipment_categories = 'Equipment'
    db.session.commit()


def test_expenses_panel_escapes_textarea(loaded_modules, request_ctx):
    import json
    from app import db
    mod = loaded_modules.modules['expenses']
    mod.save_settings(None, MultiDict({'expense_category_defaults': json.dumps(
        {'</textarea><script>alert(1)</script>': {'vat_rate': 21}})}))
    html = mod.get_settings_html(None)
    assert '<script>alert(1)</script>' not in html
    row = mod.ExpensesConfig.query.filter_by(key='category_defaults').first()
    db.session.delete(row)
    db.session.commit()


def test_external_storage_panel_hides_keys(standalone, request_ctx):
    from app import db
    mod = standalone('external_storage')
    cfg = mod._get_config()
    cfg.s3_bucket = XSS
    cfg.s3_auth_method = 'keys'
    cfg.s3_access_key = 'AKIA' + SECRET
    cfg.s3_secret_key = SECRET
    db.session.commit()
    html = mod.get_settings_html(None)
    _assert_safe(html, (XSS, SECRET, 'AKIA' + SECRET))

    mod.save_settings(None, MultiDict({'es_storage_type': 'local', 'es_s3_auth_method': 'keys',
                                       'es_s3_access_key': '', 'es_s3_secret_key': ''}))
    assert cfg.s3_secret_key == SECRET and cfg.s3_access_key == 'AKIA' + SECRET


def test_ai_parser_panel_hides_api_keys(standalone, request_ctx):
    from app import db
    mod = standalone('ai_parser')
    cfg = mod._get_config()
    cfg.openai_api_key = SECRET
    cfg.anthropic_api_key = SECRET + '-2'
    cfg.google_project_id = XSS
    db.session.commit()
    html = mod.get_settings_html(None)
    _assert_safe(html, (XSS, SECRET))

    mod.save_settings(None, MultiDict({'ai_provider': 'openai', 'ai_openai_api_key': '',
                                       'ai_anthropic_api_key': '',
                                       'ai_anthropic_api_key__clear': '1'}))
    assert cfg.openai_api_key == SECRET
    assert cfg.anthropic_api_key == ''


def test_pdf_signature_panel_hides_certificate_password(standalone, request_ctx):
    from app import db
    mod = standalone('pdf_signature')
    cfg = mod._get_config()
    cfg.certificate_password = SECRET
    db.session.commit()
    _assert_safe(mod.get_settings_html(None), (SECRET,))


def test_tax_poland_and_simple_panels_render(standalone, loaded_modules, request_ctx):
    from app import Settings
    html = standalone('tax_poland').get_settings_html(None)
    assert 'tax_pl_zus' in html
    assert 'fiscal_forms' in loaded_modules.modules['fiscal_calendar'].get_settings_html(None)
    assert 'social_security_monthly' in loaded_modules.modules['tax_management'].get_settings_html(
        Settings.query.first())


def test_api_token_shown_once_then_masked(standalone, app):
    from sqlalchemy import inspect, text
    from app import Settings, db
    mod = standalone('api')
    if 'api_token' not in [c['name'] for c in inspect(db.engine).get_columns('settings')]:
        db.session.execute(text('ALTER TABLE settings ADD COLUMN api_token VARCHAR(64)'))
        db.session.commit()
    if not Settings.query.first():
        db.session.add(Settings())
        db.session.commit()
    mod._set_token(SECRET)
    with app.test_request_context('/settings'):
        html = mod.get_settings_html(None)
        assert SECRET not in html and SECRET[-4:] in html
    with app.test_request_context('/settings'):
        from flask import session
        session['api_token_reveal'] = True
        assert SECRET in mod.get_settings_html(None)      # once, after rotation
        assert SECRET not in mod.get_settings_html(None)  # then masked


# --- core settings page ---------------------------------------------------------

def test_settings_page_never_contains_currency_api_key(loaded_modules, client):
    from app import Settings, db
    s = Settings.query.first()
    if not s:
        s = Settings()
        db.session.add(s)
    s.currency_provider = 'fixer'
    s.currency_provider_api_key = SECRET
    db.session.commit()
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    html = client.get('/settings').get_data(as_text=True)
    assert SECRET not in html

    client.post('/settings', data={'active_tab': 'general', 'currency_provider': 'fixer',
                                   'currency_provider_api_key': ''})
    db.session.refresh(s)
    assert s.currency_provider_api_key == SECRET
    s.currency_provider = 'ecb'
    s.currency_provider_api_key = ''
    db.session.commit()
