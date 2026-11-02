# Import provenance

`evidence/` is an unchanged extraction of the reviewed, already-public
`incident-agent-evidence-v0.1.0.zip`:

- Source: https://curtiscovington.com/assets/posts/incident-agent-context/incident-agent-evidence-v0.1.0.zip
- Archive SHA-256: `f00a0dfdb6bb985bc91b978d371dc98c2464143bf581c288837e6e15fa23772b`
- Archive bytes: 1,665,809.
- The ZIP's single enclosing directory was removed when placing its contents
  under `evidence/`; no payload file was changed.

The archive's manifest and `SHA256SUMS` describe paths relative to `evidence/`
and remain valid. New repository-level documentation explains navigation,
licensing and saved-analysis reproduction; it is outside the original package
manifest. `import-provenance.json` records this mapping.

Original-source hashes and documented sanitization remain in
`evidence/provenance/`. Study-frozen source takes precedence over historical
supplements. The supplemental revision
`cb9a370a7144c8c2d7b9fbf043b3f64dc1549fc7` matches 15/16 archived study source
files, excluding `app/main.py`; no supplemental `app/main.py` is substituted.
The job-service implementation remains the separately frozen
`control-plane/service.py`.

No new records were selected from the private Lab for this repository. No
secrets, fixture volumes, raw unrelated logs or private runtime state were
added. The original URL records a historical publication location; it is not a promise of a current download.
