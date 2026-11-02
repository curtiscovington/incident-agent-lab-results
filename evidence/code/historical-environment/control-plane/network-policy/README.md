# API database route policy

A fixed Terraform provisioner adds/removes a database /32 blackhole route only
inside cp-api-1 and cp-api-2 network namespaces, checking immutable container IDs
and project ownership first. Kernel routes are read back independently. It never
changes the host route table, database container or unrelated project resources.

The broker reconciles an active deny policy after API process restarts. Changing
the Terraform policy to allow is the repair; a restart is not a durable fix.
The policy workspace and worker releases are included in the Lab inventory.
