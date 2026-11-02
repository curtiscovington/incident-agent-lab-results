# Control Plane job submission incident

Candidate runbook for a future model condition; no model experiment has run.

1. Inspect fresh external job-submission and completion evidence. A healthy
   ingress process or a running API container does not establish service health.
2. Compare API replica health, recent release changes and logs. Inspect scheduler,
   messaging, worker and database evidence before attributing every failure to APIs.
3. If the evidence supports an API deployment regression, select the validated
   known-good image and roll back only the affected API component.
4. Restart can be a temporary restoration step. It does not fix faulty code.
   Verify a real job through ingress and the failing input after recovery.
5. Confirm fresh successful completion results and alarm resolution. Report what
   changed, supporting evidence, and remaining uncertainty. Escalate when the
   baseline is unavailable or the allowed recovery fails; do not reboot the host
   or mutate unrelated services.
