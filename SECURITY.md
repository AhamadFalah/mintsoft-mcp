# Security

This server talks to a **live** warehouse management system — there is no Mintsoft
sandbox. If you believe you've found a vulnerability (a way to bypass the write
gating, the client allowlist, or the confirm gates; key leakage into logs or tool
output; or anything injection-shaped), please **do not open a public issue**.

Report it privately via GitHub's *Report a vulnerability* (Security Advisories) on
this repository. You'll get an acknowledgement within a week.

Hardening notes for operators:

- Run read-only (the default) unless you have a concrete write task.
- Point `MINTSOFT_WRITE_CLIENT_ALLOWLIST` only at a dedicated test client account.
- Keep the audit log on (`MINTSOFT_AUDIT=1`, the default) — it is your recovery record.
- Treat your Mintsoft API key like a production credential: env var or secret
  manager, never in files you commit.
