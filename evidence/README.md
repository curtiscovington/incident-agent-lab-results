# Incident Agent Context Evidence v0.1.0

Saved protocols, results, provider responses, source references and reproducible analysis.

## Reproduce the saved-data analysis

Requires Python 3.10+ and only its standard library. From this directory:

```sh
python analysis.py
python analysis.py /tmp/incident-recomputed.json  # optional explicit output
```

The default run is read-only. It checks every package file hash, recomputes the
160-trial study summary using the frozen aggregation code, reconciles both
80-decision replays against raw submitted requests and provider responses,
revalidates JSON and the frozen action rubric, checks the three budgeted arms
and matched fact pools, and verifies the exact 8173+19=8192 cutoff record. It
performs no network calls, inference or repair-tool dispatch.

## Contents and integrity

`data/study` contains the scientific protocol, original aggregate summary and
160 per-trial judge/result records in JSONL. Deployment/trigger log copies are
omitted; the per-trial source hashes are retained. `data/replay-v1` and
`data/replay-v2` contain their protocols, 20 exact selected cases each, 80 saved
decision records each, all 165 submitted replay inference requests and 165 provider
responses in total, and the saved aggregate analyses. `evidence/t158-s07` makes
the exact cutoff and matched budgeted comparison easy to inspect.

`manifest.json` and `SHA256SUMS` identify package bytes. Original artifact hashes,
logical paths and transformations are in `provenance`. Original source files
were never edited. Historical source copies replace local-home paths and one
home-LAN address with explicit placeholders; study hardware hostname and the
tokenizer cache path are similarly masked. The original hashes remain recorded.
The complete scientific options, prompt text, rubrics, outputs and token counters
are preserved. Historical runner modules are audit source, not launch commands;
`analysis.py` compiles only their pure validator/scorer functions. Frozen study
source is included for audit; original source hashes identify the evaluated code.

## Inspect the architecture and action surface

See [`environment/README.md`](environment/README.md) for the architecture,
Terraform/service definitions, fault injection and agent-action source index.
The 16 study-frozen source files are retained unchanged from this package
version; 28 labeled historical supplementary files add supporting configuration,
build definitions, provider locks and documentation. Their provenance levels
are explicitly distinguished.

## What this can and cannot reproduce

This package reproduces analysis of the saved experiment and replay decisions.
It does not re-create the full infrastructure, restore a volume snapshot, rerun
160 incidents or download/serve the model. Model weights, tokenizer binaries,
credentials, private runtime/service state and fixture volume archives are not
included. New inference would require the pinned model/tokenizer, compatible
serving runtime and an explicitly configured isolated environment; full live
trial reproduction also needs the complete Lab setup/build/fixture tooling.
Included environment sources support inspection, not a complete live rerun.

The live study reuses known faults, model, seed and fixture. Replays use 20
outcome-aware selected development states and automatic next-action rubrics.
Their scores are not live recovery rates, human diagnosis adjudication or
held-out confirmation. Unlisted actions are review-required, not automatically
wrong. Budgeted history and normalized facts retain different information; only
the two fact-order arms hold the fact multiset and near-equal length fixed.

Original code is licensed under MIT; documentation and curated evidence/data
are licensed under CC BY 4.0. See `LICENSE.md` for exact path mapping, full texts
and exclusions. No model weights or third-party vendored code are bundled.


`methodology/validation.json` records condition-wise truncation counts,
replay-state provenance and original agent-artifact hashes. The audit counts
length-limited provider replies separately from failed independent repair checks.

## Original-study reply counts

`data/study/model-responses.jsonl` retains all 1,696 exact saved provider-response
objects from the 160 study trials, indexed by trial and response order. It adds
original agent-artifact hashes for provenance. It excludes full operational
agent snapshots and tool logs. Recompute reply denominators and length stops:

```sh
python count_replies.py
```

This joins provider replies to the independent trial results: current has 5/916
length-limited replies and runbook has 5/780. Five current-arm and four runbook-arm
unverified trials end with one; a tenth truncated reply belongs to independently
verified trial 99. All ten prompt-plus-output counters total 8,192.

## Replay condition names

| Article label | Frozen replay-2 arm ID |
| --- | --- |
| Original context | `replication` |
| Budgeted raw | `budget_raw` |
| Chronological facts | `facts_time` |
| Component-grouped facts | `facts_entity` |

Replay 1 calls the original-context condition `baseline`. Its submitted request
objects match replay 2's `replication` objects for all 20 cases: same messages,
model, options, schema and other request fields. Separate model calls were made;
this is input-condition equivalence, not a claim of byte-identical responses.

The current article figure specifications are under `methodology/matrix` and
`methodology/context`. Historical figure-reference sources are retained unchanged
under `code/figure-reference`; current specifications supersede their older
editorial labels. Raw scientific arm IDs remain unchanged in the saved records.

## Exact runbook and request coverage

`data/study/protocol.json` preserves the exact two frozen system prompts in
`prompts.current` and `prompts.runbook`. They match the original archived
protocol byte-for-byte as strings. The current prompt also equals `PROMPT` in
the frozen `app/incident_agent.py`. The added guidance is extracted verbatim as
`data/study/runbook-guidance.txt` from the frozen protocol, not a current loose
runbook file. `app/recovery_study.py` supplies the frozen protocol's prompt for
each condition when starting a trial. This is prompt
guidance supplied to the model, not an executed scripted recovery baseline.
Hashes and verification details are in `methodology/prompt-coverage.json`.

For the original live study, all 1,696 provider-response objects are included,
but the full per-call request message/tool histories for all those responses
are **not** included. There are 160 per-trial judge/result records and the
scientific protocol. The selected replay records include complete requests
and responses for 85 replay-1 calls and 80 replay-2 calls (165 paired calls),
plus the 20 selected decision states per replay. `evidence/t158-s07` is a
convenience copy of a selected replay request/response, not another original
live-study request packet. Provider-response counts must not be described as
1,696 complete request/response exchanges.

The frozen `code/frozen-study/app/main.py` remains authoritative despite its
difference from the historical supplemental revision. It is the Lab application
entry point; the job service implementation is the separately frozen
`code/frozen-study/control-plane/service.py`. No supplemental `app/main.py`
is included or substituted.
