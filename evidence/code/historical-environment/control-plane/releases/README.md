# Control Plane service: real API release incidents

The failure target is **the Control Plane service itself**. Its existing Job API
replicas (`api-1`, `api-2`) are now fully managed by this Terraform workspace.
Ingress, PostgreSQL, schedulers, NATS, workers and monitoring remain the real,
existing components. The catalog and incident agent remain outside the service.
Echo and Checkout were the wrong target: their containers and dedicated networks
have been destroyed, and their controller/UI integration has been retired.

## Scenario

`r3` is the baseline API image; `r4` contains a deliberate request-dispatch hang.
Both are built from `control-plane/service.py`, with the same dependency base and
bounded threaded HTTP server. `r4` differs by its baked-in fault flag. The normal
job path remains PostgreSQL transaction → outbox → scheduler → JetStream → worker
→ fenced completion. These are not replacement echo endpoints.

A job whose payload is `incident:queue-lock` latches the faulty API process into
an unresponsive state. This is a deterministic lab bug, not a discovered
production vulnerability. Containers stay running while health, metrics and job
requests stop responding. A maximum of 16 HTTP handler threads bounds the fault's
resource use; excess connections close. Restart clears the in-memory latch, but
the next triggering job hangs the same release again. Rollback removes the defect.
There is no host reboot and no deliberate data corruption.

You can deploy or recover one API replica, or both as the Job API component.
Failing one replica should exercise redundancy; failing both exercises service
unavailability. The full smoke test uses both. Deployment is a destructive
container replacement on a single host, not a zero-downtime rolling rollout.

## Ownership and deployment

Terraform owns the two API containers, including immutable image IDs, their
existing control/data network attachments, static IPs, limits and secret mount.
The password remains a host file mounted read-only; no password value is copied
into Terraform variables or evidence. APIs have no Docker socket and no published
host ports. The agent still has its own isolated `cp-agent` subnet.

The earlier adoption workspace no longer tracks the APIs; its capture script
excludes them. It retains 29 tracked objects, and this workspace adds two API
objects. The Lab inventory combines them without duplicate ownership. Backups of
the previous adoption state are in `.runtime/control-plane-releases/`.

Compose retains API bootstrap definitions behind `bootstrap-api`; ordinary
`./lab up` starts the other components with Compose and applies this workspace
for APIs. Ordinary Compose startup no longer recreates Terraform-managed APIs.
Do not explicitly invoke the bootstrap API profile against an established
Terraform deployment. `./lab down` stops the APIs explicitly and preserves state.

Build new immutable releases using `python3 control-plane/releases/build.py`.
The current dependency base is saved from the original installed API image.
`.runtime/control-plane-releases/releases.json` records image IDs, source hash,
base image, and the two releases offered by the UI. Never redefine an existing
release ID: add new IDs when changing application code. Historical `r1`/`r2`
images remain recorded; the current fixture uses `r3`/`r4`.

## Observation and recovery

The Lab-host release controller (`app/plane_releases.py`) uses a lock shared with
existing Control Plane experiments and instance actions. It accepts only known
API targets and built image IDs. It refuses Terraform plans that include resources
outside the two API addresses. Actions are operator authenticated; no model has
an operator credential or Docker socket mounted.

| API | Purpose |
| --- | --- |
| `GET /api/system/releases` | API image/health observations, service job probe, known-good releases, latest operation and persistent incidents |
| `GET /api/system/releases/{api-1,api-2}/logs` | Bounded actual API logs |
| `POST /api/system/releases/{target}/deploy` | Deploy a catalog release, e.g. `{ "release": "r3" }` |
| `POST /api/system/releases/{target}/trigger` | Send the incident job to selected replicas |
| `POST /api/system/releases/{target}/restart` | Restart selected API processes |
| `POST /api/system/releases/{target}/rollback` | Apply validated known-good image IDs |
| `POST /api/system/releases/{target}/validate` | Submit and complete real jobs, then record known-good images |

`target` is `api-1`, `api-2`, or `job-api` (both replicas). In Lab, select the
Control Plane service, the expanded Job API component, or an individual replica.
No additional application services or subnets are drawn.

Monitoring samples API `/health` and `/summary`; two failures open an API alarm.
Separately, about every 15 seconds plus request time, a real canary submits a job
through ingress, repeats its idempotency key, polls for completion, and verifies
the actual PBKDF2 result. A failed canary opens a **Control Plane service** incident;
a fresh successful canary resolves it. A green ingress `/health` alone cannot
claim successful service operation. Stale API readings (>20 seconds) and service
checks (>45 seconds) remain unknown. These are lab smoke-check rules, not SLOs.

Canaries intentionally add small synthetic jobs to PostgreSQL. Results and job
IDs are retained, not deleted; the existing job history has no automatic retention.
Before large or long-running trials, define retention and monitoring load.

Validation checks the triggering input directly on every selected replica, plus
normal jobs through ingress, duplicate-key identity, and correct completed results.
Ingress validation allows a 15-second convergence window for Nginx’s configured
5-second failed-upstream backoff, retaining every failed attempt and requiring two
consecutive successful jobs. The continuous monitor still records that outage.
Only passing validation promotes a pinned image to known-good. A Terraform apply
alone never does. Failed operations and checks remain visible. Restart does not
change the known-good image or repair a defective release.

State, operations, pre-action logs, alarm evidence and canary traces persist under
`.runtime/control-plane-releases/`. Controller restart marks an in-flight action
interrupted; it does not silently replay it. A deliberate fault remains active
until an explicit recovery; the live test always attempts baseline restoration.

## Qwen boundary and next step

The standalone agent receives the actual Control Plane alarms through
`/api/system`, including service-level failures. It remains **observation-only**;
no model inference or autonomous recovery is claimed here.

The next layer is a scoped tool broker and trial coordinator: give Qwen reads of
observations, deployment history and logs, plus restart/rollback only for the
trial's API target. Keep release deployment, baseline promotion, fault injection,
and verification oracles in the coordinator. Do not expose the Lab operator token
or Docker access to the model. Baseline and runbook variants should share the same
tools and environment and differ only in guidance. Keep operator fixture labels
and privileged fault configuration outside agent context.

Before running comparative trials, freeze model/digest, prompt/runbook hashes,
decoding/seed, budgets, recovery criteria, controls and raw trace format. Correct
job completion after a repeated triggering input distinguishes durable repair
from a temporary restart. This change validates infrastructure, not model ability.

## Checks

```
.venv/bin/python -m pytest -q tests/test_plane_releases.py tests/test_plane.py tests/test_plane_incident.py tests/test_terraform_inventory.py
.venv/bin/python tests/browser_plane_releases.py
.venv/bin/python tests/live_plane_releases.py --run-faults
```

The last command deliberately disrupts the real Job API and restores `r3` in a
cleanup block. See the saved validation report for successes and failures.
