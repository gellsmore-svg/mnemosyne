# Security

Mnemosyne is an early local-first research prototype. Treat it as unsuitable for untrusted network exposure until authentication, authorization, and data-handling boundaries are explicitly designed and tested.

## Reporting

Report security concerns through a private channel to the repository owner rather than opening a public issue with exploit details.

## Data Handling

- Do not commit `config.yaml`, local archives, dead-letter files, or MongoDB data exports.
- Assume ingested documents and prompt history may contain private material.
- Run the web interface on `127.0.0.1` unless a deliberate deployment model has been reviewed.
