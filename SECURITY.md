# Security Policy

ContaAutónomo may process sensitive financial and personal information. We appreciate responsible security research.

## Reporting a vulnerability

**Please do not disclose suspected vulnerabilities in public GitHub issues or discussions.**

Use GitHub's **Report a vulnerability** action in the repository's Security tab (private vulnerability reporting), if available. Include:

- A concise description and potential impact.
- The affected version, commit, or deployment configuration.
- Reproduction steps or a proof of concept that does not expose real user data.
- Any suggested mitigation, if known.

If private vulnerability reporting is not enabled, contact the maintainer privately through the contact method listed on the GitHub profile. Do not publish exploit details until a fix or mitigation has been coordinated.

## What to expect

The maintainer will make a reasonable effort to acknowledge the report, investigate it, and coordinate remediation and disclosure.

## Safe testing

- Test only systems and data you own or have explicit permission to assess.
- Do not access, modify, or retain other users' financial data.
- Avoid denial-of-service testing, destructive actions, and social engineering.
- Do not include real credentials, invoices, personal data, or customer records in reports.

## Deployment guidance

Operators should use HTTPS, keep dependencies and the host system updated, protect `.env` and encryption keys, restrict access to backups, and maintain secure copies of critical keys. A database or backup may not be sufficient to recover encrypted information if its associated key is lost.
