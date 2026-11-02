# Fault injection and agent action surface

The authoritative implementations are study-frozen sources. The table summarizes
the injected condition, not a promise that the agent selected the listed repair.

| Scenario | Injection and intended repair mechanism |
| --- | --- |
| API regression | API release regression; known-good API release rollback through the release controller. See `app/plane_releases.py` and `app/recovery_study.py`. |
| Worker backlog | Deploy worker release w2, submit retained work; worker rollback dispatches deployment of w1. |
| Database route | Submit work, deny API/database connectivity and retain a failed submission; restore scoped network policy. |
| Database lock | Hold the experiment's maintenance database lock, retain a failed submission; release the scoped lock holder. |
| Scheduler outage | Stop both schedulers, submit jobs; restore scheduler power. |
| Scheduler lease | Hold the experiment's scheduler lease and submit jobs; release that scoped lease holder. |
| Broker quorum | Stop nats-2 and nats-3, submit jobs; restore broker members. |
| Worker loss | Stop worker-1 and submit jobs; restore the worker without changing its release. |

`app/plane_diagnosis.py:inject` defines seven diagnosis faults;
`app/recovery_study.py` / `app/plane_releases.py` handle the API regression.
`plane_diagnosis.py:judge` checks removal plus the agent-independent business
path and retained jobs; `recovery_study.py:judge` routes the relevant check.

## Scoped dispatcher

`app/incident_agent.py:dispatch` implements these tools:

- Observation: `inspect`, `logs`, `metrics`, `probe`, `connectivity`,
  `network_policy`, `database_activity`, `messaging_status`.
- Mutation: `power`, `rollback`, `restore_network`, `release_database_lock`,
  `release_scheduler_lease`.
- Verification: `verify`, combining observed service health, missing-resource
  checks and the actual job business-path check.

Targets and actions are validated in the frozen dispatcher and helpers.
Database tools are scoped to `postgres`; scheduler-lease restoration to
`schedulers`; network restoration to `job-api`; worker rollback to w1; API
rollback to the API release controller. There is no arbitrary shell tool in
this dispatcher. The implementation includes real subprocess/controller calls
and bounded waits; tool latency includes those operations and observation,
not just model inference. See exact code for error and timeout semantics.

Release/policy operations use Terraform or the scoped controller implementation;
this bundle includes those sources to make the action semantics inspectable.
It does not include the runtime secrets, images or volume snapshots needed to
execute them safely in a new live installation.
