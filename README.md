# Incident Agent Lab Results

Protocols, saved results, source and reproducible analysis for
[Can a Local AI Agent Handle an Incident?](https://curtiscovington.com/posts/incident-agent-context/).

This study explores what a local model can do when investigating known faults
and choosing bounded repair actions in a development lab. It does not compare
the agent with scripted automation or establish production incident reliability.

[Study protocol](evidence/data/study/protocol.json) ·
[160 trial records](evidence/data/study/trials.jsonl) ·
[Aggregate results](evidence/data/study/summary.json) ·
[Architecture and source index](evidence/environment/README.md) ·
[Reproduction instructions](REPRODUCING.md)

## Results and scope

Immediate independently verified recoveries were **57/80 without added runbook
guidance and 67/80 with it**, across eight known faults with ten trials per
condition per fault. These are repeated executions with a shared fixture and
fixed model/seed, not independent samples of unfamiliar production incidents.

Two development replays reuse twenty outcome-aware selected states. Replay-2
preferred-action counts were 5/20 for original context, 9/20 for budgeted raw,
13/20 for chronological facts and 11/20 for component-grouped facts. These are
automatic next-action scores; no replay repair tool was dispatched.

The model was pinned to `qwen3.8:27b-q4_K_M`, digest
`25b843619e944cd0ae6069f94ff4e5e26a16e109ccbc0a66a0f05979ed70098e`.
Exact options, prompts, limitations and provenance remain in the protocol.

## Reproduce the saved-data analysis

Verified on Linux with Python 3.10+ and its standard library:

```sh
python3 evidence/analysis.py
python3 evidence/count_replies.py
```

The default runs are read-only. They validate package integrity and recompute
saved results without network calls, inference or tool dispatch. See
[REPRODUCING.md](REPRODUCING.md) for expected checks and boundaries.

## What is included

- `evidence/data/study/`: the scientific protocol, 160 trial results, aggregate
  results, exact runbook guidance and 1,696 original provider-response objects.
- `evidence/data/replay-v1/` and `evidence/data/replay-v2/`: twenty selected states
  per replay, decision results and 165 complete request/response pairs in total.
- `evidence/evidence/t158-s07/`: a convenience copy of the cutoff replay example.
- `evidence/code/frozen-study/`: sixteen sources archived by the original study,
  including Terraform, service implementation, faults, judging and agent tools.
- `evidence/code/historical-environment/`: twenty-eight separately labeled
  historical supporting files: SQL, proxy/messaging/metrics configuration,
  Docker builds, dependency declarations, provider locks and documentation.
- `evidence/environment/`, `evidence/methodology/`, `evidence/provenance/`:
  architecture/action guides, validation records and original/export hashes.
- `evidence/code/historical-replay/` and `evidence/code/figure-reference/`:
  historical replay and figure source for inspection.

The full original per-call request histories are not included alongside the
1,696 live-study responses. Request coverage is explained in the
[evidence README](evidence/README.md#exact-runbook-and-request-coverage).

## Inspectable environment, not a complete live rerun

The environment source makes the studied architecture, fault injection,
scaffolded action surface and verification inspectable. The frozen source is
authoritative. The historical supplemental Git revision matches fifteen of
sixteen frozen files, but differs in `app/main.py`; that supplement is not proof
that every supporting file was the exact study-runtime version.

This repository excludes model/tokenizer binaries, container/provider
executables, credentials, private runtime state, initialized fixture volumes
and unrelated Lab projects. It is not the full Lab application or a turnkey
live experiment distribution. No new inference or live rerun is needed to
recompute the saved-data results.

## Provenance and licenses

The `evidence/` directory is a byte-for-byte extraction of the previously published
reviewed v0.1.0 ZIP. Its original manifest and checksums remain valid. See
[PROVENANCE.md](PROVENANCE.md) and [import-provenance.json](import-provenance.json).

Original code/configuration is MIT-licensed; documentation and curated evidence
are CC BY 4.0, with the original exclusions and third-party rights unchanged.
See [LICENSE.md](LICENSE.md) for the full mapping and license texts.
