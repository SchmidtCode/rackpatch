# Agents and host maintenance

## Generated install and update commands

The `Settings` page exposes exact generated commands for agent install and update workflows. Those commands are built from:

- `RACKPATCH_PUBLIC_BASE_URL`
- `RACKPATCH_PUBLIC_REPO_URL`
- `RACKPATCH_PUBLIC_REPO_REF`
- `RACKPATCH_PUBLIC_AGENT_COMPOSE_DIR`
- `RACKPATCH_PUBLIC_RACKPATCH_COMPOSE_DIR`
- the current bootstrap token

Agent packaging modes:

- `compose`: installs under the configured agent compose directory and pulls `ghcr.io/.../rackpatch-agent:<tag>`
- `container`: installs under `/opt/rackpatch-agent` and runs a compose-managed published `rackpatch-agent` image
- `systemd`: installs under `/opt/rackpatch-agent` and runs `rackpatch-agent.service`

Compose and container agent updates pull the configured published tag by default. Existing source-built compose or container agent installs can still update in place, but fresh installs no longer need a local Docker build step. Compose and container self-updates now run from a short-lived helper container so the running agent does not try to replace its own container in place.

## Example install and update commands

Example container install:

```bash
RELEASE_TAG=v0.3.9
RELEASE_VERSION="${RELEASE_TAG#v}"

curl -fsSL "https://raw.githubusercontent.com/SchmidtCode/rackpatch/${RELEASE_TAG}/scripts/install-agent.sh" | bash -s -- \
  --server-url http://YOUR-RACKPATCH-HOST:3011 \
  --bootstrap-token YOUR_BOOTSTRAP_TOKEN \
  --mode container \
  --image "ghcr.io/schmidtcode/rackpatch-agent:${RELEASE_VERSION}"
```

Example stack update:

```bash
RELEASE_TAG=v0.3.9

curl -fsSL "https://raw.githubusercontent.com/SchmidtCode/rackpatch/${RELEASE_TAG}/scripts/update-rackpatch.sh" | bash -s -- \
  --install-dir /srv/compose/rackpatch \
  --repo-url https://github.com/SchmidtCode/rackpatch.git \
  --ref "${RELEASE_TAG}"
```

## Host-maintenance helper

Host maintenance is a separate opt-in step. The base agent install stays focused on enrollment and unprivileged operations. If you want limited host maintenance, run the dedicated helper enable script after the agent is installed and choose the preset or action list you want that node to expose.

Example host-maintenance enablement for guest and Docker-host package actions:

```bash
RELEASE_TAG=v0.3.9

curl -fsSL "https://raw.githubusercontent.com/SchmidtCode/rackpatch/${RELEASE_TAG}/scripts/enable-agent-host-maintenance.sh" | sudo bash -s -- \
  --mode compose \
  --preset packages \
  --compose-dir /srv/compose/rackpatch-agent \
  --install-source https://github.com/SchmidtCode/rackpatch.git \
  --install-ref "${RELEASE_TAG}"
```

Example host-maintenance enablement for Proxmox nodes:

```bash
RELEASE_TAG=v0.3.9

curl -fsSL "https://raw.githubusercontent.com/SchmidtCode/rackpatch/${RELEASE_TAG}/scripts/enable-agent-host-maintenance.sh" | sudo bash -s -- \
  --mode systemd \
  --preset proxmox \
  --install-source https://github.com/SchmidtCode/rackpatch.git \
  --install-ref "${RELEASE_TAG}"
```

If you want a custom mix, pass `--allow-actions package_check,package_patch,proxmox_patch,proxmox_reboot` with only the actions you want that node to advertise.

The helper exposes only approved host-maintenance actions. Package actions remain separate from Proxmox patch and reboot, and the agent advertises only the capabilities that are explicitly enabled on that node.

## Self-agent

If the rackpatch control-plane host is also an inventory host, the main stack can run an optional self-agent with:

```bash
docker compose --profile self-agent up -d agent
```

Set `RACKPATCH_SELF_AGENT_BOOTSTRAP_TOKEN` and `RACKPATCH_SELF_AGENT_NAME` in `.env` so that the self-agent enrolls as the matching inventory host, for example `core-vm`.

If that self-agent reports the same compose directory as `RACKPATCH_PUBLIC_RACKPATCH_COMPOSE_DIR`, the normal `Stacks` page live-update flow can update rackpatch itself. Those jobs use the release-aware rackpatch updater under the hood, so a new GitHub release can be applied from the standard stack update controls instead of a separate manual shell step.

## Live action behavior

The web UI treats package check and package patch as helper-gated actions. Hosts without the limited host-maintenance helper stay visible, but their package actions and package-job picker entries are greyed out until that access is enabled.

Package maintenance no longer falls back to the legacy worker or SSH path. Multi-host package requests fan out into one helper-backed agent job per eligible host.

Proxmox patch and Proxmox reboot are helper-gated too. Multi-node live Proxmox actions stay approval-gated so you can release nodes deliberately instead of having agents change several nodes at once.

Docker updates no longer fall back to the legacy worker path either. Live updates require an enrolled Docker-capable agent for each selected stack.

For Docker live updates:

- `backup_before: true` creates a lightweight archive of the stack project directory before `docker compose pull` and `up -d`.
- `snapshot_before` is ignored on the agent-driven Docker path and no longer blocks live updates.
- `Settings -> Docker Update Settings` controls how many backup runs are retained per stack and whether custom stack `backup_commands` are also executed.
- Backup archives live in the agent state directory, so they stay on the managed host rather than being copied back to the control plane.

For Proxmox soft reboots, rackpatch uses `soft_reboot_guest_order` from inventory and falls back to `guest_ids` when that order is not set.

`Patch Live` remains grey for helper-backed hosts that still require snapshot-before-patch. In the current rollout, helper-backed live patching only becomes eligible when the host advertises helper access and its inventory policy allows live patching without a pre-patch snapshot, which means `snapshot_class: none` for that host.

## Trust-sensitive privileged actions

- Base agent installs do not enable privileged host maintenance by default.
- Privileged host maintenance is enabled only by the dedicated helper setup step.
- The helper is limited to named maintenance actions such as package check, package patch, Proxmox patch, and Proxmox reboot.
- The helper does not accept arbitrary shell, free-form commands, package names, or paths from the control plane.
- The control-plane compose stack does not mount host SSH directories into the API or worker containers.
- Package check and package patch in the web UI are intentionally disabled on hosts that do not advertise the matching helper-backed capability.
- Proxmox patch and Proxmox reboot in the web UI are intentionally disabled on nodes that do not advertise the matching helper-backed capability.
- Package maintenance is agent-only now. If a host cannot satisfy helper or policy requirements, rackpatch rejects or skips that host instead of falling back to worker or SSH execution.
- Multi-node live Proxmox helper actions are intentionally kept approval-gated.
- Every future privileged action must have a named helper action, a dedicated root-owned wrapper, an explicit capability, and UI disclosure.
- Docker socket access is still a separate trust-sensitive capability and will be hardened in a later phase.
