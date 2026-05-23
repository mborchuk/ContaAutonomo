---
layout: page
title: "Features and Modules — ContaAutónomo capabilities"
description: "Overview of all 14 modules, multi-currency support, security mechanisms and the task scheduler in ContaAutónomo."
permalink: /features/
---

ContaAutónomo ships as a Flask core plus 14 self-contained modules. This page lists every module with its status, endpoints, models and user-facing features, then summarizes cross-cutting capabilities: multi-currency support, security and the task scheduler.

## All modules

<table>
  <caption>All modules with status and short description</caption>
  <thead>
    <tr>
      <th scope="col">Module</th>
      <th scope="col">Status</th>
      <th scope="col">Description</th>
    </tr>
  </thead>
  <tbody>
    {% for m in site.data.modules %}
    <tr>
      <td><a href="#{{ m.id }}">{{ m.name }}</a></td>
      <td>{% if m.status == 'optional' %}<span class="optional-badge">Optional</span>{% else %}Core{% endif %}</td>
      <td>{{ m.description }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>

## Module details

{% for m in site.data.modules %}
<section id="{{ m.id }}">
  <h3>{{ m.name }}{% if m.status == 'optional' %} <span class="optional-badge">Optional</span>{% endif %}</h3>
  <p>{{ m.description }}</p>
  {% if m.status == 'optional' %}
  <p class="optional-note">Can be enabled or disabled in Settings without data loss.</p>
  {% endif %}
  <h4>Endpoints</h4>
  {% if m.endpoints.size > 0 %}
  <ul class="endpoints">
    {% for ep in m.endpoints %}<li><code>{{ ep }}</code></li>{% endfor %}
  </ul>
  {% else %}
  <p><em>No HTTP endpoints (data-only module).</em></p>
  {% endif %}
  <h4>Models</h4>
  {% if m.models.size > 0 %}
  <ul class="models">
    {% for model in m.models %}<li><code>{{ model }}</code></li>{% endfor %}
  </ul>
  {% else %}
  <p><em>No SQLAlchemy models.</em></p>
  {% endif %}
  <h4>Features</h4>
  <ul class="feature-list">
    {% for f in m.features %}<li>{{ f }}</li>{% endfor %}
  </ul>
</section>
{% endfor %}

## Multi-currency support

ContaAutónomo supports invoices and expenses in multiple currencies through the `CurrencyService` core service (see [Architecture → Core services](/architecture/#currency_service)). Exchange rates come from the European Central Bank (ECB) reference feed by default, which exposes more than 50 currencies; alternative providers can be registered via `CurrencyService.set_provider(...)` without touching modules.

How it works in practice:

- Each invoice and expense stores both the **original amount and currency** entered by the user and a **converted amount in the base currency** (typically EUR). The conversion uses `CurrencyService.convert(amount, from_currency, to_currency, date_str)`.
- Rates are looked up by document date (not "now"), so historical reports remain stable even when newer rates arrive.
- Daily ECB rates are fetched and cached automatically. The Reports module switches between original-currency and converted-currency views with a single toggle.
- If the active provider fails, the service transparently falls back to ECB so freelancers can keep working offline-of-third-parties.

## Security

Security mechanisms are layered across the framework, not bolted onto a single module:

- **CSRF protection** — Flask-WTF tokens are issued for every form-rendered page and verified on all state-changing POST endpoints. The token is bound to the session.
- **Rate limiting** — per-IP, per-route throttling on authentication and write endpoints (login, password reset, AI parser, file uploads) blunts brute-force and abuse without affecting normal browsing.
- **Security headers** — every response carries a Content Security Policy (CSP), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, and a strict `Referrer-Policy`. Together they reduce XSS, clickjacking and MIME-sniffing risk.
- **AES-256 backup encryption** — the Backup module produces ZIP archives encrypted with AES-256 keys derived via PBKDF2 from either the application password or a separate, user-chosen backup password. Encrypted archives are safe to copy to External Storage (S3, GCS, Google Drive).

## Task Scheduler

The `TaskScheduler` core service runs a single in-process daemon thread and exposes a small API to modules (see [Architecture → Core services](/architecture/#scheduler)).

Modules register periodic work during their `on_enable` (or comparable) hook:

```python
core.scheduler.add_job(
    job_id="backup.daily",
    func=run_daily_backup,
    job_type="daily",
    time_str="03:00",
    description="Encrypted daily backup of DB and uploaded files",
)
```

Two job types are supported:

- `daily` — runs once per day at `time_str` (HH:MM, server local time). Used by the Backup module for nightly encrypted snapshots.
- `interval` — runs every `interval` seconds. Used for log cleanup, cache warming, and similar housekeeping work.

`scheduler.get_jobs()` returns each registered job with its id, type, schedule, last run, next run, running flag and last error — feeding the Settings UI so administrators can see what is scheduled and whether it is healthy.
