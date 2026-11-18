# AI Parser (`ai_parser`)

Reads an invoice or a receipt (PDF or photo) with an AI provider and returns
the fields that prefill the invoice and expense forms. Nothing is saved: the
user reviews the form and saves it.

## Files

| File | Contents |
|------|----------|
| `index.py` | `AIParserModule`: config table, routes, settings tab, invoice-form widget |
| `providers.py` | One class per provider, `PROVIDERS`, reply parsing, error messages |
| `prompts.py` | `INVOICE_PROMPT`, `EXPENSE_PROMPT`, `EXPENSE_CATEGORIES` |
| `media.py` | File type from the leading bytes; what each provider is sent |
| `catalog.py` | Default, fallback and retired models; live listing via `GET /v1/models` |
| `cli_agents.py` | Claude Code and Kiro CLI providers, run as local programs |
| `templates/` | Parse page and settings tab |

## Registry functions

The module declares one function in `get_functions()`; other code calls it by
name through `core.functions`, never this module directly, so a module that
declares the same name replaces this one for it:

| Function | Method | Notes |
|---|---|---|
| `ai.parse_document` | `parse_file(file_data, filename, doc_type)` | The configured provider reads the document; route `/ai-parser/parse` for browser forms (the expense form posts there) |

Without a configured provider it raises `FunctionUnavailable` with a message
for the user. Text questions (`ai.complete_text`, `ai.text_status`) are not
this module's: the AI Agent (`modules/ai_agent`) answers them, with its own
provider settings.

## Flow

1. `POST /ai-parser/parse` (or `POST /api/v1/m/ai_parser/parse`) with `file`
   and `doc_type` (`invoice` or `expense`).
2. `media.detect_mime` reads the type from the bytes. Anything other than
   PDF, JPEG, PNG, GIF or WebP returns 422 without calling a provider.
3. The configured provider sends the file and the prompt for `doc_type`.
4. `providers.parse_reply` takes the JSON object out of the reply. A reply of
   `{"error": "not_a_document"}` returns 422.

## Result fields

The field names are the contract with the forms; change them together.

- Invoice (`templates/create.html` through the widget in `index.py`):
  `invoice_number`, `invoice_date`, `due_date`, `client_name`, `client_vat`,
  `client_address`, `currency`, `items[]` (`description`, `quantity`,
  `unit_price`), `total`, `notes`.
- Expense (`modules/expenses/templates/expense_form.html`, `prefillFromData`):
  `invoice_number`, `expense_date`, `contractor_name`, `contractor_vat`,
  `contractor_country`, `buyer_tax_id`, `amount` (gross), `currency`,
  `category`, `description`, `net_amount`, `vat_rate`, `vat_amount`,
  `reverse_charge`. VAT fields are `null` when the document shows no
  breakdown. `buyer_tax_id` is the tax ID the document is issued to (`null` on
  most tickets); the expense form compares it with the owner's NIF to warn
  that the VAT is normally not deductible. The form picks the contractor by
  `contractor_vat`, then by `contractor_name`.

## CLI agents (`cli_agents.py`)

`claude_cli` (Claude Code) and `kiro_cli` (Kiro CLI) run the CLI installed on
the server, under the account it is logged in with, once per parse. They are
for self-hosted installs: the Docker image does not include either CLI.

| Server environment variable | Effect |
|---|---|
| `AI_PARSER_ALLOW_CLI=1` | Required. Without it the CLI providers are listed but never run. |
| `AI_PARSER_CLAUDE_BIN`, `AI_PARSER_KIRO_BIN` | Binary to run instead of `claude` / `kiro-cli` from `PATH`. |
| `AI_PARSER_CLI_TIMEOUT` | Seconds before a run is stopped (default 180, minimum 10). |

The binary is never taken from the settings form; only the model is, and it
must match a model-name pattern so it cannot be read as a flag. Each run gets
a new temporary directory with only the upload, and the prompt tells the
agent to treat the document as data.

- Claude Code: `claude -p --output-format json --safe-mode --restricted
  --tools Read --allowedTools Read --permission-prompts none
  --no-session-persistence [--model M]`, prompt on stdin. Read is the only
  tool in the session and is confined to the directory; it reads PDFs and
  images. API errors (for example 429) keep their status.
- Kiro CLI: `kiro-cli chat --v2 --agent contaautonomo-ai-parser
  --no-interactive --trust-tools=fs_read --wrap never [--model M] <prompt>`.
  The agent file written to the run directory gives it `fs_read` only (no
  shell, network or MCP servers). `fs_read` is not confined to the directory.
  It reads images but not PDFs, so a PDF's text layer is extracted with
  `pypdf` and sent in the prompt; a scanned PDF without text is refused (422).
  The v3 engine is not used: in a test on 2026-11-16 it ran a shell command
  although only `fs_read` was trusted. "Refresh list" runs
  `kiro-cli chat --list-models -f json`.

A run blocks one web worker for up to the timeout.

## Models

Defaults are in `catalog.DEFAULT_MODELS` and `catalog.CLI_DEFAULT_MODELS`. "Refresh list" in Settings → AI
Parser calls `POST /ai-parser/models/<openai|anthropic>/refresh`, which asks
the vendor which models the key can use and stores the list in
`ai_parser_config.models_cache`. When a vendor retires a model that earlier
versions offered, add it to `catalog.RETIRED_MODELS`: stored settings on it
move to the default when the module loads.

## Tests

`tests/test_ai_parser.py` and `tests/test_ai_parser_cli.py`. Every SDK is
replaced by a stub and `subprocess.run` by a recorder; tests never call a
provider or run a CLI.
