"""AI Parser CLI agents (Claude Code, Kiro CLI). No CLI is ever run:
subprocess.run and the PATH lookup are replaced."""
import json
import os
import subprocess
from types import SimpleNamespace

import pytest
from werkzeug.datastructures import MultiDict

from modules.ai_parser import cli_agents, media, prompts
from modules.ai_parser.cli_agents import CLIError, ClaudeCLIProvider, KiroCLIProvider
from modules.ai_parser.providers import describe_error

PNG = b'\x89PNG\r\n\x1a\n...'
REPLY = {'expense_date': '2026-03-03', 'contractor_name': 'Shop', 'amount': 24.2}


@pytest.fixture
def cli_env(monkeypatch):
    """CLI agents allowed, both binaries "installed", subprocess.run recorded."""
    monkeypatch.setenv(cli_agents.ALLOW_ENV, '1')
    monkeypatch.setattr(cli_agents.shutil, 'which', lambda name: f'/usr/bin/{name}')
    runs = []

    def install(stdout='', returncode=0, stderr='', raises=None):
        def fake_run(args, **kw):
            workdir = kw.get('cwd')
            runs.append({'args': args, 'kw': kw,
                         'files': sorted(os.listdir(workdir)) if workdir else [],
                         'agent': _read_agent(workdir)})
            if raises:
                raise raises
            return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)
        monkeypatch.setattr(cli_agents.subprocess, 'run', fake_run)
        return runs
    return install


def _read_agent(workdir):
    path = os.path.join(workdir or '', '.kiro', 'agents', f'{cli_agents.KIRO_AGENT}.json')
    return json.load(open(path)) if workdir and os.path.exists(path) else None


def _claude_out(result, **extra):
    return json.dumps({'type': 'result', 'is_error': False, 'result': result, **extra})


def test_cli_off_without_the_env_switch(monkeypatch):
    monkeypatch.delenv(cli_agents.ALLOW_ENV, raising=False)
    monkeypatch.setattr(cli_agents.shutil, 'which', lambda name: f'/usr/bin/{name}')
    provider = ClaudeCLIProvider({})
    assert not provider.is_configured()
    assert cli_agents.ALLOW_ENV in provider.unavailable_reason()
    with pytest.raises(CLIError):
        provider.parse_document(PNG, 'a.png', 'expense')


def test_missing_binary(monkeypatch):
    monkeypatch.setenv(cli_agents.ALLOW_ENV, '1')
    monkeypatch.setattr(cli_agents.shutil, 'which', lambda name: None)
    assert '`kiro-cli` is not on the server PATH' in KiroCLIProvider({}).unavailable_reason()


def test_binary_override_comes_from_the_environment(cli_env, monkeypatch):
    seen = []
    monkeypatch.setattr(cli_agents.shutil, 'which', lambda name: seen.append(name) or name)
    monkeypatch.setenv('AI_PARSER_CLAUDE_BIN', '/opt/claude/bin/claude')
    assert ClaudeCLIProvider({}).binary_path() == '/opt/claude/bin/claude'
    assert seen == ['/opt/claude/bin/claude']


def test_claude_command_is_read_only_and_prompt_on_stdin(cli_env):
    runs = cli_env(stdout=_claude_out('```json\n' + json.dumps(REPLY) + '\n```'))
    out = ClaudeCLIProvider({'claude_cli_model': 'opus'}).parse_document(PNG, 'x.pdf', 'expense')
    assert out == REPLY
    run = runs[0]
    args = run['args']
    assert args[:2] == ['/usr/bin/claude', '-p']
    assert args[args.index('--tools') + 1] == 'Read'
    assert args[args.index('--allowedTools') + 1] == 'Read'
    for flag in ('--restricted', '--safe-mode', '--no-session-persistence'):
        assert flag in args
    assert args[args.index('--model') + 1] == 'opus'
    assert not any('Bash' in a for a in args)
    assert run['files'] == ['document.png']           # type from content, not name
    assert run['kw']['input'].startswith(prompts.EXPENSE_PROMPT)
    assert 'document.png' in run['kw']['input'] and 'untrusted data' in run['kw']['input']
    assert run['kw']['timeout'] == cli_agents.DEFAULT_TIMEOUT
    assert 'shell' not in run['kw']


def test_claude_api_error_keeps_the_status(cli_env):
    cli_env(stdout=_claude_out('You have hit your limit', is_error=True, api_error_status=429),
            returncode=1)
    with pytest.raises(CLIError) as err:
        ClaudeCLIProvider({}).parse_document(PNG, 'a.png')
    assert err.value.status_code == 429
    assert 'rate limit' in describe_error(err.value)


def test_claude_non_json_output(cli_env):
    cli_env(stdout='boom', stderr='crashed', returncode=2)
    with pytest.raises(CLIError, match='exit 2'):
        ClaudeCLIProvider({}).parse_document(PNG, 'a.png')


def test_kiro_image_uses_restricted_agent(cli_env):
    runs = cli_env(stdout='Here you go:\n```json\n' + json.dumps(REPLY) + '\n```\nDone {ok}.')
    assert KiroCLIProvider({'kiro_cli_model': 'auto'}).parse_document(PNG, 'a.png', 'expense') == REPLY
    run = runs[0]
    args = run['args']
    assert args[:3] == ['/usr/bin/kiro-cli', 'chat', '--v2']
    assert args[args.index('--agent') + 1] == cli_agents.KIRO_AGENT
    assert '--no-interactive' in args and '--trust-tools=fs_read' in args
    assert '--trust-all-tools' not in args and '-a' not in args
    assert args[-1].startswith(prompts.EXPENSE_PROMPT)
    assert run['agent']['tools'] == ['fs_read'] and run['agent']['mcpServers'] == {}
    assert run['kw']['stdin'] is subprocess.DEVNULL


def test_kiro_pdf_text_goes_in_the_prompt(cli_env, monkeypatch):
    monkeypatch.setattr(cli_agents, 'pdf_text', lambda data: 'TOTAL 24,20 EUR')
    runs = cli_env(stdout=json.dumps(REPLY))
    KiroCLIProvider({}).parse_document(b'%PDF-1.7 ...', 'r.pdf', 'expense')
    prompt = runs[0]['args'][-1]
    assert '<document>\nTOTAL 24,20 EUR\n</document>' in prompt
    assert 'untrusted data' in prompt


def test_kiro_refuses_a_scanned_pdf(cli_env, monkeypatch):
    monkeypatch.setattr(cli_agents, 'pdf_text', lambda data: '')
    runs = cli_env(stdout='{}')
    with pytest.raises(media.UnsupportedFile, match='no readable text layer'):
        KiroCLIProvider({}).parse_document(b'%PDF-1.7 ...', 'r.pdf')
    assert runs == []


def test_kiro_long_pdf_text_is_capped(cli_env, monkeypatch):
    monkeypatch.setattr(cli_agents, 'pdf_text', lambda data: 'é' * 200_000)
    runs = cli_env(stdout=json.dumps(REPLY))
    KiroCLIProvider({}).parse_document(b'%PDF-1.7 ...', 'r.pdf')
    assert len(runs[0]['args'][-1].encode('utf-8')) < 128 * 1024


def test_pdf_text_extracts_a_real_pdf():
    import io
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(50, 750, 'TOTAL 24,20 EUR')
    c.save()
    assert 'TOTAL 24,20 EUR' in cli_agents.pdf_text(buf.getvalue())
    assert cli_agents.pdf_text(b'%PDF-broken') == ''


def _fake_pdf_pages(monkeypatch, *texts):
    import pypdf
    pages = [SimpleNamespace(extract_text=lambda t=t: t) for t in texts]
    monkeypatch.setattr(pypdf, 'PdfReader', lambda stream: SimpleNamespace(pages=pages))


def test_pdf_text_drops_nul_bytes(monkeypatch):
    # Fonts without a Unicode map extract as NUL; NUL cannot go into argv.
    _fake_pdf_pages(monkeypatch, 'Invoice\x00 0006\nTotal\x00 121,00 EUR\x07', 'Page 2\t\x00')
    text = cli_agents.pdf_text(b'%PDF')
    assert text == 'Invoice 0006\nTotal 121,00 EUR\nPage 2'


def test_pdf_text_mostly_unmapped_is_empty(monkeypatch):
    _fake_pdf_pages(monkeypatch, '\x00\x01\x02\x03 12 \x00\x00\x00')
    assert cli_agents.pdf_text(b'%PDF') == ''


def test_kiro_pdf_with_nul_reaches_the_cli_clean(cli_env, monkeypatch):
    _fake_pdf_pages(monkeypatch, 'TOTAL\x00 24,20 EUR')
    runs = cli_env(stdout=json.dumps(REPLY))
    KiroCLIProvider({}).parse_document(b'%PDF-1.7 ...', 'r.pdf', 'expense')
    assert all('\x00' not in arg for arg in runs[0]['args'])
    assert 'TOTAL 24,20 EUR' in runs[0]['args'][-1]


def test_kiro_failure_and_timeout(cli_env):
    cli_env(returncode=1, stderr='\x1b[31mnot logged in\x1b[0m')
    with pytest.raises(CLIError, match='not logged in'):
        KiroCLIProvider({}).parse_document(PNG, 'a.png')
    cli_env(raises=subprocess.TimeoutExpired('kiro-cli', 180))
    with pytest.raises(CLIError, match='did not answer'):
        KiroCLIProvider({}).parse_document(PNG, 'a.png')
    cli_env(raises=FileNotFoundError(2, 'No such file'))
    with pytest.raises(CLIError, match='could not start'):
        KiroCLIProvider({}).parse_document(PNG, 'a.png')
    cli_env(raises=ValueError('embedded null byte'))
    with pytest.raises(CLIError, match='could not start: embedded null byte'):
        KiroCLIProvider({}).parse_document(PNG, 'a.png')


def test_timeout_from_environment(monkeypatch):
    monkeypatch.setenv(cli_agents.TIMEOUT_ENV, '45')
    assert cli_agents._timeout() == 45
    monkeypatch.setenv(cli_agents.TIMEOUT_ENV, 'x')
    assert cli_agents._timeout() == cli_agents.DEFAULT_TIMEOUT


@pytest.mark.parametrize('model', ['--trust-all-tools', '-a', 'a b', 'x;rm', 'm' * 101])
def test_model_names_cannot_inject_flags(model):
    with pytest.raises(ValueError):
        cli_agents.valid_model(model)


@pytest.mark.parametrize('model', ['', 'sonnet', 'claude-opus-5.5', 'claude-sonnet-5-5[1m]'])
def test_valid_model_names(model):
    assert cli_agents.valid_model(model) == model


def test_kiro_list_models(cli_env):
    runs = cli_env(stdout=json.dumps({'models': [
        {'model_id': 'auto', 'model_name': 'auto', 'rate_multiplier': 1.0},
        {'model_id': 'claude-sonnet-5.5', 'model_name': 'claude-sonnet-5.5'}]}))
    assert KiroCLIProvider({}).list_models() == [
        {'id': 'auto', 'name': 'auto (1.0x credits)'},
        {'id': 'claude-sonnet-5.5', 'name': 'claude-sonnet-5.5'}]
    assert runs[0]['args'][1:] == ['chat', '--list-models', '-f', 'json']
    cli_env(stdout='not json')
    with pytest.raises(CLIError):
        KiroCLIProvider({}).list_models()


# --- module integration ---------------------------------------------------------

@pytest.fixture
def ai_module(standalone_module):
    return standalone_module('ai_parser')


def test_module_uses_a_cli_provider(ai_module, cli_env):
    from app import db
    cfg = ai_module._get_config()
    cfg.provider = 'kiro_cli'
    db.session.commit()
    provider = ai_module._get_provider()
    assert provider.name == 'kiro_cli' and provider.model == 'auto'
    assert ai_module.provider_settings()['configured']
    cfg.provider = 'openai'
    db.session.commit()


def test_module_explains_an_unavailable_cli(ai_module, monkeypatch):
    from app import db
    monkeypatch.delenv(cli_agents.ALLOW_ENV, raising=False)
    cfg = ai_module._get_config()
    cfg.provider = 'claude_cli'
    db.session.commit()
    assert ai_module._get_provider() is None
    assert cli_agents.ALLOW_ENV in ai_module._not_configured_message()
    cfg.provider = 'openai'
    db.session.commit()


def test_refresh_kiro_models_needs_no_key(ai_module, cli_env):
    cli_env(stdout=json.dumps({'models': [{'model_id': 'auto', 'model_name': 'auto'}]}))
    payload, status = ai_module.refresh_models('kiro_cli')
    assert status == 200 and payload['models'][0]['id'] == 'auto'
    from app import db
    ai_module._get_config().models_cache = ''
    db.session.commit()


def test_refresh_kiro_models_when_cli_is_off(ai_module, monkeypatch):
    monkeypatch.delenv(cli_agents.ALLOW_ENV, raising=False)
    payload, status = ai_module.refresh_models('kiro_cli')
    assert status == 400 and cli_agents.ALLOW_ENV in payload['error']


def test_scanned_pdf_gets_its_own_422_message():
    from modules.ai_parser.index import rejection_message
    assert rejection_message(cli_agents.ScannedPDF('x')) == cli_agents.SCANNED_PDF_MESSAGE
    assert rejection_message(media.UnsupportedFile('x')) == media.UNSUPPORTED_MESSAGE


def test_save_settings_validates_cli_models_and_provider(ai_module, app):
    cfg = ai_module._get_config()
    ai_module.save_settings(None, MultiDict({'ai_provider': 'claude_cli',
                                             'ai_claude_cli_model': 'opus',
                                             'ai_kiro_cli_model': '--trust-all-tools'}))
    assert cfg.provider == 'claude_cli' and cfg.claude_cli_model == 'opus'
    assert cfg.kiro_cli_model == 'auto'
    ai_module.save_settings(None, MultiDict({'ai_provider': 'no-such-provider'}))
    assert cfg.provider == 'claude_cli'
    ai_module.save_settings(None, MultiDict({'ai_provider': 'openai', 'ai_claude_cli_model': ''}))
    assert cfg.provider == 'openai' and cfg.claude_cli_model == 'sonnet'


def test_settings_panel_shows_cli_agents(ai_module, app, monkeypatch):
    monkeypatch.delenv(cli_agents.ALLOW_ENV, raising=False)
    with app.test_request_context('/settings'):
        html = ai_module.get_settings_html(None)
    assert 'Claude Code CLI' in html and 'Kiro CLI' in html
    assert 'value="claude_cli"' in html and 'value="kiro_cli"' in html
    assert 'Not available: CLI agents are off' in html
