# Security

Tirzah is an early local-first research prototype. Treat it as unsuitable for untrusted network exposure until authentication, authorization, and data-handling boundaries are explicitly designed and tested.

## Reporting

Report security concerns through a private channel to the repository owner rather than opening a public issue with exploit details.

## Data Handling

- Do not commit `config.yaml`, local archives, dead-letter files, or MongoDB data exports.
- Assume ingested documents and prompt history may contain private material.
- Run the web interface on `127.0.0.1` unless a deliberate deployment model has been reviewed.
- If any `/api/*` route is exposed beyond trusted loopback access, set `runtime.web_api_token`
  or `TIRZAH_WEB_API_TOKEN` and require clients to send `Authorization: Bearer <token>` or
  `X-Tirzah-Api-Token`.
- Set `runtime.web_localhost_only: true` or `TIRZAH_WEB_LOCALHOST_ONLY=true` for an extra
  loopback-only middleware guard in local deployments.
- `runtime.web_max_upload_bytes` caps `/api/upload-source` request content; keep it low for
  local development and raise it only for reviewed deployments.
