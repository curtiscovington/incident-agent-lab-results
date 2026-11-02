# Retired toy workload experiment

Echo and Checkout were the wrong failure target and have been removed, including
their internal Docker networks. This workspace is kept empty for Terraform state
history. Do not redeploy it for Control Plane incident tests.

Use [the actual Control Plane API release scenario](../releases/README.md).
Historical toy test evidence remains in `.runtime/workloads/`.
