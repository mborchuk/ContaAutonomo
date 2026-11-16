#!/usr/bin/env python3
"""
Text completion with the provider configured in the AI Parser.

Only OpenAI and Anthropic can answer questions; Google Document AI reads
documents and cannot. The SDKs are imported lazily, as in the AI Parser.
The API key is passed to the SDK and never logged.
"""


class ProviderUnavailable(Exception):
    """No usable provider; the message says what is missing."""


TEXT_PROVIDERS = ('openai', 'anthropic')


def missing_reason(settings):
    """None when `settings` (ai_parser.provider_settings()) can answer, else a
    sentence saying what to configure."""
    if settings is None:
        return ('The AI Parser module is not enabled. Enable it in Settings → Modules '
                'and choose OpenAI or Anthropic with an API key in Settings → AI Parser.')
    provider = settings.get('provider')
    if provider not in TEXT_PROVIDERS:
        return (f'The AI Parser uses {settings.get("display_name") or provider}, which '
                'reads documents but cannot answer questions. Choose OpenAI or '
                'Anthropic in Settings → AI Parser.')
    if not settings.get('api_key'):
        return (f'No API key is stored for {settings.get("display_name") or provider}. '
                'Add one in Settings → AI Parser.')
    return None


def complete(settings, system, user, max_tokens=1500):
    """The provider's reply text to `user` under the `system` instruction."""
    reason = missing_reason(settings)
    if reason:
        raise ProviderUnavailable(reason)
    from modules.ai_parser.catalog import DEFAULT_MODELS
    if settings['provider'] == 'openai':
        import openai
        client = openai.OpenAI(api_key=settings['api_key'])
        # Current models reject max_tokens and a non-default temperature, and
        # count reasoning tokens toward the limit, hence the headroom.
        response = client.chat.completions.create(
            model=settings.get('model') or DEFAULT_MODELS['openai'],
            messages=[{'role': 'system', 'content': system},
                      {'role': 'user', 'content': user}],
            max_completion_tokens=max_tokens * 4)
        return response.choices[0].message.content or ''
    import anthropic
    client = anthropic.Anthropic(api_key=settings['api_key'])
    response = client.messages.create(
        model=settings.get('model') or DEFAULT_MODELS['anthropic'],
        max_tokens=max_tokens, system=system,
        messages=[{'role': 'user', 'content': user}])
    return response.content[0].text if response.content else ''
