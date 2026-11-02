# Terraform inventory and standalone incident agent

The Lab Architecture view is an interactive canvas. Drag background to pan,
scroll to zoom around the pointer, Shift-click nodes for multiple selection,
and drag to arrange. Fit selection, grouping, ungrouping, layout undo and an
expanded canvas are available. Arrow keys move a focused node; Enter opens
its inspector. Layout changes are saved per project/view on the controller.
Viewport position stays in the browser. Neither action changes infrastructure.

## Current ownership

This workspace records the existing 17 Control Plane containers, seven networks
and five persistent volumes in Terraform state. Existing resources were imported
without replacing, restarting or changing them. Their original configuration is
still authored in `control-plane/compose.json`: these imported resources use
`ignore_changes = all`, `must_run = false` for containers, and `prevent_destroy`.
This is **inventory adoption, not complete configuration migration**. It does not
claim to detect configuration drift, repair a stopped container, or recreate the
old stack from these minimal resource declarations. Do not remove those lifecycle
rules until the full desired configuration has been ported and its plan reviewed.
Do not destroy this workspace to remove only the observer.

Terraform fully manages the new `lab-incident-agent` container and its access
network. It has a pinned Python runtime, 64 MiB memory, 0.25 CPU, read-only root,
unprivileged user, no capabilities and no Docker socket or operator credential.
The standalone driver now uses a dedicated, read-only mounted credential for
`/api/incident-agent/*` on the host gateway. The scoped broker connects Qwen to
inspection and recovery tools for the 14 components inside the Control Plane
service. Other Lab routes are rejected for the agent. See
[agent architecture and operator workflow](../../agents/README.md).

The internal `cp-agent` bridge is 10.78.80.0/24, with agent address 10.78.80.11
and gateway 10.78.80.1. It has no direct workload or internet egress. Host gateway
services remain reachable; this is subnet isolation, not a per-port firewall.

Start the Lab controller once before applying the agent workspace: it creates
`.runtime/incident-agent/credential`, which Terraform mounts without putting its
contents in environment variables or state. The existing Qwen runtime stays on
the host. The agent container is the separately managed driver; the host broker
performs authorized tools and inference.

## Terraform workflow

Run commands from the repository root. `.runtime/bin/terraform` is the installed
CLI; state, exported values and machine-specific IDs are ignored by Git.

```bash
python3 control-plane/terraform/capture-existing.py
.runtime/bin/terraform -chdir=control-plane/terraform init
.runtime/bin/terraform -chdir=control-plane/terraform plan -out=inventory.tfplan
.runtime/bin/terraform -chdir=control-plane/terraform apply inventory.tfplan
.venv/bin/python scripts/publish-terraform-inventory.py \
  --project 552f9fd26c364741865fe02d00260dcd \
  --workspace control-plane/terraform
```

The capture step reads existing Docker identities only. It fails if a declared
resource cannot be found. The publisher consumes Terraform's documented
`show -json` format and saves only resource addresses, types, plan actions and
state membership. It excludes raw values, outputs, variables and credentials.
The UI shows the timestamp; saved state membership is not a current health probe.
Publishing a new export updates the inventory on the next UI refresh.

For a proposed plan, append `--plan inventory.tfplan`. Any project can also use
**Terraform inventory → Import Terraform JSON** with a `terraform show -json`
export. Nested modules and other providers are supported for inventory; this does
not install or execute arbitrary providers through the web UI. Create, update,
replace, remove, import and unchanged actions are shown separately. Imported
plans are reviewable snapshots, never automatically applied.

## New deployments and faults

`infra/modules/deployment` is a reusable Docker module accepting a map of images,
commands, environment values, memory, CPU and enabled flags. Each module instance
owns a named, internal network. This is a container deployment building block,
not a universal cloud provisioning or fault-injection engine. Other Terraform
providers can publish their inventory through the same JSON interface.

`infra/scenarios/http-incident` supplies a small real deployment with two variable
files: healthy requests return HTTP 200; the bad deployment returns HTTP 500.
Each configuration is applied through an explicit saved Terraform plan:

```bash
.runtime/bin/terraform -chdir=infra/scenarios/http-incident init
.runtime/bin/terraform -chdir=infra/scenarios/http-incident plan \
  -var-file=healthy.tfvars -out=healthy.tfplan
.runtime/bin/terraform -chdir=infra/scenarios/http-incident apply healthy.tfplan
.runtime/bin/terraform -chdir=infra/scenarios/http-incident plan \
  -var-file=bad-deployment.tfvars -out=bad.tfplan
.runtime/bin/terraform -chdir=infra/scenarios/http-incident apply bad.tfplan
```

Reapply a freshly generated healthy plan to recover. Use separate working
copies/state files and deployment names for concurrent configurations. Images
must exist on the target Docker host. The example's image ID is pinned to this
host; override it for another host. No public workload ports are opened.

The visible **HTTP incident scenario** project contains the saved healthy plan,
with its service and network marked **not deployed**. Its project ID is
`43b820817d924d21bc9f52759c48c94e`.

Validation: both workspaces validate; the live Control Plane adoption imported
29 existing resources and created two agent resources without changing the old
stack. A follow-up plan was empty. The scenario was deployed once healthy (HTTP
200) and once broken (HTTP 500), then its two test resources were destroyed. This
is a deployment smoke test, not an agent-recovery experiment or reliability claim.


## Subnet canvas and service instances

The architecture canvas groups resources by primary subnet, with CIDR, bridge
name and actual internal/egress state. Secondary network attachments are shown
on cards and in instance details. A stopped instance remains in its configured
primary subnet and retains its intended address; its runtime state is explicit.
APIs, schedulers, NATS and workers are service nodes with clickable instance rows.
Each row retains its own runtime/health status and instance controls.

Host-based health monitoring, alarms and the loopback-only model service live in
a separate host boundary. They are not depicted as containers attached to a
workload subnet. Drag a subnet title to arrange its members together, or use
Arrange subnets to restore the default layout (undoable). Visual movement never
rewires a network. The pre-subnet layout was preserved in the project's local
`architecture-before-subnets.json` backup.

## Application abstraction

The Control Plane architecture opens as one collapsed service node. Expand
Control Plane service (or double-click its node) to reveal the logical service boundary
with its subnet portions, services and clickable instances. Collapse Control Plane service
returns to the application view. The preference and camera are remembered in the
browser; overview and expanded positions are stored separately in the layout.

Application membership includes ingress, APIs, PostgreSQL, schedulers, NATS,
workers, Prometheus and the authorized catalog client (14 container instances).
The catalog, its private access endpoint, the unapproved test client, incident
agent, Qwen model and host health/alarm services remain external. This is logical
ownership, not a new network or a change to runtime isolation. A subnet that crosses
the ownership boundary is marked shared on both portions. External connections
are retained when the application collapses; internal connections are hidden.
Aggregate health uses application members only and reports unknown for stale
observations. Expanding preserves the previous instance health and controls.

## API ownership migration

The two Job API replicas are now fully managed in `../releases`; this adoption
workspace excludes them and tracks 29 objects. The combined Lab inventory has
31 resources. See [API release documentation](../releases/README.md).
