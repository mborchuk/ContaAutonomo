"""
Text completion with an API provider for the `ai.complete_text` and
`ai.text_status` registry functions: OpenAI and Anthropic. The CLI agents
answer text through `cli_agents.CLIAgentProvider.complete_text`; Google
Document AI reads documents only. The API key stays in this module: callers
get the reply text, never the key.
"""
from .catalog import DEFAULT_MODELS

TEXT_PROVIDERS = ('openai', 'anthropic')


def missing_reason(settings):
    """None when `settings` (AIParserModule.provider_settings()) can answer
    text, else a sentence saying what to configure."""
    provider = settings.get('provider')
    name = settings.get('display_name') or provider
    if provider not in TEXT_PROVIDERS:
        return (f'The AI Parser uses {name}, which reads documents but cannot answer '
                'questions. Choose OpenAI, Anthropic, Claude Code CLI or Kiro CLI in '
                'Settings → AI Parser.')
    if not settings.get('api_key'):
        return f'No API key is stored for {name}. Add one in Settings → AI Parser.'
    return None


def complete(settings, system, user, max_tokens=1500):
    """The provider's reply text to `user` under the `system` instruction.
    `settings` must pass `missing_reason`."""
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
