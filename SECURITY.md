# Security Policy

ContaAutónomo is a self-hosted application that stores financial and personal
data. Security reports are taken seriously and handled privately.

## Supported Versions

Only the latest code on the `main` branch receives security fixes. If you run
an older checkout or Docker image, update to the latest `main` before
reporting.

## Reporting a Vulnerability

**Please do not open a public issue, discussion, or pull request for security
problems.**

Report vulnerabilities privately through GitHub:

1. Go to the repository's **Security** tab.
2. Click **Report a vulnerability**.
3. Describe the issue, affected component, and steps to reproduce. A minimal
   proof of concept helps.

This uses GitHub's private vulnerability reporting, so the report is visible
only to the maintainers.

## What to Expect

- Acknowledgement of your report within **7 days**.
- An assessment and, where confirmed, a fix or mitigation plan. Timelines
  depend on severity and complexity; this is a volunteer-maintained project.
- Credit in the release notes and the published advisory, unless you prefer to
  remain anonymous.

## Scope

In scope: the application code in this repository (core app, modules, auth,
file storage, backups, the REST API) and the provided `Dockerfile` /
`docker-compose.yml`.

Out of scope: vulnerabilities in your own deployment (reverse proxy, host,
TLS configuration), third-party services you connect (cloud storage, AI
providers, SMTP), and issues that require an already-authenticated
administrator to act against their own installation.

## Hardening Your Deployment

- Always set a strong `SECRET_KEY` in production.
- Serve the app over HTTPS and set `FORCE_HTTPS=1`.
- Keep the `instance/` directory (database and auth config) and backups out of
  any publicly reachable location.
- Rotate the API token if it may have been exposed.
