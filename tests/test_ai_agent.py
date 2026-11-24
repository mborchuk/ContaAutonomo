"""AI Agent: the bridge between modules and AI vendors. It declares
ai.complete_text and ai.text_status, answers with the selected provider,
keeps the key to itself, and takes over the AI Parser's settings once.
Every SDK is a stub and subprocess.run a recorder: no provider is called and
no CLI runs."""
import json
import os
import sys
from types import ModuleType, SimpleNamespace

import pytest
from werkzeug.datastructures import MultiDict

import secret_box
from functions import FunctionRegistry, FunctionUnavailable
from modules.ai_agent import catalog, cli, vendors

SECRET = 'sk-synthetic-agent-key'


@pytest.fixture
def agent(standalone_module, app):
    mod = standalone_module('ai_agent')
    _clear(mod.AIAgentConfig)
    yield mod
    _clear(mod.AIAgentConfig)


def _clear(model):
    """Delete every row and forget the loaded objects, so a new row with the
    same id does not meet a stale one in the session."""
    from app import db
    model.query.delete()
    db.session.commit()
    db.session.expunge_all()


def _configure(mod, **values):
    from app import db
    cfg = mod._get_config()
    for key, value in values.items():
        setattr(cfg, key, value)
    db.session.commit()
    return cfg


@pytest.fixture
def sdks(monkeypatch):
    """Stub openai and anthropic SDKs that record each request."""
    calls = []
    openai = ModuleType('openai')

    class OpenAIClient:
        def __init__(self, api_key):
            self.api_key = api_key
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        def _create(self, **kw):
            calls.append(('openai', self.api_key, kw))
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content='openai reply'))])
    openai.OpenAI = OpenAIClient
    anthropic = ModuleType('anthropic')

    class AnthropicClient:
        def __init__(self, api_key):
            self.api_key = api_key
            self.messages = SimpleNamespace(create=self._create)

        def _create(self, **kw):
            calls.append(('anthropic', self.api_key, kw))
            return SimpleNamespace(content=[SimpleNamespace(text='anthropic '),
                                            SimpleNamespace(text='reply')])
    anthropic.Anthropic = AnthropicClient
    monkeypatch.setitem(sys.modules, 'openai', openai)
    monkeypatch.setitem(sys.modules, 'anthropic', anthropic)
    return calls


@pytest.fixture
def cli_env(monkeypatch):
    """CLI agents allowed, both binaries "installed", subprocess.run recorded."""
    for name in ('AI_AGENT_ALLOW_CLI', 'AI_PARSER_ALLOW_CLI', 'AI_AGENT_CLAUDE_BIN',
                 'AI_PARSER_CLAUDE_BIN', 'AI_AGENT_KIRO_BIN', 'AI_PARSER_KIRO_BIN'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(cli.ALLOW_ENV, '1')
    monkeypatch.setattr(cli.shutil, 'which', lambda name: f'/usr/bin/{name}')
    runs = []

    def install(stdout='', returncode=0, stderr=''):
        def fake_run(args, **kw):
            agent_file = os.path.join(kw['cwd'], '.kiro', 'agents', f'{cli.KIRO_AGENT}.json')
            agent = None
            if os.path.exists(agent_file):
                with open(agent_file) as fh:
                    agent = json.load(fh)
            runs.append({'args': args, 'kw': kw, 'agent': agent,
                         'files': sorted(os.listdir(kw['cwd']))})
            return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)
        monkeypatch.setattr(cli.subprocess, 'run', fake_run)
        return runs
    return install


# --- the module and its functions ---------------------------------------------- #

def test_module_is_off_by_default_and_declares_only_the_text_functions(agent):
    assert getattr(type(agent), 'enabled_by_default', False) is False
    registry = FunctionRegistry(SimpleNamespace(modules={'ai_agent': agent}))
    assert registry.check_all() == ([], [])
    assert {f.name for f in registry.find()} == {'ai.complete_text', 'ai.text_status',
                                                 'ai.list_providers'}
    assert registry.get('ai.list_providers').external is False
    assert registry.get('ai.complete_text').external is True
    assert registry.get('ai.text_status').external is False


def test_api_provider_answers_with_its_stored_key(agent, sdks):
    status = agent.text_status()
    assert status == {'available': False, 'provider': 'OpenAI (GPT)',
                      'reason': 'No API key is stored for OpenAI (GPT). Add one in '
                                'Settings → AI Agent.'}
    with pytest.raises(FunctionUnavailable):
        agent.complete_text('sys', 'q')
    assert sdks == []

    _configure(agent, openai_api_key=SECRET, openai_model='gpt-test')
    assert agent.text_status()['available'] is True
    assert agent.complete_text('Be brief.', 'Hello?', 100) == 'openai reply'
    provider, key, kw = sdks[-1]
    assert (provider, key, kw['model']) == ('openai', SECRET, 'gpt-test')
    assert kw['messages'] == [{'role': 'system', 'content': 'Be brief.'},
                              {'role': 'user', 'content': 'Hello?'}]
    assert kw['max_completion_tokens'] == 400 and 'max_tokens' not in kw

    _configure(agent, provider='anthropic', anthropic_api_key=SECRET + '-2')
    assert agent.complete_text('Be brief.', 'Hello?', 100) == 'anthropic reply'
    provider, key, kw = sdks[-1]
    assert (provider, key) == ('anthropic', SECRET + '-2')
    assert kw['system'] == 'Be brief.' and kw['max_tokens'] == 100
    assert kw['model'] == catalog.DEFAULT_MODELS['anthropic']


def test_a_module_may_choose_the_provider(agent, sdks):
    _configure(agent, provider='openai', openai_api_key=SECRET,
               anthropic_api_key=SECRET + '-2', anthropic_model='claude-test')
    registry = FunctionRegistry(SimpleNamespace(modules={'ai_agent': agent}))
    assert registry.call('ai.complete_text', system='s', user='u',
                         provider='anthropic') == 'anthropic reply'
    assert sdks[-1][:2] == ('anthropic', SECRET + '-2')
    assert registry.call('ai.complete_text', system='s', user='u') == 'openai reply'
    assert registry.call('ai.text_status', provider='kiro_cli')['provider'] == 'Kiro CLI'
    assert registry.problem_with('ai.complete_text', {'system': 's', 'user': 'u',
                                                      'provider': 'other'}).startswith(
        'provider must be one of')


def test_list_providers_says_which_can_answer(agent, cli_env, monkeypatch):
    monkeypatch.delenv(cli.ALLOW_ENV, raising=False)
    _configure(agent, provider='anthropic', anthropic_api_key=SECRET, anthropic_model='claude-test')
    listed = {p['id']: p for p in agent.list_providers()['data']}
    assert list(listed) == list(catalog.PROVIDER_NAMES)
    assert listed['anthropic'] == {'id': 'anthropic', 'name': 'Anthropic (Claude)', 'kind': 'api',
                                   'model': 'claude-test', 'available': True, 'reason': None,
                                   'selected': True}
    assert listed['openai']['available'] is False and 'No API key' in listed['openai']['reason']
    assert listed['kiro_cli']['kind'] == 'cli' and listed['kiro_cli']['available'] is False
    assert SECRET not in json.dumps(listed)


def test_calls_through_the_registry_never_return_the_key(agent, sdks):
    _configure(agent, openai_api_key=SECRET)
    registry = FunctionRegistry(SimpleNamespace(modules={'ai_agent': agent}))
    assert SECRET not in json.dumps(registry.call('ai.text_status'))
    assert registry.call('ai.complete_text', system='s', user='u') == 'openai reply'


def test_vendor_errors_are_explained_without_details():
    assert 'Settings → AI Agent' in vendors.describe_error(SimpleNamespace(status_code=401))
    assert 'Refresh list' in vendors.describe_error(SimpleNamespace(status_code=404))
    assert vendors.describe_error(RuntimeError('boom')) is None


# --- CLI agents ---------------------------------------------------------------- #

def test_claude_cli_has_no_tools_and_reads_the_message_on_stdin(agent, cli_env):
    runs = cli_env(stdout=json.dumps({'type': 'result', 'is_error': False, 'result': ' PONG '}))
    _configure(agent, provider='claude_cli', claude_cli_model='opus')
    assert agent.text_status()['available'] is True
    assert agent.complete_text('Say PONG.', 'Now.', 50) == 'PONG'
    args, run = runs[0]['args'], runs[0]
    assert args[:2] == ['/usr/bin/claude', '-p']
    assert args[args.index('--tools') + 1] == ''                  # no tool at all
    assert args[args.index('--system-prompt') + 1] == 'Say PONG.'
    assert args[args.index('--model') + 1] == 'opus'
    for flag in ('--restricted', '--safe-mode', '--no-session-persistence'):
        assert flag in args
    assert run['kw']['input'] == 'Now.' and run['files'] == []    # empty directory


def test_kiro_cli_uses_an_agent_without_tools(agent, cli_env):
    runs = cli_env(stdout='\x1b[0mPONG\n')
    _configure(agent, provider='kiro_cli')
    assert agent.complete_text('Say PONG.', 'Now.') == 'PONG'
    run = runs[0]
    assert run['args'][:3] == ['/usr/bin/kiro-cli', 'chat', '--v2']
    assert run['args'][run['args'].index('--agent') + 1] == cli.KIRO_AGENT
    assert run['args'][run['args'].index('--model') + 1] == 'auto'
    assert not any(a.startswith('--trust') for a in run['args'])
    assert run['agent']['tools'] == [] and run['agent']['allowedTools'] == []
    assert run['agent']['mcpServers'] == {}
    assert run['kw']['input'] == 'Say PONG.\n\n---\n\nNow.'


def test_cli_agents_are_off_unless_the_server_allows_them(agent, cli_env, monkeypatch):
    runs = cli_env(stdout='x')
    _configure(agent, provider='kiro_cli')
    monkeypatch.delenv(cli.ALLOW_ENV)
    status = agent.text_status()
    assert status['available'] is False and cli.ALLOW_ENV in status['reason']
    with pytest.raises(FunctionUnavailable):
        agent.complete_text('s', 'u')
    # The AI Parser's switch counts when the AI Agent's is not set ...
    monkeypatch.setenv('AI_PARSER_ALLOW_CLI', '1')
    assert agent.text_status()['available'] is True
    # ... and the AI Agent's own setting wins over it.
    monkeypatch.setenv(cli.ALLOW_ENV, '0')
    assert agent.text_status()['available'] is False
    assert runs == []


def test_cli_failures_raise_cli_errors(cli_env):
    cli_env(returncode=1, stderr='not logged in')
    with pytest.raises(cli.CLIError, match='not logged in'):
        cli.KiroCLI().complete_text('s', 'u')
    cli_env(stdout=json.dumps({'is_error': True, 'result': 'quota', 'api_error_status': 429}))
    with pytest.raises(cli.CLIError) as err:
        cli.ClaudeCLI().complete_text('s', 'u')
    assert 'rate limit' in vendors.describe_error(err.value)


def test_cli_binary_from_the_environment(cli_env, monkeypatch):
    runs = cli_env(stdout=json.dumps({'result': 'ok'}))
    monkeypatch.setattr(cli.shutil, 'which', lambda name: name if name.startswith('/') else None)
    assert 'not on the server PATH' in cli.ClaudeCLI().unavailable_reason()
    monkeypatch.setenv('AI_AGENT_CLAUDE_BIN', '/opt/claude/bin/claude')
    assert cli.ClaudeCLI().complete_text('s', 'u') == 'ok'
    assert runs[0]['args'][0] == '/opt/claude/bin/claude'


# --- taking over the AI Parser's settings ---------------------------------------- #

@pytest.fixture
def parser_cfg(standalone_module, app):
    from app import db
    parser = standalone_module('ai_parser')
    _clear(parser.AIParserConfig)

    def make(**values):
        cfg = parser._get_config()
        for key, value in values.items():
            setattr(cfg, key, value)
        db.session.commit()
        return cfg
    yield make
    _clear(parser.AIParserConfig)


def _raw(sql):
    from sqlalchemy import text
    from app import db
    return db.session.execute(text(sql)).scalar()


def test_parser_settings_are_copied_once(agent, parser_cfg):
    from app import db
    parser_cfg(provider='kiro_cli', openai_api_key=SECRET, openai_model='gpt-6-luna',
               anthropic_api_key='', kiro_cli_model='claude-sonnet-5-5')
    stored = _raw('SELECT openai_api_key FROM ai_parser_config')
    assert agent._copy_parser_settings() is True
    db.session.expire_all()
    cfg = agent.AIAgentConfig.query.one()
    assert (cfg.provider, cfg.openai_api_key, cfg.openai_model, cfg.kiro_cli_model) == (
        'kiro_cli', SECRET, 'gpt-6-luna', 'claude-sonnet-5-5')
    raw = _raw('SELECT openai_api_key FROM ai_agent_config')
    assert raw == stored and raw.startswith(secret_box.PREFIX) and SECRET not in raw

    # Later changes in either module stay separate: no second copy.
    parser_cfg(provider='openai')
    assert agent._copy_parser_settings() is False
    db.session.expire_all()
    assert agent.AIAgentConfig.query.one().provider == 'kiro_cli'


def test_a_document_only_parser_provider_becomes_the_vendor_with_a_key(agent, parser_cfg):
    parser_cfg(provider='google_docai', openai_api_key='', anthropic_api_key=SECRET)
    assert agent._copy_parser_settings() is True
    assert agent.AIAgentConfig.query.one().provider == 'anthropic'


def test_nothing_to_copy_without_parser_settings(agent, parser_cfg):
    assert agent._copy_parser_settings() is False
    assert agent.AIAgentConfig.query.count() == 0


def test_on_enable_copies_then_encrypts(agent, parser_cfg):
    from sqlalchemy import text
    from app import db
    parser_cfg(provider='openai')
    # A key stored in plaintext by an old version is encrypted once copied.
    db.session.execute(text("UPDATE ai_parser_config SET openai_api_key = :k"), {'k': SECRET})
    db.session.commit()
    agent.on_enable()
    raw = _raw('SELECT openai_api_key FROM ai_agent_config')
    assert raw.startswith(secret_box.PREFIX) and SECRET not in raw
    db.session.expire_all()
    assert agent._get_config().openai_api_key == SECRET


# --- settings, model lists, connection check ------------------------------------- #

def test_save_settings(agent):
    cfg = _configure(agent, openai_api_key=SECRET)
    agent.save_settings(None, MultiDict({'agent_provider': 'claude_cli',
                                         'agent_openai_api_key': '',
                                         'agent_claude_cli_model': 'opus',
                                         'agent_kiro_cli_model': '--trust-all-tools',
                                         'agent_anthropic_model': ''}))
    assert cfg.provider == 'claude_cli' and cfg.claude_cli_model == 'opus'
    assert cfg.openai_api_key == SECRET                        # blank keeps the key
    assert cfg.kiro_cli_model == 'auto'                        # a flag is refused
    assert cfg.anthropic_model == catalog.DEFAULT_MODELS['anthropic']
    agent.save_settings(None, MultiDict({'agent_provider': 'no-such-provider',
                                         'agent_openai_api_key__clear': '1'}))
    assert cfg.provider == 'claude_cli' and cfg.openai_api_key == ''
    agent.save_settings(None, MultiDict({'ai_provider': 'openai'}))   # the parser's form
    assert cfg.provider == 'claude_cli'


def test_settings_panel_hides_the_key_and_escapes(agent, app, monkeypatch):
    monkeypatch.delenv(cli.ALLOW_ENV, raising=False)
    monkeypatch.delenv('AI_PARSER_ALLOW_CLI', raising=False)
    _configure(agent, openai_api_key=SECRET, openai_model='<script>x</script>')
    with app.test_request_context('/settings'):
        html = agent.get_settings_html(None)
    assert SECRET not in html and '<script>x</script>' not in html
    assert 'name="agent_provider"' in html and 'value="kiro_cli"' in html
    assert 'Not available: CLI agents are off' in html
    assert '/ai-agent/check' in html


def test_refresh_models(agent, sdks, monkeypatch):
    assert agent.refresh_models('google_docai')[1] == 404
    assert agent.refresh_models('openai') == ({'error': 'Enter the API key first.'}, 400)
    monkeypatch.setattr(catalog, 'list_api_models',
                        lambda provider, key: [{'id': f'{provider}-m1', 'name': key[-3:]}])
    payload, status = agent.refresh_models('anthropic', api_key=SECRET)
    assert status == 200 and payload['live']
    # The stored model is kept, flagged, when the account's list lacks it.
    assert [m['id'] for m in payload['models']] == ['claude-sonnet-5-5', 'anthropic-m1']
    assert payload['models'][0]['name'].endswith('(not in the list)')
    assert 'anthropic-m1' in agent._get_config().models_cache

    def failing(provider, key):
        raise RuntimeError('internal detail')
    monkeypatch.setattr(catalog, 'list_api_models', failing)
    payload, status = agent.refresh_models('anthropic', api_key=SECRET)
    assert status == 502 and 'internal detail' not in payload['error']


def test_check_connection(agent, sdks, monkeypatch):
    payload, status = agent.check_connection()
    assert status == 400 and 'No API key' in payload['error']
    _configure(agent, openai_api_key=SECRET)
    assert agent.check_connection() == (
        {'ok': True, 'provider': 'OpenAI (GPT)', 'reply': 'openai reply'}, 200)

    def failing(*args, **kw):
        raise RuntimeError('internal detail')
    monkeypatch.setattr(vendors, 'complete', failing)
    payload, status = agent.check_connection()
    assert status == 502 and payload['ok'] is False and 'internal detail' not in payload['error']


def test_routes_require_login(loaded_modules, client, agent, monkeypatch):
    assert client.post('/ai-agent/check').status_code in (302, 401)


# --- the Ledger Assistant asks through the AI Agent -------------------------------- #

def test_ledger_assistant_asks_through_the_agent(agent, loaded_modules, monkeypatch):
    """The assistant names no module: with the AI Agent enabled its text
    questions go to the agent's provider."""
    la = loaded_modules.modules['ledger_assistant']
    assert la.missing() is not None and 'AI Agent' in la.missing()
    monkeypatch.setitem(loaded_modules.modules, 'ai_agent', agent)
    _configure(agent, openai_api_key=SECRET)
    seen = []
    monkeypatch.setattr(vendors, 'complete', lambda provider, key, model, system, user,
                        max_tokens=1500: seen.append((provider, key, user)) or '{"calls": []}')
    assert la.missing() is None
    out = la.ask('How much did I invoice in 2035?')
    assert seen and seen[0][:2] == ('openai', SECRET)
    assert out['answer'] and out['calls'] == []
