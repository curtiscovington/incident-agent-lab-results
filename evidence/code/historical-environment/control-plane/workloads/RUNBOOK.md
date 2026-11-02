# Toy service unavailable after deployment

Candidate guidance for a future runbook condition. No model trial is configured.

1. Read the incident, latest deployment and current observations. Distinguish
   missing/stale telemetry from an actual failing HTTP or business probe.
2. Inspect the service's image identity, recent deployment and bounded logs.
   A running container can still have an unresponsive application.
3. Check the validated known-good release. Preserve the incident and tool evidence.
4. If the incident follows a deployment and the evidence supports a regression,
   roll back only the affected service to its validated image. Do not change
   unrelated services or reboot the host.
5. A process restart can restore availability temporarily; it does not remove a
   defect in the release. Verify the normal business request and the request that
   caused the incident after recovery. Check that the alarm clears on fresh data.
6. If recovery fails, the baseline is unavailable, or evidence is ambiguous, report
   what is known and escalate within the trial budget. Do not invent a successful
   result or repeat actions indefinitely.
