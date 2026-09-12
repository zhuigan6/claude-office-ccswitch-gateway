# ADR-0009: Request fidelity, explicit legacy fallback and local boundaries

- Status: accepted, 2026-09-12.
- Supersedes: ADR-0008 automatic deep sanitization; revises ADR-0004 authentication diagnostics.
- Evidence: two-machine development handoff dated 2026-09-10, current source review, isolated mock-provider tests and Windows runtime checks. Historical credentials and machine-specific records are not part of the repository.

## Context

The earlier implementation solved real Office errors by removing unsupported adaptive thinking, custom-tool wrappers and system block formats. It also kept a stable embedded edge token separate from the live CC Switch token. Those were important compatibility requirements.

However, always discarding tool fields or remembering broad cleanup by provider ID alone loses capabilities on models that do support them. Counting any HTTP 200 as success also misses stream errors and incomplete output. Reading only the main SQLite mtime misses WAL commits. A shared manifest proves common connectivity, not independent Word/Excel/PowerPoint UI success.

## Decision

1. Preserve tool selection and native fields. Retry explicitly rejected optional fields conservatively. Offer lossy legacy cleanup only via `EDGE_LEGACY_SANITIZE=1`; no automatic cross-model learning of that cleanup.
2. Preserve both Office entry methods, existing ports and fixed tokens. Keep the CC Switch gateway token internal; remove raw provider environments from public status output.
3. Watch main database and WAL changes. Treat `/healthz` as local liveness with a cached configuration summary, not proof that the provider is healthy.
4. Verify complete responses and timely SSE delivery. Keep real Office UI and reboot verification separate from mock/API results.
5. Never reintroduce tunnel dependency or Wef cleanup into service startup. Historical recovery scripts are reference only: directory-level `Get-FileHash`, swallowed errors and guessing the source solely by directory size are not verified migration procedures. A future migration needs explicit source/target selection, backups and per-file hashes.

## Consequences

Some older providers need an explicit compatibility opt-in after upgrading. The setting is documented because removing reasoning or changing tool selection is a semantic tradeoff, not lossless normalization. Existing 8790/8787 deployments keep their own configuration and data. Unknown port occupants are never terminated merely because a health check failed. Usage dashboards, local icon hosting, macOS distribution and other roadmap items remain separate work.
