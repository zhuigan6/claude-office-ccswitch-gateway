# 3.4 upgrade and verification

## Existing installations

Keep your current `.env`, port, Task Scheduler action, Office Gateway settings and `runtime/` files. Replace `gateway/office_edge.py` and `supervisor.py` after backing them up, then restart the owned supervisor and gateway processes. Do not create another scheduled task or reset Office WebView storage. The file database schema is unchanged.

The public defaults remain port `8790` and task `Claude Office Gateway`. An existing `8787` installation continues to use `EDGE_PORT=8787`. Diagnose a custom task with `./diagnose.ps1 -Port 8787 -TaskName 'Claude Office Gateway 8787'`.

New developer manifests use the historical three-host shared-runtime template in `templates/claude-office.xml`. The frontend and icons load from `pivot.claude.ai`; encoded query parameters supply the gateway URL and token. The gateway does not serve an `/index.html` frontend. Existing installed manifests are not modified automatically. Generated manifests contain the supplied token and must stay private.

If both CC Switch categories exist, `auto` prefers `claude-desktop`. Set `CCSWITCH_CHANNEL=claude` explicitly to follow the Claude Code category. Both are supported configuration choices, not a reliable way to infer CC Switch version.

## Authentication and diagnostics

- Set `EDGE_ALLOWED_ORIGINS=https://pivot.claude.ai` (default). Additional frontends require comma-separated exact origins. Unknown browser origins receive 403, including actual GET/POST requests.
- With no fixed `EDGE_TOKEN`, supply a nonempty placeholder such as `PROXY_MANAGED` for either channel. This is a local usability mode, not strong authentication against other local processes.
- With `EDGE_TOKEN` configured, supply that token. The legacy CC Switch gateway token remains accepted for existing clients; it is used internally for upstream requests.
- `/status/ccswitch` no longer exposes credentials or complete provider environments. Verification never retrieves secrets to silently repair incorrect authentication.
- `/healthz` reports process liveness and a cached configuration summary. Use `/status/ccswitch` for fresh configuration and a real Messages request for upstream readiness.

## Protocol fidelity

Tool wrappers are unwrapped without discarding native tool types, strict/cache fields or forced `tool_choice`. Thinking and system instructions are preserved. Automatic compatibility retries only remove `metadata` or `service_tier` explicitly rejected by a 400/422 response. Unknown incompatibilities are returned to the client. 401/403/429/5xx are never compatibility-retried.

Successful optional-field adaptations are remembered for five minutes, isolated by channel/provider/model/configuration, with at most 128 entries.

Development records confirm that some historical providers rejected adaptive thinking, system blocks and forced tools. If that exact incompatibility remains, explicitly set `EDGE_LEGACY_SANITIZE=1` and restart the gateway to allow one legacy deep-sanitization retry on field-related 400/422 errors. This is lossy: it may remove thinking and tool extensions or relax forced tool selection. It is disabled by default, never learned into compatibility memory, and should be disabled again when switching to a fully compatible provider. See ADR-0009, which supersedes ADR-0008's broad automatic cleanup.

Database cache fingerprints include both the main database and WAL. This matters because committed writes can remain in WAL before checkpointing ([SQLite WAL documentation](https://www.sqlite.org/wal.html)). Streaming uses `HTTPResponse.read1` so small events need not fill a fixed-size read buffer ([Python HTTPResponse documentation](https://docs.python.org/3/library/http.client.html#httpresponse-objects)).

`count_tokens` forwards the normalized model and file references using the same credentials and beta headers as Messages. Unsupported upstream responses remain errors. The auxiliary OpenAI conversion is text-oriented and is not a full tool/image-compatible replacement for Anthropic Messages.

## Verification

```powershell
python -m unittest discover -s tests -v
./verify.ps1 -BaseUrl http://127.0.0.1:8790
./verify.ps1 -BaseUrl http://127.0.0.1:8790 -RunInference
```

Tests cover WAL-only model changes, tool fidelity, retry boundaries, small SSE first events, Unicode filenames, Origin rejection and actual Windows pythonw child restart. Live inference costs API usage. A syntax check or HTTP 200 alone is insufficient: the live check requires an actual message, no SSE error event and a message_stop event.

Verify Word, Excel and PowerPoint UI actions separately. Upstream model capabilities still determine whether images, thinking and tools work. This update does not claim all models support every Anthropic capability.

The supervisor logs to `runtime/supervisor.log`. It restarts only its own child; an unidentified occupied port is left untouched and reported. Forced OS termination may leave an orphan process; identify its executable, script path and parent before stopping it during maintenance.
