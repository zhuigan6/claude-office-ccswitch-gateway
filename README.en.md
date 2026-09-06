# Claude Office × CC Switch Local Gateway

[中文](README.md) | **English**

A local, zero-cloud adapter that lets the official Claude add-in inside Microsoft Word / Excel / PowerPoint use the provider you have selected in [CC Switch](https://github.com/farion1231/cc-switch) (DeepSeek / Kimi / GLM and other Anthropic-compatible endpoints). Switch providers in CC Switch and **the very next message takes effect** — nothing to restart.

**Purely local**: the gateway listens on `127.0.0.1` only, talks to no third-party server, and never stores provider keys (they stay inside CC Switch).

```text
Claude add-in in Word / Excel / PPT (https://pivot.claude.ai)
      ↓  local HTTP, CORS/PNA handled
office_edge gateway  http://127.0.0.1:8790   (pure Python stdlib, windowless daemon)
      ↓  reads cc-switch.db per request (read-only) for current provider/model/token
CC Switch built-in proxy  http://127.0.0.1:15721
      ↓
CC Switch current provider (GUI hot-switching, effective per request)
```

## Features

- **Full Anthropic Messages passthrough**: streaming/non-streaming, thinking, tool calls, images, attachments; only when the upstream explicitly rejects a field does the gateway re-send once with compatibility sanitization
- **Provider-fingerprint sanitize memory**: fields a provider once rejected are remembered and pre-sanitized on later requests — halves upstream round-trips on incompatible providers; compatible providers behave exactly as before ([ADR-0008](docs/adr/0008-provider-fingerprint-sanitize-memory.md))
- **Actionable errors**: gateway-generated errors carry a machine-readable `code` + `suggestion`; upstream errors are normalized to the official error shape
- **Dynamic model list**: `/v1/models` synthesizes `claude-*` aliases from the current provider (avoids Office filtering); reopen the sidebar after switching to refresh
- **Files API**: upload/list/download/delete (24h default TTL, storage quota, background cleanup); `file_id` references are inlined as image or text (TXT/MD/CSV/JSON/XML/PDF/DOCX/XLSX/PPTX); archives are explicitly rejected, never auto-extracted (zip-bomb safe)
- **Self-healing daemon**: health-probes every 5 s, restarts on false-death, auto-starts at login
- **Two generations of CC Switch**: auto-detects old/new database layouts and upstream paths ([ADR-0003](docs/adr/0003-upstream-channel-auto-detect.md)), with explicit diagnosis when the schema drifts again
- **One-click install / verify**: `install.ps1` (or double-click `install.bat`); `verify.ps1` acceptance gate, `diagnose.ps1` redacted diagnostics
- **Optional enhancements**: `install.ps1 -WithExtras` adds pypdf/python-docx/openpyxl/python-pptx for better PDF/Office text extraction (works without them — falls back to the built-in stdlib parser)

## Requirements

| Need | Notes |
| --- | --- |
| Windows 10/11 (macOS: see [notes](docs/MACOS-NOTES.md)) | gateway is cross-platform stdlib; macOS adaptation in progress |
| Python runtime | **Not needed with the Release bundle** — it ships the official embeddable runtime; git installs need Python 3.9+ (auto-downloaded if absent) |
| [CC Switch](https://github.com/farion1231/cc-switch) | with at least one **Claude-category** provider configured and enabled |
| Microsoft Office (Word/Excel/PPT) | with the official Claude add-in installed (sidebar opens) |

## Quick start

**Option 1 (recommended, zero Python install)**:

1. Download the latest `ClaudeOfficeGateway-vX.Y.Z-windows-x64.zip` from [Releases](../../releases), extract to a fixed directory (e.g. `C:\Tools\ClaudeOfficeGateway`; avoid OneDrive/Temp/Downloads)
2. Double-click `install.bat`
3. Configure Office (below) → `verify.ps1`

**Option 2 (git / developers)**:

```powershell
# 1. Clone to a fixed directory, then one-click install (auto-downloads the embedded runtime if no Python)
powershell -ExecutionPolicy Bypass -File .\install.ps1

# 2. Office side, choose one:
#    A (newer add-in with a Gateway settings pane):
#       URL: http://127.0.0.1:8790  Token: PROXY_MANAGED  Header: x-api-key  Format: Anthropic Messages
#    B (older add-in without it): generate + register a sideload manifest
powershell -ExecutionPolicy Bypass -File .\scripts\New-OfficeManifest.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\Install-DeveloperSideload.ps1 -Manifest .\sideload\claude-office-ccswitch-gateway.xml

# 3. Verify (zero cost)
powershell -ExecutionPolicy Bypass -File .\verify.ps1
```

Open the Claude sidebar in Word/Excel/PPT and chat — success. Full walkthrough: [docs/DEPLOYMENT-WINDOWS.md](docs/DEPLOYMENT-WINDOWS.md).

**Let an AI agent do it**: hand [SKILL.md](SKILL.md) to Claude Code / Cursor (or just let it read this repo) — it will install, configure, verify and troubleshoot for you.

## Daily use

- **Switch provider/model**: in the CC Switch GUI; reopen the Office sidebar afterwards to refresh the model list
- **Upgrade**: `git pull` (or re-extract a new Release) → re-run `install.ps1` → `.\verify.ps1` (`.env` and `runtime\` data are preserved — see [upgrade](docs/DEPLOYMENT-WINDOWS.md#升级))
- **Uninstall**: `powershell -ExecutionPolicy Bypass -File .\uninstall.ps1` (removes autostart and processes only; data kept)

## FAQ

| Symptom | Fix |
| --- | --- |
| Add-in shows `Could not reach gateway` / `Failed to fetch` | Run `.\diagnose.ps1` (auto-redacted, Issue-ready); if the port listens but health fails, it's a false-death — the daemon recovers within ~20 s |
| Do I really need Python? | **No.** The Release bundle ships the official embeddable runtime ([ADR-0007](docs/adr/0007-bundled-embeddable-runtime.md)); git installs without Python auto-download it |
| `502 inference gateway` | The gateway is up but can't reach 15721 or the provider: check CC Switch is running and the current provider works; don't touch Office config |
| `Something went wrong` | Usually tool-definition compat — the gateway converts/retries automatically; if it persists, open an Issue with `runtime\last-upstream-error.txt` (**strip keys first**) |
| Only one model / list not updating | Configure Claude model slots for the current provider in CC Switch; reopen the sidebar after switching |
| Chat history gone / asked to log in again | ⚠️ **Never clear Office cache/WebView data** — history lives in the local IndexedDB. See [docs/OFFICE-ONBOARDING.md](docs/OFFICE-ONBOARDING.md) |
| Popup "failed to install or load required resources" | Harmless if the sidebar still chats — a peripheral resource (icons/shortcuts) hosted on pivot.claude.ai blipped; restart that Office app to self-heal. ⚠️ **Never clear the Wef folder** (chat history lives there) |

## Honest capability boundaries

- The gateway guarantees **protocol-complete forwarding** only; vision/tool-calls/long-context depend on the current provider's model
- Scanned PDFs and complex layouts may extract poorly; archives must be unpacked manually
- Not affiliated with Anthropic, Microsoft or CC Switch; trademarks belong to their owners

## Docs

- [Architecture](docs/ARCHITECTURE.md) · [Windows deployment](docs/DEPLOYMENT-WINDOWS.md) · [Office onboarding](docs/OFFICE-ONBOARDING.md) · [Verification & triage](docs/VERIFICATION.md) · [macOS status](docs/MACOS-NOTES.md)
- [ADRs](docs/adr/) — why every major decision looks the way it does

## Contributing

Issues and PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) ([English](CONTRIBUTING.en.md)). Run `python -m unittest discover -s tests` and `.\verify.ps1` before submitting.

## License

[MIT](LICENSE)
