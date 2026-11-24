#!/usr/bin/env python3
"""
AI Agent Module — the bridge between the app's modules and AI vendors.

Scope: the one place that talks to AI. It holds the provider choice, the
API keys and the models, and answers text questions through registry
functions; it has no features of its own. A module that needs AI calls
`ai.complete_text` / `ai.text_status` by name (the Ledger Assistant does) and
never sees a key or a vendor. Vendors: OpenAI and Anthropic over their APIs,
Claude Code CLI and Kiro CLI as local programs (cli.py, off unless the server
allows them).

A module from outside this repository that declares the same two functions
replaces this one for them.
"""

from flask import Blueprint, jsonify, request

from functions import Function, FunctionUnavailable, Param
from module_manager import BaseModule
from secret_box import EncryptedString, encrypt_existing
from settings_forms import secret_from_form

from . import catalog, cli, vendors
from .catalog import API_PROVIDERS, DEFAULT_MODELS, DEFAULT_PROVIDER, PROVIDER_NAMES

# Columns taken over once from the AI Parser's settings (see _copy_parser_settings).
PARSER_TABLE = 'ai_parser_config'
COPIED_COLUMNS = ('provider', 'openai_api_key', 'openai_model', 'anthropic_api_key',
                  'anthropic_model', 'claude_cli_model', 'kiro_cli_model', 'models_cache')

CHECK_SYSTEM = 'You check a connection. Reply with the single word OK.'
CHECK_FAILED = 'The provider did not answer. Check the server log.'


class AIAgentModule(BaseModule):
    """Connects modules to AI vendors through registry functions."""

    @property
    def module_id(self):
        return 'ai_agent'

    @property
    def name(self):
        return 'AI Agent'

    @property
    def description(self):
        return ('Connects modules to AI providers (OpenAI, Anthropic, Claude Code CLI, '
                'Kiro CLI). Modules such as the Ledger Assistant ask through it and never '
                'see an API key.')

    @property
    def version(self):
        return '0.1.0'

    @property
    def settings_tab(self):
        return {'id': 'ai_agent', 'label': 'AI Agent'}

    # --- Storage ------------------------------------------------------------ #

    def register_models(self, db):
        self._db = db

        class AIAgentConfig(db.Model):
            __tablename__ = 'ai_agent_config'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            provider = db.Column(db.String(50), default=DEFAULT_PROVIDER)
            openai_api_key = db.Column(EncryptedString(500), default='')
            openai_model = db.Column(db.String(100), default=DEFAULT_MODELS['openai'])
            anthropic_api_key = db.Column(EncryptedString(500), default='')
            anthropic_model = db.Column(db.String(100), default=DEFAULT_MODELS['anthropic'])
            claude_cli_model = db.Column(db.String(100), default=DEFAULT_MODELS['claude_cli'])
            kiro_cli_model = db.Column(db.String(100), default=DEFAULT_MODELS['kiro_cli'])
            # JSON of the last live model listing per provider (catalog.store_cache).
            models_cache = db.Column(db.Text, default='')

        self.AIAgentConfig = AIAgentConfig
        return {'ai_agent_config': AIAgentConfig}

    def _get_config(self):
        """The settings row, created with defaults when missing."""
        cfg = self.AIAgentConfig.query.first()
        if not cfg:
            cfg = self.AIAgentConfig()
            self._db.session.add(cfg)
            self._db.session.commit()
        return cfg

    def on_enable(self):
        self._copy_parser_settings()
        encrypt_existing(self._db, 'ai_agent_config', ['openai_api_key', 'anthropic_api_key'])

    def _copy_parser_settings(self):
        """Once, while this module has no settings: take the provider, models
        and API keys the AI Parser has stored, so the modules that asked
        through the AI Parser keep working. Read with SQL from its table (no
        import of the module); keys are copied as stored, still encrypted.
        Returns True when something was copied."""
        from sqlalchemy import inspect as sa_inspect, text
        try:
            if self.AIAgentConfig.query.first() is not None:
                return False
            inspector = sa_inspect(self._db.engine)
            if PARSER_TABLE not in inspector.get_table_names():
                return False
            present = {c['name'] for c in inspector.get_columns(PARSER_TABLE)}
            columns = [c for c in COPIED_COLUMNS if c in present]
            row = self._db.session.execute(text(
                f'SELECT {", ".join(columns)} FROM {PARSER_TABLE} ORDER BY id LIMIT 1')
            ).mappings().first()
            if row is None:
                return False
            values = {k: v for k, v in row.items() if v is not None}
            if values.get('provider') not in PROVIDER_NAMES:
                # A document-only provider (Google Document AI): pick the API
                # vendor that has a key.
                values['provider'] = ('anthropic' if values.get('anthropic_api_key')
                                      and not values.get('openai_api_key') else DEFAULT_PROVIDER)
            names = ', '.join(values)
            marks = ', '.join(f':{k}' for k in values)
            self._db.session.execute(text(
                f'INSERT INTO ai_agent_config ({names}) VALUES ({marks})'), values)
            self._db.session.commit()
        except Exception as e:  # pragma: no cover - defensive
            self._db.session.rollback()
            self.logger.error('AI Agent: could not copy the AI Parser settings: %s',
                              type(e).__name__)
            return False
        self.logger.info('AI Agent: copied the AI Parser settings (provider %s)',
                         values['provider'])
        self.core.log_activity('ai_agent_settings_copied', 'ai_agent',
                               f'provider={values["provider"]} from the AI Parser')
        return True

    # --- Registry functions ------------------------------------------------- #

    def get_functions(self):
        provider = Param('provider', 'str', 'Provider id (ai.list_providers); default: the '
                         'one selected in Settings → AI Agent', choices=tuple(PROVIDER_NAMES))
        return [
            Function(
                'ai.complete_text', self.complete_text,
                "The AI provider's reply to `user` under the `system` instruction.",
                params=(Param('system', 'str', 'Instruction', required=True, max_length=20_000),
                        Param('user', 'str', 'Message', required=True, max_length=400_000),
                        Param('max_tokens', 'int', 'Reply length limit', minimum=1,
                              maximum=16_000),
                        provider),
                returns='str', external=True),
            Function(
                'ai.text_status', self.text_status,
                'Whether text questions can be answered, by which provider, and what to '
                'configure if not.',
                params=(provider,),
                returns='{"available": bool, "provider": str or null, "reason": str or null}'),
            Function(
                'ai.list_providers', self.list_providers,
                'The AI providers a module may choose from, with whether each can answer '
                'now; for a module that lets the owner pick where its data goes.',
                returns='{"data": [{"id", "name", "kind": "api" | "cli", "model", '
                        '"available": bool, "reason": str or null, "selected": bool}]}'),
        ]

    def _selected(self, provider=None):
        """(provider id, settings row or None): `provider` when given, else the
        one selected in the settings; read only, no row is created."""
        cfg = self.AIAgentConfig.query.first()
        provider = provider or (cfg.provider if cfg else None) or DEFAULT_PROVIDER
        return (provider if provider in PROVIDER_NAMES else DEFAULT_PROVIDER), cfg

    def list_providers(self):
        """Every provider with its model and whether it can answer now."""
        selected, cfg = self._selected()
        out = []
        for p, name in PROVIDER_NAMES.items():
            reason = self._reason(p, cfg)
            out.append({'id': p, 'name': name, 'kind': 'api' if p in API_PROVIDERS else 'cli',
                        'model': self._model(cfg, p), 'available': reason is None,
                        'reason': reason, 'selected': p == selected})
        return {'data': out}

    @staticmethod
    def _model(cfg, provider):
        return (getattr(cfg, f'{provider}_model', None) if cfg else None) \
            or DEFAULT_MODELS[provider]

    def _reason(self, provider, cfg):
        """None when `provider` can answer now, else what to configure."""
        if provider in API_PROVIDERS:
            if not (cfg and getattr(cfg, f'{provider}_api_key')):
                return (f'No API key is stored for {PROVIDER_NAMES[provider]}. '
                        'Add one in Settings → AI Agent.')
            return None
        return cli.AGENTS[provider]().unavailable_reason()

    def text_status(self, provider=None):
        """Whether the selected provider (or `provider`) can answer, and if not, why."""
        provider, cfg = self._selected(provider)
        reason = self._reason(provider, cfg)
        return {'available': reason is None, 'provider': PROVIDER_NAMES[provider],
                'reason': reason}

    def complete_text(self, system, user, max_tokens=1500, provider=None):
        """The reply text of the selected provider, or of `provider` when a
        module lets the owner choose; the API key never leaves this module.
        `max_tokens` is not passed to a CLI agent (neither CLI takes a reply
        limit)."""
        provider, cfg = self._selected(provider)
        reason = self._reason(provider, cfg)
        if reason:
            raise FunctionUnavailable(reason)
        model = self._model(cfg, provider)
        if provider in API_PROVIDERS:
            return vendors.complete(provider, getattr(cfg, f'{provider}_api_key'), model,
                                    system, user, max_tokens)
        return cli.AGENTS[provider](model).complete_text(system, user)

    # --- Routes (settings helpers) ------------------------------------------ #

    def register_routes(self, app):
        bp = Blueprint('ai_agent', __name__, template_folder='templates',
                       url_prefix='/ai-agent')
        login_required = self.core.login_required
        module = self
        from app import limiter

        def _rl(rule):
            def deco(f):
                return limiter.limit(rule)(f) if limiter else f
            return deco

        @bp.route('/models/<provider>/refresh', methods=['POST'])
        @_rl('10/minute')
        @login_required
        def refresh_models(provider):
            """Load the provider's model list into the cache. The key typed in
            the settings form (JSON `api_key`) is used when given, so the list
            can be loaded before saving; it is not stored here."""
            body = request.get_json(silent=True)
            body = body if isinstance(body, dict) else {}
            payload, status = module.refresh_models(
                provider, str(body.get('api_key') or ''), str(body.get('current') or ''))
            return jsonify(payload), status

        @bp.route('/check', methods=['POST'])
        @_rl('5/minute')
        @login_required
        def check_connection():
            """Ask the selected provider one short question."""
            payload, status = module.check_connection()
            return jsonify(payload), status

        app.register_blueprint(bp)

    def refresh_models(self, provider, api_key='', current=''):
        """(catalog.choices payload or {'error'}, HTTP status)."""
        if provider not in catalog.LISTABLE:
            return {'error': 'Unknown provider'}, 404
        # Use the allowlisted constant from here on, not the URL value.
        provider = next(p for p in catalog.LISTABLE if p == provider)
        cfg = self._get_config()
        try:
            if provider in API_PROVIDERS:
                api_key = api_key.strip() or getattr(cfg, f'{provider}_api_key')
                if not api_key:
                    return {'error': 'Enter the API key first.'}, 400
                models = catalog.list_api_models(provider, api_key)
            else:
                agent = cli.AGENTS[provider]()
                reason = agent.unavailable_reason()
                if reason:
                    return {'error': reason}, 400
                models = agent.list_models()
        except Exception as e:
            self.logger.warning('Model listing failed for %s: %s', provider,
                                e if isinstance(e, cli.CLIError) else type(e).__name__)
            return {'error': vendors.describe_error(e)
                    or 'Could not load the model list. Check the server log.'}, 502
        cfg.models_cache = catalog.store_cache(cfg.models_cache, provider, models)
        self._db.session.commit()
        self.logger.info('Model list refreshed for %s: %d models', provider, len(models))
        current = current.strip() or getattr(cfg, f'{provider}_model')
        return catalog.choices(provider, cfg.models_cache, current), 200

    def check_connection(self):
        """({'ok', 'provider', 'reply' | 'error'}, HTTP status) for one short
        question to the saved provider. No exception text reaches the reply."""
        status = self.text_status()
        if not status['available']:
            return {'ok': False, 'provider': status['provider'],
                    'error': status['reason']}, 400
        try:
            reply = self.complete_text(CHECK_SYSTEM, 'Connection check.', 20)
        except Exception as e:
            self.logger.warning('AI Agent connection check failed: %s',
                                e if isinstance(e, cli.CLIError) else type(e).__name__)
            return {'ok': False, 'provider': status['provider'],
                    'error': vendors.describe_error(e) or CHECK_FAILED}, 502
        return {'ok': True, 'provider': status['provider'], 'reply': (reply or '')[:80]}, 200

    # --- Settings ----------------------------------------------------------- #

    def get_settings_html(self, settings):
        cfg = self._get_config()
        models = {p: catalog.choices(p, cfg.models_cache, self._model(cfg, p))
                  for p in PROVIDER_NAMES}
        providers = [{'id': p, 'name': name, 'ready': self._reason(p, cfg) is None}
                     for p, name in PROVIDER_NAMES.items()]
        agents = {p: {'reason': cls().unavailable_reason(), 'binary': cls.binary,
                      'bin_env': cls().bin_env}
                  for p, cls in cli.AGENTS.items()}
        return self.render_settings_template(
            'ai_agent_settings.html', cfg=cfg, providers=providers, models=models,
            agents=agents, cli_allowed=cli.cli_allowed(), allow_env=cli.ALLOW_ENV,
            timeout_env=cli.TIMEOUT_ENV)

    def save_settings(self, settings, form):
        if 'agent_provider' not in form:
            return
        cfg = self._get_config()
        if form.get('agent_provider') in PROVIDER_NAMES:
            cfg.provider = form.get('agent_provider')
        for provider in API_PROVIDERS:
            key = f'{provider}_api_key'
            setattr(cfg, key, secret_from_form(form, f'agent_{key}', getattr(cfg, key)))
        for provider in PROVIDER_NAMES:
            column = f'{provider}_model'
            value = form.get(f'agent_{column}', getattr(cfg, column))
            if provider in cli.AGENTS:
                try:
                    value = cli.valid_model(value)
                except ValueError:
                    self.logger.warning('AI Agent: ignored an invalid %s value', column)
                    continue
            setattr(cfg, column, (value or '').strip() or DEFAULT_MODELS[provider])
        self._db.session.commit()
        self.logger.info('AI Agent settings saved: provider=%s', cfg.provider)
        self.core.log_activity('ai_agent_settings_saved', 'ai_agent',
                               f'provider={cfg.provider}')
