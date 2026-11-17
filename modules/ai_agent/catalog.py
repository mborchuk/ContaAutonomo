"""
AI Agent providers and models: which vendors the agent connects to, their
default models, a fallback list, and live listing (`GET /v1/models` on OpenAI
and Anthropic, `kiro-cli chat --list-models` on Kiro).

The fallback list is only what the settings tab shows before the first
"Refresh list". The live list is what the key or the logged-in CLI account
can actually use.
"""
import json
import re
from datetime import datetime, timezone

# Provider id -> name shown in the settings tab, in display order.
PROVIDER_NAMES = {
    'openai': 'OpenAI (GPT)',
    'anthropic': 'Anthropic (Claude)',
    'claude_cli': 'Claude Code CLI',
    'kiro_cli': 'Kiro CLI',
}
API_PROVIDERS = ('openai', 'anthropic')
CLI_PROVIDERS = ('claude_cli', 'kiro_cli')
DEFAULT_PROVIDER = 'openai'

DEFAULT_MODELS = {
    'openai': 'gpt-6.1-terra',
    'anthropic': 'claude-sonnet-5-5',
    # Claude Code takes aliases for the latest model of a tier and has no
    # list command; Kiro lists its models.
    'claude_cli': 'sonnet',
    'kiro_cli': 'auto',
}

FALLBACK_MODELS = {
    'openai': [
        {'id': 'gpt-6.1-terra', 'name': 'GPT-6.1 Terra'},
        {'id': 'gpt-6.1-sol', 'name': 'GPT-6.1 Sol'},
        {'id': 'gpt-6-astra', 'name': 'GPT-6 Astra'},
        {'id': 'gpt-6-luna', 'name': 'GPT-6 Luna (cheaper)'},
    ],
    'anthropic': [
        {'id': 'claude-sonnet-5-5', 'name': 'Claude Sonnet 5.5'},
        {'id': 'claude-opus-5-5', 'name': 'Claude Opus 5.5'},
        {'id': 'claude-haiku-5-5', 'name': 'Claude Haiku 5.5 (cheaper)'},
        {'id': 'claude-fable-5-2', 'name': 'Claude Fable 5.2'},
    ],
    'claude_cli': [
        {'id': 'sonnet', 'name': 'Sonnet (latest)'},
        {'id': 'opus', 'name': 'Opus (latest)'},
        {'id': 'haiku', 'name': 'Haiku (latest)'},
        {'id': 'fable', 'name': 'Fable (latest)'},
    ],
    'kiro_cli': [
        {'id': 'auto', 'name': 'Auto (Kiro picks the model)'},
    ],
}

# Providers whose model list can be refreshed.
LISTABLE = ('openai', 'anthropic', 'kiro_cli')

# OpenAI lists every model of the account; keep the chat models.
_OPENAI_CHAT = re.compile(r'^(gpt-|chatgpt-|o\d)')
_OPENAI_SKIP = ('audio', 'realtime', 'tts', 'transcribe', 'search', 'image',
                'embedding', 'moderation', 'instruct', 'codex', 'dall-e',
                'whisper', 'sora', 'computer-use')


def list_api_models(provider, api_key):
    """Models the key can use, newest first: [{'id', 'name'}].
    Raises the SDK's exception on an API error."""
    if provider == 'anthropic':
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        return [{'id': m.id, 'name': getattr(m, 'display_name', None) or m.id}
                for m in client.models.list(limit=100)]
    if provider == 'openai':
        import openai
        client = openai.OpenAI(api_key=api_key)
        models = [m for m in client.models.list()
                  if _OPENAI_CHAT.match(m.id) and not any(s in m.id for s in _OPENAI_SKIP)]
        models.sort(key=lambda m: getattr(m, 'created', 0) or 0, reverse=True)
        return [{'id': m.id, 'name': m.id} for m in models]
    raise ValueError(f'Model listing is not supported for {provider}')


def load_cache(raw):
    """Parsed `models_cache` column: {provider: {'models': [...], 'fetched_at': str}}."""
    try:
        data = json.loads(raw or '{}')
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def store_cache(raw, provider, models):
    data = load_cache(raw)
    data[provider] = {'models': models,
                      'fetched_at': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
    return json.dumps(data)


def choices(provider, raw_cache, current):
    """Options for the settings <select>: cached list or the fallback, with the
    stored model kept (and flagged) when the list does not contain it."""
    entry = load_cache(raw_cache).get(provider) or {}
    models = list(entry.get('models') or FALLBACK_MODELS.get(provider, []))
    if current and current not in {m['id'] for m in models}:
        label = f'{current} (not in the list)' if entry.get('models') else current
        models.insert(0, {'id': current, 'name': label})
    return {'models': models, 'fetched_at': entry.get('fetched_at'),
            'live': bool(entry.get('models'))}
