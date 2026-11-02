# Reproducing the saved analysis

Run from the repository root on Linux with Python 3.10+:

```sh
python3 evidence/analysis.py
python3 evidence/count_replies.py
```

Only Python's standard library is required. No installation, network access,
model server, credentials, Docker, Terraform or live Lab is used by these two
commands. The default commands print JSON without modifying evidence files.

`analysis.py` checks the complete package file set and every manifest hash,
recomputes the 160-trial aggregation using frozen pure functions, reconciles
both saved replays, checks fact-pool/length controls, and verifies the recorded
8,173 + 19 = 8,192 cutoff and 10,397-token submitted-prompt estimate. Expected
live totals are 57/80 and 67/80; replay-1 has 80 decisions from 85 calls and
replay-2 has 80 decisions from 80 calls.

`count_replies.py` joins all 1,696 saved provider responses with independent
trial results. It reports 5/916 length-limited replies without added runbook
guidance and 5/780 with it; five and four unverified trials respectively end
with one. The tenth truncated reply belongs to a separately verified trial.

The exact runbook guidance is in `evidence/data/study/runbook-guidance.txt`,
extracted from the archived protocol. The full system prompts are under
`prompts.current` and `prompts.runbook` in that protocol. The package contains
all original provider responses, not all original live request histories;
the 165 complete request/response pairs belong to the saved replays.

To inspect the environment rather than run it, start with
[`evidence/environment/README.md`](evidence/environment/README.md).
Historical Terraform and setup commands are audit source, not a supported
one-command live rerun. Recreating live trials would need an independently
prepared isolated environment, pinned model/runtime/dependencies, new secrets
and compatible fixture state that are not distributed here.

The protocol limitations still apply: known development faults, a tailored
runbook, shared fixtures and seed, outcome-aware replay-state selection,
automatic action rubrics and no sustained production recovery claim.
