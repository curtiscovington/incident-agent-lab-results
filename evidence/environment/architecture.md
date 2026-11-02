# Architecture of the studied control plane

This overview is derived from the study-frozen `control-plane/compose.json`,
`control-plane/service.py`, `app/plane_releases.py` and `app/plane_diagnosis.py`.
Historical supplementary definitions are identified separately in README.

```text
HTTP job client -> NGINX ingress -> API replicas -> PostgreSQL jobs + outbox
                                                   |
                       elected scheduler replicas -+
                                  |
                          NATS / JetStream (3 brokers)
                                  |
                         worker replicas (3 workers)
                                  |
                   PostgreSQL leased / fenced completion

Prometheus observes the services. The agent's tool broker inspects this system
and dispatches scoped repair operations; the model is not the job executor.
```

The API durably accepts bounded CPU-work jobs with idempotency keys. Schedulers
use database coordination and a transactional outbox to publish work. Workers
claim expiring leases and fence completion; the workload is a deterministic
PBKDF2 calculation, not cloud-instance provisioning. PostgreSQL, three NATS
members, two API replicas, two schedulers and three workers are actual Docker
services on one development host. Internal Docker bridges separate edge,
control, compute, messaging, data and observation paths; network fault injection
changes the scoped API/database connectivity policy. This is not an independent
multi-host production cluster.

The business-path check submits a job twice with the same idempotency key,
verifies stable job identity, polls for completion (up to 12 seconds), and checks
the deterministic result. It is separate from the HTTP health/probe path.
Independent recovery judging also checks fault removal and retained jobs (and
replays the failed submission for the two database faults). The agent's own
success claim is not the independent repair result.

Reset and checkpoint code in `app/recovery_study.py` captures/restores owned
container configuration and named fixture volumes between trials. The fixture
archives themselves are excluded. The model receives observations and a scoped
action vocabulary; hidden scenario truth and independent judge records are not
inserted into its prompt. Exact prompts, options and saved responses remain in
the evidence data.
