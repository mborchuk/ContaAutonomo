"""
CLI agents of the AI Agent: a coding-agent CLI installed on the server
(Claude Code, Kiro CLI) answers a text question under the account it is
logged in with.

Running a local program from a web request is off unless the server sets
AI_AGENT_ALLOW_CLI=1 (AI_PARSER_ALLOW_CLI=1 is honoured when the AI Agent
variable is not set, so a server set up for the AI Parser keeps working). The
binary comes from PATH or a server environment variable, never from the
settings form. Every run gets an empty temporary directory and no tools at
all; the message goes in on stdin, so its size is not limited by argv:

- Claude Code: `--tools ""` (no tool exists in the session), `--restricted`,
  `--safe-mode` (no user hooks, plugins, MCP servers or CLAUDE.md), and the
  instruction replaces Claude Code's own system prompt.
- Kiro CLI: the v2 engine with a workspace agent that has no tools. (The v3
  engine ran a shell command although only fs_read was trusted, so it is not
  used.) Kiro takes no separate system prompt; the instruction comes first.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile

PREFIX, LEGACY_PREFIX = 'AI_AGENT_', 'AI_PARSER_'
ALLOW_ENV = PREFIX + 'ALLOW_CLI'
TIMEOUT_ENV = PREFIX + 'CLI_TIMEOUT'
DEFAULT_TIMEOUT = 180
KIRO_AGENT = 'contaautonomo-ai-agent'

_MODEL_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,99}$')
_ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')


class CLIError(RuntimeError):
    """The CLI run failed. `status_code` carries the API status when the CLI
    reports one (Claude Code does), so vendors.describe_error() can explain it."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def env(suffix):
    """AI_AGENT_<suffix>, else the AI Parser's AI_PARSER_<suffix>, else None."""
    value = os.environ.get(PREFIX + suffix)
    return value if value is not None else os.environ.get(LEGACY_PREFIX + suffix)


def cli_allowed():
    return env('ALLOW_CLI') == '1'


def timeout():
    try:
        return max(10, int(env('CLI_TIMEOUT') or DEFAULT_TIMEOUT))
    except ValueError:
        return DEFAULT_TIMEOUT


def valid_model(model):
    """'' (the CLI's own default) or a model name that cannot be read as a flag."""
    model = (model or '').strip()
    if model and not _MODEL_RE.match(model):
        raise ValueError('Model names contain only letters, digits and . _ : / - [ ]')
    return model


class CLIAgent:
    name = ''
    display_name = ''
    binary = ''
    bin_suffix = ''            # AI_AGENT_<bin_suffix> names another binary path
    install_hint = ''

    def __init__(self, model=''):
        self.model = model or ''

    @property
    def bin_env(self):
        return PREFIX + self.bin_suffix

    def binary_path(self):
        return shutil.which(env(self.bin_suffix) or self.binary)

    def unavailable_reason(self):
        """None when the CLI can run, else what to change on the server."""
        if not cli_allowed():
            return f'CLI agents are off. Set {ALLOW_ENV}=1 in the server environment.'
        if not self.binary_path():
            return (f'`{env(self.bin_suffix) or self.binary}` is not on the server PATH. '
                    f'{self.install_hint}')
        return None

    def complete_text(self, system, user):
        """The CLI's reply to `user` under the `system` instruction, as text."""
        reason = self.unavailable_reason()
        if reason:
            raise CLIError(reason)
        model = valid_model(self.model)
        with tempfile.TemporaryDirectory(prefix='ai-agent-') as workdir:
            self.prepare(workdir)
            args, stdin = self.command(self.binary_path(), system, user, model)
            proc = self._run(args, workdir, stdin)
        return self.reply_text(proc).strip()

    def _run(self, args, workdir, stdin):
        try:
            return subprocess.run(args, cwd=workdir, capture_output=True, text=True,
                                  timeout=timeout(), check=False, input=stdin)
        except subprocess.TimeoutExpired:
            raise CLIError(f'{self.display_name} did not answer within {timeout()} s') from None
        except OSError as e:
            raise CLIError(f'{self.display_name} could not start: {e.strerror}') from None
        except ValueError as e:  # e.g. a NUL byte in an argument
            raise CLIError(f'{self.display_name} could not start: {e}') from None

    def prepare(self, workdir):
        """Write any per-run configuration into the working directory."""

    def command(self, binary, system, user, model):
        """(argv, stdin text) for one question."""
        raise NotImplementedError

    def reply_text(self, proc):
        raise NotImplementedError

    @staticmethod
    def _tail(text):
        return _ANSI_RE.sub('', (text or '').strip())[-300:]


class ClaudeCLI(CLIAgent):
    name = 'claude_cli'
    display_name = 'Claude Code CLI'
    binary = 'claude'
    bin_suffix = 'CLAUDE_BIN'
    install_hint = 'Install Claude Code and log in as the user that runs the app.'

    def command(self, binary, system, user, model):
        args = [binary, '-p', '--output-format', 'json', '--safe-mode', '--restricted',
                '--tools', '', '--permission-prompts', 'none', '--no-session-persistence',
                '--system-prompt', system]
        if model:
            args += ['--model', model]
        return args, user

    def reply_text(self, proc):
        try:
            out = json.loads(proc.stdout)
        except ValueError:
            raise CLIError(f'Claude Code CLI failed (exit {proc.returncode}): '
                           f'{self._tail(proc.stderr or proc.stdout)}') from None
        if out.get('is_error') or proc.returncode != 0:
            raise CLIError(f'Claude Code CLI error: {self._tail(str(out.get("result")))}',
                           status_code=out.get('api_error_status'))
        return out.get('result') or ''


class KiroCLI(CLIAgent):
    name = 'kiro_cli'
    display_name = 'Kiro CLI'
    binary = 'kiro-cli'
    bin_suffix = 'KIRO_BIN'
    install_hint = 'Install the Kiro CLI and log in as the user that runs the app.'

    def prepare(self, workdir):
        # A workspace agent with no tools: no shell, no files, no network,
        # no MCP servers. It can only answer.
        agents = os.path.join(workdir, '.kiro', 'agents')
        os.makedirs(agents)
        with open(os.path.join(agents, f'{KIRO_AGENT}.json'), 'w') as fh:
            json.dump({'name': KIRO_AGENT, 'description': 'Answers one question for the app',
                       'prompt': None, 'tools': [], 'allowedTools': [],
                       'mcpServers': {}, 'includeMcpJson': False, 'resources': []}, fh)

    def command(self, binary, system, user, model):
        # The v2 engine reads the prompt from stdin when it is piped.
        args = [binary, 'chat', '--v2', '--agent', KIRO_AGENT, '--no-interactive',
                '--wrap', 'never']
        if model:
            args += ['--model', model]
        return args, f'{system}\n\n---\n\n{user}'

    def reply_text(self, proc):
        if proc.returncode != 0:
            raise CLIError(f'Kiro CLI failed (exit {proc.returncode}): {self._tail(proc.stderr)}')
        return _ANSI_RE.sub('', proc.stdout or '')

    def list_models(self):
        """Models the logged-in Kiro account offers: [{'id', 'name'}]."""
        reason = self.unavailable_reason()
        if reason:
            raise CLIError(reason)
        try:
            proc = subprocess.run([self.binary_path(), 'chat', '--list-models', '-f', 'json'],
                                  capture_output=True, text=True, timeout=60, check=False,
                                  stdin=subprocess.DEVNULL)
            models = json.loads(proc.stdout)['models']
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
            raise CLIError('Kiro CLI did not return a model list') from None
        out = []
        for m in models:
            label = m.get('model_name') or m['model_id']
            if m.get('rate_multiplier') is not None:
                label += f' ({m["rate_multiplier"]}x credits)'
            out.append({'id': m['model_id'], 'name': label})
        return out


AGENTS = {'claude_cli': ClaudeCLI, 'kiro_cli': KiroCLI}
