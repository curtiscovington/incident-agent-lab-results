# Worker release workspace

Owns cp-worker-1 through cp-worker-3. The adoption workspace no longer owns them.
`build.py` builds w1 (compatible envelope decoder) and w2 (incompatible decoder)
from installed dependencies. Runtime operations select only these built image
IDs; the agent can only restore w1, not supply arbitrary images or Terraform.
The controller saves and scope-checks a Terraform plan before applying it.

The current build artifacts and operations are under `.runtime/plane-diagnosis`.
Do not rebuild an existing release name for a new study: use new version IDs and
update the scenario definition so existing frozen protocols remain meaningful.
