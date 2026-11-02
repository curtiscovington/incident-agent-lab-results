# A working job control plane

The default **http://lab.local** explorer renders this deployment and its [private-access sister service](private-access/README.md). The extension adds four containers and a seventh subnet, for 17 containers total. The original Incus VM / agent environment remains at **http://lab.local/sandbox**.

This is a real, durable job-processing system on one physical Bigspark host. It exercises transactional acceptance, replicated messaging, leader election, expiring worker claims, retry limits and fenced completion. Jobs run bounded CPU work; they do not provision cloud instances.

## Deployment

```bash
python3 control-plane/configure.py
docker compose -f control-plane/compose.json up -d --build
systemctl --user restart datacenter-lab.service
```

`configure.py` is the common source for Compose, desired inventory, addressing and dependency definitions. It creates a local password in `.runtime/control-plane/db-password` (0600), mounted as a Docker secret. Never commit or export that file. Preserve it with the PostgreSQL volume; changing it alone does not change an initialized database password.

`./lab up` starts this stack, the original VMs, the configured local model and the mDNS-advertised website. `./lab down` stops the controller (allowing fault cleanup), then this stack, alongside the original environment. Volumes and evidence persist. Containers use `unless-stopped`, so an unplanned Docker/host restart can restart running services. The website's user service has its own lifecycle. No host reboot is required.

To stop only this stack, first cancel any active experiment in the UI and wait for its terminal report, then run `docker compose -f control-plane/compose.json stop`. Do not use `down -v` unless intentionally deleting job history and messaging data. `agent-search` and other projects are outside this stack.

## Addressing and components

All six networks are **internal Docker bridges**, separate from the existing 10.77.x Incus networks. Every gateway is `.1`; fixed endpoints are below `.128`; the dynamic pool is `.128–.254`.

| Bridge | CIDR | Primary residents |
|---|---|---|
| cp-edge | 10.78.10.0/24 | ingress .10 |
| cp-control | 10.78.40.0/24 | API .21/.22; schedulers .31/.32 |
| cp-compute | 10.78.20.0/24 | workers .21/.22/.23 |
| cp-messaging | 10.78.30.0/24 | NATS .11/.12/.13 |
| cp-data | 10.78.50.0/24 | PostgreSQL .11 |
| cp-observe | 10.78.0.0/24 | Prometheus .20 |

Services join additional networks as needed. For example, scheduler-1 has `.31` in control, data and messaging; the inspector exposes every actual IP/MAC attachment. Prometheus uses `.90` on its additional networks. The host reaches the ingress at **10.78.10.10:8080**. There are no published workload ports. The website remains reachable through the existing Nginx/mDNS route.

Network membership allows connectivity, including ports not shown as dependencies. This is not a default-deny per-port firewall. There is no dedicated router VM, VLAN isolation, packet capture, ACL hit counter or fabricated firewall hop. The host boundary is drawn as infrastructure, not as an independently probed application.

## Job lifecycle

1. Nginx distributes requests across two API processes. `POST /jobs` requires an `Idempotency-Key` and a text `payload` (at most 4096 characters). Repeating the key with the same payload returns the existing job; another payload returns 409.
2. PostgreSQL commits the job and an outbox row in one transaction before the API returns 202. PostgreSQL is authoritative for job state and lease time.
3. One of two schedulers holds PostgreSQL advisory lock 78001. It publishes pending outbox rows to the `JOBS` JetStream stream, then marks them published. A crash between those operations can redeliver; the outbox ID is also the NATS deduplication ID.
4. Three NATS members store a three-replica stream. Workers share one durable pull consumer with explicit acknowledgement. Delivery is at least once.
5. A worker atomically claims a queued job with a unique token and a 15-second database lease. It computes a bounded PBKDF2 result. Completion checks the matching token, running state and unexpired lease in the same database transaction that writes the unique completion row.
6. The scheduler requeues expired claims, clearing the old token and writing another outbox row. Five unsuccessful attempts exhaust the retry budget. A stale worker cannot overwrite its successor's completion.

This does **not** guarantee exactly-once execution or exactly-once arbitrary external side effects. The demo does not renew long-running work leases. Jobs are intentionally shorter than their lease. JetStream messages expire after 24 hours; this configuration is not an indefinite backlog service. PostgreSQL/outbox history is retained without an automatic purge.

## Experiments and evidence

The website offers five explicit, serialized experiments: API pause, active-worker pause past lease expiry, current NATS stream leader pause, current scheduler leader stop, and PostgreSQL pause. Each run:

- Requires all deployed baseline probes healthy; records image IDs and blueprint SHA-256.
- Submits 24 distinct jobs and repeats each idempotency key.
- Records actual fault and restore times, direct component probes, job progress, outbox depth and observed scheduler leader.
- Grades completion of all 24 jobs, duplicate-key equality, unique completion rows and final recovery health before the deadline.
- Additionally verifies a successor's lease token/owner for the worker test, observed standby election for the scheduler test, and completion of newly accepted work while the NATS stream leader remains paused. The NATS baseline records three replicas and checks replica currency at the leader.
- Restores its allowlisted target on completion, cancellation or error. A persisted armed-fault record allows the controller to restore a target after a controller restart.

Reports live in `.runtime/control-plane/run-*.json`. Failed, cancelled and interrupted reports remain visible. Timeline marks are discrete samples. Gaps have no observation; they are not filled with an assumed healthy state. The amber band records the fault interval separately. A passing NATS-loss test requires new work to complete during one member interruption; it is not a comprehensive Raft or durability proof. Clients use one-second pings and a two-missed-ping threshold to detect silent paused connections and reconnect. A database-outage pass means accepted work recovered, **not** that the database stayed available.

The seed fixes job payloads. Runtime scheduling and network timing remain nondeterministic. These short single-run checks are not statistically supported production SLO claims. No SLO burn chart is shown because no SLO target/window is defined.

## Observation and extension

`GET /api/system` exposes schema version 2: resources, groups, interfaces, dependencies, provenance, summary, measured metrics and experiment evidence. It excludes the operator action token and database secret. The old agent runtime retains its existing scoped tools for the original sandbox; it is not silently granted control over these containers.

- Desired: generated Compose inventory.
- Observed: Docker inspect, including paused/running/exited state, actual IP/MAC, image ID, limits and restart count.
- Measured: HTTP probes; PostgreSQL's narrower `pg_isready` check; application dependency counters; Prometheus rolling submit p99.

Configured dashed dependencies carry protocol/port/purpose. Solid instrumented dependencies show actual cumulative application activity, including probe-generated database connections; they are not per-link packets or business throughput. NATS cluster totals are not attributed to an arbitrary member. Lack of instrumentation is unknown, not policy drift.

Additional components can extend the generator and typed inventory. Network and Service views include fallback groups/roles; Placement uses the resource domain labels. New fault types must explicitly extend the controller allowlist, cleanup, evidence and grading, rather than passing arbitrary commands from the browser. Source authentication, real multi-host placement, firewall-policy collection, HA PostgreSQL, independent load balancers and agent tools for this source remain future adapters/components.

## Limits and operating footprint

There is one physical host, one PostgreSQL primary and one ingress. Labels a/b do not provide hardware fault isolation. Loss of PostgreSQL prevents new acceptance and worker commits until recovery. NATS quorum tolerates one member loss, not loss of the whole host. The local workload API and NATS endpoints trust bridge membership; this is not a multi-tenant production security boundary.

The core 13 containers have explicit CPU/memory limits and bounded log rotation. Initial idle resident usage was approximately 315 MiB in Docker's working-set reporting (not reserved memory or an upper bound). The sum of configured container memory limits is about 5.4 GiB. Persistent volumes hold PostgreSQL, JetStream and three days of Prometheus samples. Image tags are versioned but mutable; reports retain actual image IDs. This is not a bit-for-bit reproducible build across future image updates.

## Verification

```bash
.venv/bin/python -m pytest -q
node --check web/plane.js
.venv/bin/python tests/browser_plane.py
node tests/test_snapshot.cjs
.venv/bin/python tests/browser_diagram.py
```

The browser tests are read-only. To deliberately run all five real fault experiments, use `.venv/bin/python tests/live_plane_experiments.py --run-faults`. Fault experiments run only when explicitly requested through the authenticated local controller, and affect only the `bigspark-plane` project. They do not reboot the host or change the original VMs.

Recorded final results: [live validation](VALIDATION.md).

## Instance controls

Select a container and open Overview to turn it on, turn it off, or restart it. Restart affects the selected container, preserving its volumes; it does not reboot Bigspark. Host bridge interfaces and subnet objects have no power controls. Actions share the experiment lock, require the existing operator token/origin checks, and verify the container belongs to `bigspark-plane` before using its immutable Docker ID. Runtime state comes from Docker; health is re-probed afterward.

## Control Plane API releases and service incidents

The [API release controller](releases/README.md) deploys real versions of the
Control Plane's existing Job API replicas with Terraform. Its external canary
verifies job submission, idempotency and correct completion through ingress.
Select the Control Plane service or Job API in Lab to deploy, trigger an API
incident, restart, validate or roll back. Qwen remains a separate observer.

API Compose definitions are bootstrap-only. Normal startup uses `./lab up` so
Terraform controls the API images; the other components retain Compose ownership.
