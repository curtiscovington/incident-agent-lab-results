# Inspecting the environment

This package offers three distinct levels of reproducibility:

1. **Saved-data analysis is reproducible.** Run `python analysis.py` and
   `python count_replies.py` with Python 3.10+; no model, network or live tools
   are used. The scripts reconcile saved trial and replay records.
2. **The environment is inspectable.** The study-frozen Terraform, service,
   fault injection, reset/judge and agent-dispatch sources are already in
   `code/frozen-study`. The supplementary Git sources in
   `code/historical-environment` add SQL, NGINX/NATS/Prometheus configuration,
   Docker build definitions, dependency declarations, provider locks, and
   explanatory architecture/runbook documents. See the source index below.
3. **A complete live rerun is not packaged.** There are no image/weight
   binaries, model/tokenizer cache, initialized fixture volumes, credentials,
   private-access identities or complete Lab application. Historical setup
   commands are source documentation, not a supported one-command rerun.
   Recreating a new isolated environment requires additional integration,
   separate dependencies and new secrets. Never point the historical tools at
   a production installation.

## Provenance and sanitization

The 16 files in `code/frozen-study` come from the study's archived source
directory; their original hashes are retained in the scientific protocol and
source provenance. Existing documented path/LAN masking remains unchanged.

The 28 additional files are copied from historical Git commit
`cb9a370a7144c8c2d7b9fbf043b3f64dc1549fc7`. Fifteen of the sixteen archived
source files match that revision byte for byte; `app/main.py` does not. This
anchors the supplement to the studied implementation, but does not prove that
each supplementary file was the exact runtime version. They are labeled
historical supplements, not newly discovered frozen records. The archived
sources take precedence if there is a discrepancy. Original/export hashes and
the full selection are in `provenance/environment-sources.json`.

Only allowlisted tracked source/static configuration was read for this
supplement. No `.env`, secret value, Terraform state, runtime access catalog,
credential store or volume content was read. None of the 28 supplemental files
needed redaction. Local Docker subnet addresses and service identifiers are
part of the topology, not addresses of externally accessible services. Paths
to password files in code are references; their contents are absent.

## Source index

| Question | Source relative to package root |
| --- | --- |
| Topology, roles and data flow | `environment/architecture.md`; `code/frozen-study/control-plane/compose.json`; `code/historical-environment/control-plane/blueprint.json` |
| Durable job implementation | `code/frozen-study/control-plane/service.py`; `code/historical-environment/control-plane/schema.sql` |
| Ingress, messaging, observation | `code/historical-environment/control-plane/nginx.conf`, `nats-1.conf`, `nats-2.conf`, `nats-3.conf`, `prometheus.json` |
| Desired infrastructure / release / worker / policy changes | `code/frozen-study/control-plane/{terraform,releases,workers,workloads,network-policy}/main.tf` |
| Policy application | `code/frozen-study/control-plane/network-policy/{apply,route}.py` |
| Container builds and dependencies | Dockerfiles, build.py and requirements.txt under `code/historical-environment/control-plane` |
| Terraform provider versions | `.terraform.lock.hcl` under the supplemental terraform, releases, workers and workloads directories |
| Agent prompt, scope and tool dispatcher | `code/frozen-study/app/incident_agent.py` |
| Faults, reset, verification and independent judging | `code/frozen-study/app/{recovery_study,plane_diagnosis,plane_releases}.py`; `environment/faults-and-actions.md` |
| Supporting alert/runbook context | `code/historical-environment/app/plane_incident.py`; supplemental control-plane README and runbooks |

The supplement intentionally excludes unrelated Lab projects and private-access
service source/runtime identities. References to those components in historical
documentation are retained for context; they do not make this a complete Lab
distribution. Provider locks describe external dependencies; no provider
executables, upstream image contents or third-party packages are included.
