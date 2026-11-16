"""
Model catalog for the AI Parser: defaults, a fallback list, and live listing
from the vendor APIs (`GET /v1/models` on both OpenAI and Anthropic).

The fallback list is only what the settings page shows before the first
"Refresh list". The live list is what the API key can actually use.
"""
import json
import re
from datetime import datetime, timezone

DEFAULT_MODELS = {
    'openai': 'gpt-6.1-terra',
    'anthropic': 'claude-sonnet-5-5',
}

# CLI agents (cli_agents.py). Claude Code takes aliases for the latest model
# of a tier and has no list command; Kiro lists its models (`--list-models`).
CLI_DEFAULT_MODELS = {
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

# Models earlier versions offered that the vendor has retired (requests 404).
# Stored settings holding one of these are moved to the default on start-up.
RETIRED_MODELS = {
    'anthropic': {
        'claude-sonnet-4-20250514',    # retired 2026-06-15
        'claude-3-5-sonnet-20241022',  # retired 2025-10-22
        'claude-3-5-haiku-20241022',   # retired 2026-02-19
    },
}

# Providers whose model list can be refreshed, and which of them need a key.
LISTABLE = ('openai', 'anthropic', 'kiro_cli')
KEYED = ('openai', 'anthropic')

# OpenAI lists every model of the account; keep the chat models that read images.
_OPENAI_CHAT = re.compile(r'^(gpt-|chatgpt-|o\d)')
_OPENAI_SKIP = ('audio', 'realtime', 'tts', 'transcribe', 'search', 'image',
                'embedding', 'moderation', 'instruct', 'codex', 'dall-e',
                'whisper', 'sora', 'computer-use')


def list_models(provider, api_key):
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
    """Parsed `models_cache` column: {provider: {'models': [...], 'fetched_at': iso}}."""
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
