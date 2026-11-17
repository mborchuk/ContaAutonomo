"""
API vendors of the AI Agent: OpenAI and Anthropic. One question in, the reply
text out. SDKs are imported lazily so a missing optional package only affects
its own vendor. The API key is passed in by the module and never returned.
"""
from .catalog import DEFAULT_MODELS


def describe_error(exc):
    """A message the user can act on for a vendor HTTP error, else None."""
    status = getattr(exc, 'status_code', None)
    if status == 404:
        return ('The selected model is not available for this account. Choose another '
                'model in Settings → AI Agent ("Refresh list" shows the current ones).')
    if status in (401, 403):
        return 'The provider rejected the API key. Check it in Settings → AI Agent.'
    if status == 413:
        return 'The request is too large for the provider.'
    if status == 429:
        return 'The provider rate limit or quota was reached. Try again later.'
    return None


def complete(provider, api_key, model, system, user, max_tokens=1500):
    """The vendor's reply text to `user` under the `system` instruction."""
    if provider == 'openai':
        import openai
        client = openai.OpenAI(api_key=api_key)
        # Current models reject max_tokens and a non-default temperature, and
        # count reasoning tokens toward the limit, hence the headroom.
        response = client.chat.completions.create(
            model=model or DEFAULT_MODELS['openai'],
            messages=[{'role': 'system', 'content': system},
                      {'role': 'user', 'content': user}],
            max_completion_tokens=max_tokens * 4)
        return response.choices[0].message.content or ''
    if provider == 'anthropic':
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model or DEFAULT_MODELS['anthropic'],
            max_tokens=max_tokens, system=system,
            messages=[{'role': 'user', 'content': user}])
        return ''.join(getattr(block, 'text', '') or '' for block in response.content)
    raise ValueError(f'{provider} is not an API vendor')
