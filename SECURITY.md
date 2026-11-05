# Security

Crisis Command is a research prototype, not a certified dispatch system. Don't connect it to
live emergency operations without your agency's own review.

## Reporting a vulnerability

Please don't open a public issue. Use GitHub's **Report a vulnerability** (Security tab →
Advisories) on this repository, with steps to reproduce. You'll get a reply as soon as possible.

## Handling data safely

- Keep `META_API_KEY` and `INTAKE_TOKEN` in `.env` (git-ignored) or your host's secret store.
- Set `INTAKE_TOKEN` whenever `POST /api/intake` is reachable from the internet.
- Names, phone numbers and emails are redacted locally before any cloud model call, but treat
  every report as personal data anyway.
- The citizen hub shows each resident only their own reports, by a random receipt kept on their
  device.
