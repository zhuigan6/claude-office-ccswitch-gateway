# Contributing

[中文](CONTRIBUTING.md) | **English**

Thanks for your interest! This project aims for **low friction and long-term stability**. Please spend two minutes on this document before contributing.

## Before you start

1. Skim [docs/adr/](docs/adr/) first — every major design has a veto-grade reason behind it (e.g. "why not a tunnel"), so we don't re-litigate settled routes;
2. For large changes (new endpoints, new dependencies, protocol-behavior changes), open an Issue first to align before coding;
3. Bug-fix PRs are always welcome — include reproduction steps.

## Development environment

Zero dependencies: Python 3.9+ is all you need.

```powershell
python -m unittest discover -s tests -v   # unit + end-to-end tests, must be green
powershell -ExecutionPolicy Bypass -File .\verify.ps1   # live acceptance gate (needs a running gateway)
```

Editing `.ps1` scripts: they **must be saved as UTF-8 with BOM** (PowerShell 5.x parses BOM-less Chinese comments as GBK and fails). Syntax self-check:

```powershell
$e=$null; [System.Management.Automation.Language.Parser]::ParseFile("<file>", [ref]$null, [ref]$e); $e
```

## Commit conventions

- One PR does one thing; commit messages in the imperative, e.g. `fix: expired attachments return 410 instead of 404`
- For bug fixes, add a failing test in `tests/` first, then fix until green (prevents regressions)
- Any user-visible change must update `CHANGELOG.md` and the relevant docs in the same PR

## Iron rules (violating any one will get a PR rejected)

1. `cc-switch.db` is read-only — the gateway and tools never write to it
2. Never advise users to clear Office WebView/IndexedDB (chat history lives only there)
3. Never commit keys, `.env`, `runtime/`, or any user data
4. Never fake upstream behavior (e.g. fake count_tokens precision, swallow upstream errors)
5. The gateway must keep listening on 127.0.0.1 only
6. Stability priority: history intact > request transparency > explicit errors > self-healing > new features

## Reporting security issues

**Do not** open public Issues for security vulnerabilities. Say only "security issue found" in an Issue and reach the maintainer privately for details (see [SECURITY.md](SECURITY.md)).
