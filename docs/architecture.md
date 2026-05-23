---
layout: page
title: "Architecture — Modular Flask core and capabilities"
description: "How ContaAutónomo's Flask core, ModuleManager, CoreServices and pluggable modules fit together end to end."
permalink: /architecture/
extra_scripts:
  - /assets/js/mermaid-loader.js
---

ContaAutónomo is a Flask application split into a small core and a fleet of self-contained modules. The core wires up the database, request routing and a handful of shared services; the `ModuleManager` discovers, loads and lifecycles modules at startup; modules register their own SQLAlchemy models, Blueprints and capabilities. This page walks through that architecture top-down: the high-level diagram, the CoreServices API, the module lifecycle, the capability system, a minimal example module, the pluggable auth provider system, and the on-disk project layout.

## High-level architecture

The Flask core (`app.py`) builds the application factory and exposes a `CoreServices` bag to every module. The `ModuleManager` discovers modules in `modules/`, instantiates them with a reference to the core, and lets each one register its own models and routes. Modules talk to each other only through registered Blueprints and through the capability registry — never by direct import.

{% capture arch_diagram %}flowchart TB
    subgraph Core["Core (app.py)"]
        AppFactory[Flask app + SQLAlchemy db]
        AuthRoutes[auth_routes.py + auth.py]
    end
    subgraph CS["CoreServices"]
        DB[(db)]
        Storage[FileStorageBackend]
        Logger[ActivityLogger]
        Scheduler[TaskScheduler]
        InvoiceSvc[InvoiceService]
        CurrencySvc[CurrencyService]
    end
    MM[ModuleManager]
    subgraph Mods["modules/*"]
        M1[expenses]
        M2[tax_management]
        M3[documents]
        M4[backup]
        M5[reports]
    end
    Core --> CS
    Core --> MM
    MM -- "instantiates" --> Mods
    Mods -. "use" .-> CS
    Mods -. "register Blueprints" .-> AppFactory
{% endcapture %}
{% capture arch_ascii %}
Core (app.py)
  ├── CoreServices: db, storage, logger, scheduler, invoice_service, currency_service
  └── ModuleManager
         └── modules/* (expenses, tax_management, documents, backup, reports, ...)
               └── use CoreServices, register Blueprints with Flask app
{% endcapture %}
{% include mermaid_block.html diagram=arch_diagram ascii=arch_ascii %}

## Core services

`CoreServices` is the contract between the core and the modules. Every module receives a reference to it during construction and uses it for database access, file storage, audit logging, scheduling, invoice operations and currency conversion. The signatures below mirror `module_manager.py`; modules should treat them as the stable interface.

{% for svc in site.data.core_services %}
<h3 id="{{ svc.name }}">{{ svc.name }}</h3>
<p>{{ svc.description }}</p>
<table>
  <caption>Methods of <code>{{ svc.name }}</code></caption>
  <thead>
    <tr>
      <th scope="col">Signature</th>
      <th scope="col">Description</th>
    </tr>
  </thead>
  <tbody>
    {% for m in svc.methods %}
    <tr>
      <td><code>{{ m.sig }}</code></td>
      <td>{{ m.desc }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endfor %}

## Module lifecycle

`ModuleManager` runs each module through five phases on application startup. Each phase has a clearly bounded job, which keeps modules independent and makes failures easy to localise.

1. **discover** — scan the `modules/` directory for subdirectories that contain an `index.py`. The directory name becomes the candidate module id; modules disabled in settings are skipped before any code is imported.
2. **load** — import `modules.<id>.index`, locate the `BaseModule` subclass and instantiate it with a reference to the core. An exception in any module's `__init__` is logged and isolated; the rest of the application continues to start.
3. **register_models** — call `module.register_models(db)`. The module declares its own SQLAlchemy model classes against the shared `db` instance and returns them in a dict so the manager can expose them to other modules and migrations.
4. **register_routes** — call `module.register_routes(app)`. The module creates one or more Flask `Blueprint` objects (typically with a unique `url_prefix`) and registers them on the application. This is the only place modules touch URL routing.
5. **on_enable** — call `module.on_enable()`, which runs once after every module has registered routes. It is the right place for cross-module setup: registering periodic jobs with `scheduler.add_job`, adding navigation entries, or warming caches that depend on data owned by other modules.

## Capabilities

Modules expose typed extension points through capabilities. A producer returns a list of capability dicts from `get_capabilities()`; a consumer asks the `ModuleManager` for capabilities of a given `type` (and optionally `method`) and invokes them. This keeps integrations symmetric — neither side imports the other — and means new providers can be added by enabling a module, without changing existing code.

A capability is a plain dict with at least `type`, `method`, `name` and `action` keys, plus any provider-specific metadata. The example below shows a `pdf_signature` module exposing a visual signature capability and a consumer (e.g. an invoice route) finding and calling it:

```python
# Producer (in modules/pdf_signature/index.py)
def get_capabilities(self):
    return [{
        'type': 'pdf_sign',
        'method': 'visual',
        'name': 'Visual Signature',
        'accepts': ['pdf'],
        'action': self._sign_visual,
    }]


# Consumer (any other module via core.module_manager)
signers = core.module_manager.find_capabilities('pdf_sign', method='visual')
for s in signers:
    result = s['action']({'invoice_id': 42})
```

The same pattern is used for storage backends, activity logger backends, country-specific tax packs and PDF verifiers. `find_capabilities` returns an empty list when nothing is registered, so consumers handle "no provider available" as a normal case rather than an error.

## Building a minimal module

The shortest possible module is a class that subclasses `BaseModule`, declares an id and name, registers one model and registers one route. The example below lives at `docs/_includes/code/minimal_module.py` and is included verbatim so the snippet stays in sync with what is actually shipped:

```python
{% include_relative _includes/code/minimal_module.py %}
```

Drop this file into `modules/hello/index.py`, add an empty `modules/hello/__init__.py` and a `templates/hello/index.html`, and the next time the app starts `ModuleManager` will discover it, register the `HelloNote` table, and mount `/hello/` automatically.

## Authentication providers

Authentication in ContaAutónomo is pluggable. The core ships with `auth_routes.py`, which owns the `/auth/login/<provider>` and `/auth/callback/<provider>` URLs, but the actual identity verification is delegated to providers contributed by modules. A module advertises its providers by overriding `get_auth_providers()` on its `BaseModule` subclass and returning a list of provider objects (each implementing `id`, `display_name`, `start_login(request)` and `handle_callback(request)`).

Two provider families are supported out of the box:

- **OAuth providers** (Google, GitHub) — the module redirects the user to the provider's authorize URL during `start_login`, then exchanges the returned code for tokens during `handle_callback` and maps the verified email or `sub` claim to a local user.
- **SAML providers** — the module emits a SAML AuthnRequest in `start_login`, validates the signed assertion in `handle_callback` and creates or links the local user from the assertion attributes.

`auth_routes.py` keeps no provider-specific state itself: it asks `module_manager.find_capabilities('auth_provider')` for the registered providers, dispatches by `provider.id`, and renders the login page from whatever the modules contributed. Adding SSO for a new identity source is therefore a matter of writing a new module — the core does not change.

## Project structure

The repository follows a flat, predictable layout. Core files live at the root, modules live under `modules/<id>/`, and each module owns its templates and static assets. A trimmed two-level tree (with one sample module fully expanded) looks like this:

<pre>
contaautonomo/
├── app.py
├── module_manager.py
├── auth_routes.py
├── auth.py
├── modules/
│   └── expenses/
│       ├── __init__.py
│       ├── index.py
│       └── templates/
│           └── expenses/
│               └── list.html
├── templates/
│   ├── base.html
│   └── index.html
├── static/
├── instance/
│   └── data.db
└── docker-compose.yml
</pre>

The `instance/` directory holds the SQLite database and any uploaded files in the default `LocalStorageBackend`; it is gitignored. `docker-compose.yml` at the root brings the whole stack up with a single `docker compose up -d` for self-hosted deployments.

<script src="{{ '/assets/js/mermaid-loader.js' | relative_url }}" defer></script>
