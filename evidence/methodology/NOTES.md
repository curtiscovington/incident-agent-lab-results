# Methodology and interpretation notes

Model: `qwen3.8:27b-q4_K_M`; digest
`25b843619e944cd0ae6069f94ff4e5e26a16e109ccbc0a66a0f05979ed70098e`.
Temperature 0, seed 42, context 8192, requested output cap 384 are frozen in
`data/study/protocol.json` and reused by both replays.

The offline estimate 10,397 counts the complete submitted message list with the
pinned HF tokenizer/chat template, generation prompt added and thinking disabled.
The exact saved provider response reports prompt_eval_count=8173 and eval_count=19,
with done_reason=length. These describe different stages: full offline input
versus the serving counter for the bounded request. They are not independent counts of the same retained prompt. The difference
is 2,224 input tokens. Exact agreement in 11 saved under-cap calibration cases
and in the matched 3,303-token budgeted request strongly supports serving-side
input truncation, in addition to the recorded response cutoff. The sum 8173+19=8192 and
cutoff text are recorded. Actual post-truncation input token IDs were not saved;
the exact omitted input cannot be reconstructed from the counters. Under-cap
offline/recorded calibration and all raw replay requests/responses are retained.

8192 is a configured study limit, not the documented hardware maximum of Spark
or the model. No audited artifact records why that value was originally chosen
or establishes that more context would not fit. The replay held it fixed to
test budgeting within the original configuration, not to optimize context size.

Fixed temperature and seed do not mean these live trials replayed identical
inputs. Frozen short_context includes capturedAt, resource measurements, alarm
records, business checks and queue observations. The saved serialized initial
contexts differ within each scenario/arm; hashes and timestamp examples are in
the audit JSON. Logs and tool results also arise from live asynchronous processes.
These establish changing inputs/timing; they do not establish which difference
caused a particular changed action. Serving nondeterminism was not separately
measured and should not be asserted as the observed cause.

Both arms had five length-limited replies, in five of eighty trials each.
Current: 5/916 replies; five of its 23 failed independent repair checks ended
with a length-limited parse-error reply. Runbook: 5/780 replies; four of its 13
failed checks did so. Trial 99 had a truncated final reply/agent parse error but
passed the independent repair check. Thus nine of 36 non-repaired trials had
that terminal truncation pattern, while ten replies were truncated overall.
The runbook adds prompt text. Equal observed truncation counts do not remove
headroom/history as a potential confound or prove it had no effect.

The 20 replay states are outcome-aware development selections: 13 from current,
7 from runbook, comprising five each database-lock, deployment, truncation and
successful-control states. Each case retains its source arm; all replay arms
for a case use the same original instructions. They are not 20 independent
held-out incidents and do not all come from the runbook arm.

Replay 1 (80 decisions, 85 calls): original-history valid JSON 15/20 and preferred
5/20; evidence+replan valid 20/20 and preferred 13/20; format retry final valid
19/20 and preferred 8/20 (25 calls); both valid 20/20 and preferred 13/20.
No arm chose the preferred database-lock diagnostic in 5 states. The evidence
arm changed both representation and instructions, so this replay did not isolate
whether shorter evidence helped by creating context room. The later budgeted
replay addresses that question within its stated controls and limitations.

Scenario repair counts and matrix counts are descriptive. Ten repetitions per
arm/scenario, shared faults/fixture/model/seed and selected five-state replay
strata do not warrant claims of general improvement/regression or population
significance. No significance test is reported for these descriptive comparisons.

Ingress means the Lab's nginx entry point in front of API replicas. A business
check POSTs a payload to /jobs twice with one Idempotency-Key, checks both map to
the same job ID, and polls GET /jobs/<id> up to 12 seconds at 0.3-second intervals.
The API commits a jobs row plus transactional outbox row in PostgreSQL; an elected
scheduler publishes the outbox record to NATS JetStream; a worker claims the job
with a lease token, computes PBKDF2-HMAC-SHA256(payload, control-plane-v1, 40000),
and records fenced completion in PostgreSQL. The check compares completed state
and exact expected result. This is bounded real Lab work, not arbitrary external
production workload or proof of sustained recovery.
Source: code/frozen-study/app/plane_releases.py:95-113 and
code/frozen-study/control-plane/service.py:63-81,92-115,124-159.
