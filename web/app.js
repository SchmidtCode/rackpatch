import { createApiClient } from "./api.js";
import { createDelegatedHandler, debounce, withAsyncAction } from "./events.js";
import { createState, EMPTY_DOCKER_UPDATES, normalizeSelection, PAGE_META } from "./store.js";

const state = createState();
const sessionState = state.session;
const uiState = state.ui;
const jobFormState = state.jobForm;
const selectionState = state.selection;
const entitiesState = state.entities;
const api = createApiClient(() => sessionState.token);

const loginScreen = document.getElementById("login-screen");
const appScreen = document.getElementById("app-screen");
const loginForm = document.getElementById("login-form");
const loginError = document.getElementById("login-error");
const flash = document.getElementById("flash");
const jobResult = document.getElementById("job-result");
const jobEvents = document.getElementById("job-events");
const pageTitle = document.getElementById("page-title");
const pageKicker = document.getElementById("page-kicker");
const siteChip = document.getElementById("site-chip");
const hostForm = document.getElementById("host-form");
const hostFormResetButton = document.getElementById("host-form-reset");
const hostFormResult = document.getElementById("host-form-result");
const hostEditorMeta = document.getElementById("host-editor-meta");
const hostGroupsDataList = document.getElementById("host-groups");
const jobLogPanel = document.getElementById("job-log-panel");
const jobLogExpandButton = document.getElementById("job-log-expand");
const jobKindSelect = document.getElementById("job-kind");
const jobTargetSummary = document.getElementById("job-target-summary");
const jobStackPicker = document.getElementById("job-stack-picker");
const jobStackToggle = document.getElementById("job-stack-toggle");
const jobStackMenu = document.getElementById("job-stack-menu");
const jobStackOptions = document.getElementById("job-stack-options");
const jobStackStatus = document.getElementById("job-stack-status");
const jobHostPicker = document.getElementById("job-host-picker");
const jobHostToggle = document.getElementById("job-host-toggle");
const jobHostMenu = document.getElementById("job-host-menu");
const jobHostOptions = document.getElementById("job-host-options");
const jobHostStatus = document.getElementById("job-host-status");
const jobManualTargetWrap = document.getElementById("job-manual-target-wrap");
const jobManualTargetLabel = document.getElementById("job-manual-target-label");
const jobManualTargetInput = document.getElementById("job-manual-target");
const jobOptions = document.getElementById("job-options");
const stacksSummary = document.getElementById("stacks-summary");
const overviewRelease = document.getElementById("overview-release");
const automationApi = document.getElementById("automation-api");
const automationLive = document.getElementById("automation-live");
const releaseStatus = document.getElementById("release-status");
const releaseUpdateCommands = document.getElementById("release-update-commands");
const fleetUpdateSummary = document.getElementById("fleet-update-summary");
const fleetUpdateCommands = document.getElementById("fleet-update-commands");
const fleetUpdateQueueButton = document.getElementById("fleet-update-queue");
const fleetUpdateCopyButton = document.getElementById("fleet-update-copy");
const appVersionNodes = document.querySelectorAll("[data-app-version]");
const DEFAULT_HOST_GROUPS = ["all", "docker_hosts", "guests", "proxmox_nodes"];

const FALLBACK_JOB_KIND = {
  kind: "docker_update",
  label: "Docker update",
  mode: "stack_multi",
  target_type: "stack",
  summary: "Select one or more stacks to update through enrolled agents.",
  defaults: { executor: "agent", window: "all", dry_run: true, requires_approval: false },
  default_select_all: true,
  fields: [],
};

function applyAppVersion(version = {}) {
  const appName = String(version.app_name || "rackpatch").trim() || "rackpatch";
  const appVersion = `v${String(version.app_version || "unknown").trim() || "unknown"}`;
  const displayName = String(version.app_display_name || `${appName} ${appVersion}`).trim() || appName;

  appVersionNodes.forEach((node) => {
    node.textContent = appVersion;
  });
  document.title = displayName;
}

function getJobKindItems() {
  return entitiesState.jobKinds?.items || entitiesState.context?.job_kinds || [];
}

function populateJobKindSelect() {
  const items = getJobKindItems();
  if (!items.length) {
    jobKindSelect.innerHTML = `<option value="${escapeHtml(FALLBACK_JOB_KIND.kind)}">${escapeHtml(FALLBACK_JOB_KIND.label)}</option>`;
    jobKindSelect.disabled = true;
    return;
  }

  const current = jobFormState.kind || jobKindSelect.value;
  jobKindSelect.innerHTML = items
    .map((item) => `<option value="${escapeHtml(item.kind)}">${escapeHtml(item.label || item.kind)}</option>`)
    .join("");
  jobKindSelect.disabled = false;
  if (items.some((item) => item.kind === current)) {
    jobKindSelect.value = current;
    return;
  }
  jobKindSelect.value = items[0].kind;
}

async function loadAppVersion() {
  try {
    const version = await api("/api/v1/version");
    applyAppVersion(version);
    return version;
  } catch (_) {
    applyAppVersion();
    return null;
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatTimestamp(value) {
  if (!value) {
    return "n/a";
  }
  return new Date(value).toLocaleString();
}

function formatBytes(value) {
  if (value === null || value === undefined || value === "") {
    return "n/a";
  }
  const size = Number(value);
  if (!Number.isFinite(size) || size < 0) {
    return "n/a";
  }
  if (size < 1024) {
    return `${size} B`;
  }
  const units = ["KB", "MB", "GB", "TB"];
  let scaled = size / 1024;
  let unitIndex = 0;
  while (scaled >= 1024 && unitIndex < units.length - 1) {
    scaled /= 1024;
    unitIndex += 1;
  }
  return `${scaled.toFixed(scaled >= 10 ? 0 : 1)} ${units[unitIndex]}`;
}

function shortId(value) {
  return String(value || "").slice(0, 8);
}

function badge(value, flavor = "") {
  const label = escapeHtml(value || "unknown");
  const className = flavor ? `badge ${flavor}` : "badge";
  return `<span class="${className}">${label}</span>`;
}

function statusBadge(status) {
  const lower = String(status || "").toLowerCase();
  if (["completed", "online", "enabled", "approved", "current", "up-to-date", "up to date"].includes(lower)) {
    return badge(status, "good");
  }
  if (["failed", "offline", "pending_approval", "pending approval", "pending", "outdated", "warning"].includes(lower)) {
    return badge(status, "warn");
  }
  return badge(status, "accent");
}

function releaseLabel(value) {
  const lower = String(value || "unknown").toLowerCase();
  if (lower === "current") {
    return "Current";
  }
  if (lower === "outdated") {
    return "Outdated";
  }
  if (lower === "ahead") {
    return "Ahead";
  }
  if (lower === "different") {
    return "Different";
  }
  return value || "Unknown";
}

function pluralize(count, singular, plural = `${singular}s`) {
  return `${count} ${count === 1 ? singular : plural}`;
}

function getDockerUpdateItems() {
  return entitiesState.dockerUpdates?.items || [];
}

function selectedStackNames() {
  const available = getDockerUpdateItems().map((item) => item.name);
  return normalizeSelection(selectionState.stacks, available);
}

function isStackSelected(name) {
  return selectedStackNames().includes(name);
}

function setSelectedStacks(names) {
  const allowed = getDockerUpdateItems()
    .filter((item) => item.selection_eligible)
    .map((item) => item.name);
  selectionState.stacks = normalizeSelection(names, allowed);
}

function toggleStackSelection(name, checked) {
  const next = new Set(selectedStackNames());
  if (checked) {
    next.add(name);
  } else {
    next.delete(name);
  }
  setSelectedStacks([...next]);
  renderStacks();
}

function getBackupItems() {
  return entitiesState.backups?.items || [];
}

function getJobItems() {
  return entitiesState.jobs?.items || [];
}

function canDeleteJob(job) {
  return Boolean(job?.deletable) || ["completed", "failed", "cancelled"].includes(String(job?.status || "").toLowerCase());
}

function selectedJobIds() {
  const allowed = getJobItems()
    .filter((item) => canDeleteJob(item))
    .map((item) => item.id);
  return normalizeSelection(selectionState.jobs, allowed);
}

function isJobSelected(id) {
  return selectedJobIds().includes(id);
}

function setSelectedJobs(ids) {
  const allowed = getJobItems()
    .filter((item) => canDeleteJob(item))
    .map((item) => item.id);
  selectionState.jobs = normalizeSelection(ids, allowed);
}

function toggleJobSelection(id, checked) {
  const next = new Set(selectedJobIds());
  if (checked) {
    next.add(id);
  } else {
    next.delete(id);
  }
  setSelectedJobs([...next]);
  renderJobs();
}

function selectedBackupIds() {
  const available = getBackupItems().map((item) => item.id);
  return normalizeSelection(selectionState.backups, available);
}

function isBackupSelected(id) {
  return selectedBackupIds().includes(id);
}

function setSelectedBackups(ids) {
  const allowed = getBackupItems().map((item) => item.id);
  selectionState.backups = normalizeSelection(ids, allowed);
}

function toggleBackupSelection(id, checked) {
  const next = new Set(selectedBackupIds());
  if (checked) {
    next.add(id);
  } else {
    next.delete(id);
  }
  setSelectedBackups([...next]);
  renderBackups();
}

function toggleStackDetails(name) {
  uiState.expandedStacks[name] = !uiState.expandedStacks[name];
  renderStacks();
}

function inspectionStateLabel(stateValue) {
  const value = String(stateValue || "unknown");
  if (value === "up-to-date") {
    return "Up to date";
  }
  if (value === "never_checked") {
    return "Not checked";
  }
  if (value === "pending_approval") {
    return "Pending approval";
  }
  if (value === "no-images") {
    return "No images";
  }
  return value.replaceAll("_", " ").replaceAll("-", " ");
}

function renderInspectionServices(report) {
  const services = Array.isArray(report?.services) ? report.services : [];
  if (!services.length) {
    return `<div class="empty">No per-service details yet.</div>`;
  }
  return `
    <div class="stack-detail-list">
      ${services
        .map((service) => {
          const ref = escapeHtml(service.ref || "n/a");
          const local = escapeHtml(service.local_short || "unknown");
          const remote = escapeHtml(service.remote_short || "unknown");
          const error = service.error ? `<span class="subline">${escapeHtml(service.error)}</span>` : "";
          return `
            <div class="stack-detail-row">
              <div>
                <strong>${escapeHtml(service.service || "unknown")}</strong>
                <span class="subline mono">${ref}</span>
                ${error}
              </div>
              <div>${statusBadge(inspectionStateLabel(service.status || "unknown"))}</div>
              <div>
                <span class="subline">Local ${local}</span>
                <span class="subline">Registry ${remote}</span>
              </div>
            </div>
          `;
        })
        .join("")}
    </div>
  `;
}

function emptyState(message = "Nothing here yet.") {
  return `<div class="empty">${escapeHtml(message)}</div>`;
}

function renderTable(rootId, headers, rows, emptyMessage) {
  const root = document.getElementById(rootId);
  if (!rows.length) {
    root.innerHTML = emptyState(emptyMessage);
    return;
  }
  root.innerHTML = `
    <table class="data-table">
      <thead>
        <tr>${headers.map((header) => `<th>${escapeHtml(header)}</th>`).join("")}</tr>
      </thead>
      <tbody>${rows.join("")}</tbody>
    </table>
  `;
}

function showFlash(message, type = "success") {
  flash.textContent = message;
  flash.classList.remove("hidden", "success", "error");
  flash.classList.add(type);
  clearTimeout(uiState.flashTimer);
  uiState.flashTimer = window.setTimeout(() => {
    flash.classList.add("hidden");
  }, 3500);
}

function syncPageFromHash() {
  const page = window.location.hash.replace(/^#/, "") || "overview";
  uiState.currentPage = PAGE_META[page] ? page : "overview";
  applyPageState();
}

function setPage(page) {
  if (!PAGE_META[page]) {
    return;
  }
  uiState.currentPage = page;
  window.location.hash = page;
  applyPageState();
}

function applyPageState() {
  const meta = PAGE_META[uiState.currentPage] || PAGE_META.overview;
  pageTitle.textContent = meta.title;
  pageKicker.textContent = meta.kicker;

  document.querySelectorAll("[data-page]").forEach((node) => {
    node.classList.toggle("active", node.dataset.page === uiState.currentPage);
  });
  document.querySelectorAll("[data-page-link]").forEach((node) => {
    node.classList.toggle("active", node.dataset.pageLink === uiState.currentPage);
  });
  const activeLink = document.querySelector(`[data-page-link="${uiState.currentPage}"]`);
  if (activeLink && window.matchMedia("(max-width: 760px)").matches) {
    activeLink.scrollIntoView({ block: "nearest", inline: "center", behavior: "smooth" });
  }
}

function setInputValue(id, value) {
  const element = document.getElementById(id);
  if (document.activeElement === element) {
    return;
  }
  element.value = value;
}

function setCheckboxValue(id, checked) {
  const element = document.getElementById(id);
  if (document.activeElement === element) {
    return;
  }
  element.checked = Boolean(checked);
}

function currentHostEditorName() {
  return hostForm?.dataset.originalName || "";
}

function parseCsvIntegers(value) {
  return String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
    .join(",");
}

function hostFormPayload() {
  return {
    name: document.getElementById("host-name").value.trim(),
    group: document.getElementById("host-group").value.trim() || "all",
    ansible_host: document.getElementById("host-ansible-host").value.trim(),
    ansible_user: document.getElementById("host-ansible-user").value.trim(),
    compose_root: document.getElementById("host-compose-root").value.trim(),
    maintenance_tier: document.getElementById("host-maintenance-tier").value.trim(),
    proxmox_node_name: document.getElementById("host-proxmox-node-name").value.trim(),
    guest_type: document.getElementById("host-guest-type").value.trim(),
    proxmox_guest_id: document.getElementById("host-proxmox-guest-id").value.trim(),
    guest_ids: parseCsvIntegers(document.getElementById("host-guest-ids").value),
    soft_reboot_guest_order: parseCsvIntegers(document.getElementById("host-soft-reboot-guest-order").value),
    rackpatch_control_plane: document.getElementById("host-control-plane").checked,
  };
}

function populateHostEditor(host = null) {
  hostForm.dataset.originalName = host?.name || "";
  setInputValue("host-name", host?.name || "");
  setInputValue("host-group", host?.group || "all");
  setInputValue("host-ansible-host", host?.ansible_host || "");
  setInputValue("host-ansible-user", host?.ansible_user || "");
  setInputValue("host-compose-root", host?.compose_root || "");
  setInputValue("host-maintenance-tier", host?.maintenance_tier || "");
  setInputValue("host-proxmox-node-name", host?.proxmox_node_name || "");
  setInputValue("host-guest-type", host?.guest_type || "");
  setInputValue("host-proxmox-guest-id", host?.proxmox_guest_id || "");
  setInputValue("host-guest-ids", Array.isArray(host?.guest_ids) ? host.guest_ids.join(",") : "");
  setInputValue(
    "host-soft-reboot-guest-order",
    Array.isArray(host?.soft_reboot_guest_order) ? host.soft_reboot_guest_order.join(",") : ""
  );
  setCheckboxValue("host-control-plane", Boolean(host?.rackpatch_control_plane));
  hostFormResult.textContent = host
    ? `Editing ${host.name}. Save to update ${entitiesState.hosts.inventory_path || "inventory/hosts.yml"}.`
    : "Create a host entry in the active site inventory.";
}

function syncHostEditorOptions() {
  const reportedGroups = Array.isArray(entitiesState.hosts?.groups) ? entitiesState.hosts.groups : [];
  const groups = [...new Set([...DEFAULT_HOST_GROUPS, ...reportedGroups].filter(Boolean))].sort((left, right) =>
    left.localeCompare(right)
  );
  if (hostGroupsDataList) {
    hostGroupsDataList.innerHTML = groups.map((group) => `<option value="${escapeHtml(group)}"></option>`).join("");
  }
  if (hostEditorMeta) {
    hostEditorMeta.textContent = [
      `Inventory file: ${entitiesState.hosts?.inventory_path || entitiesState.settings?.inventory_path || "unknown"}`,
      `Known groups: ${groups.join(", ") || "none"}`,
      currentHostEditorName()
        ? `Current edit target: ${currentHostEditorName()}`
        : "New hosts are written back to the active site inventory.",
    ].join("\n");
  }
}

function formatDockerVersion(ref, short) {
  const refLabel = String(ref || "").trim();
  const shortLabel = String(short || "").trim();
  if (refLabel && shortLabel && shortLabel !== "unknown") {
    return `${refLabel} (${shortLabel})`;
  }
  if (refLabel) {
    return refLabel;
  }
  if (shortLabel) {
    return shortLabel;
  }
  return "unknown";
}

function hostMaintenanceInfo(agent) {
  const payload = agent?.metadata?.host_maintenance || {};
  const actions = Array.isArray(payload.actions)
    ? payload.actions.map((item) => String(item).replaceAll("_", " "))
    : [];
  const detail =
    payload.detail ||
    (actions.length
      ? `Limited to approved maintenance actions: ${actions.join(", ")}.`
      : "Host maintenance helper not enabled.");
  return { actions, detail };
}

const LEGACY_CAPABILITY_DISPLAY_ALIASES = {
  package_check: "host-package-check",
  "sudo-packages": "host-package-patch",
};

function normalizeDisplayedCapabilities(values) {
  const list = Array.isArray(values) ? values : [];
  const deduped = [];
  const seen = new Set();
  list.forEach((value) => {
    const normalized = LEGACY_CAPABILITY_DISPLAY_ALIASES[String(value)] || String(value || "").trim();
    if (!normalized || seen.has(normalized)) {
      return;
    }
    seen.add(normalized);
    deduped.push(normalized);
  });
  return deduped;
}

function getJobSpecialAccess(kind) {
  return getJobKindConfig(kind).special_access || null;
}

function getEffectiveJobOptionValues(kind) {
  return {
    ...getJobKindConfig(kind).defaults,
    ...getStoredJobOptionValues(kind),
    ...getRenderedJobOptionValues(kind),
  };
}

function getPackagePatchAccessKey(kind) {
  if (kind !== "package_patch") {
    return kind;
  }
  return getEffectiveJobOptionValues(kind).dry_run === false ? "package_patch_live" : "package_patch_dry_run";
}

function hostHasCapability(host, capability) {
  const capabilities = Array.isArray(host?.agent?.capabilities)
    ? host.agent.capabilities.map((value) => String(value))
    : [];
  return capabilities.includes(capability);
}

function getJobHostAccess(host, kind) {
  const access = getJobSpecialAccess(kind);
  if (!access) {
    return { eligible: true, detail: "" };
  }
  const accessKey = getPackagePatchAccessKey(kind);
  const advertisedAccess = host?.job_access?.[accessKey];
  if (advertisedAccess && typeof advertisedAccess.eligible === "boolean") {
    return {
      eligible: advertisedAccess.eligible,
      detail: advertisedAccess.reason || hostMaintenanceInfo(host.agent).detail || access.summary || "",
    };
  }
  if (hostHasCapability(host, access.required_capability)) {
    return {
      eligible: true,
      detail: hostMaintenanceInfo(host.agent).detail || access.summary || "",
    };
  }
  return {
    eligible: false,
    detail: host.agent ? hostMaintenanceInfo(host.agent).detail : access.missing_detail || `${access.label} required.`,
  };
}

function getNamedHostAccess(host, accessKey, fallbackKind) {
  const advertisedAccess = host?.job_access?.[accessKey];
  if (advertisedAccess && typeof advertisedAccess.eligible === "boolean") {
    return {
      eligible: advertisedAccess.eligible,
      detail: advertisedAccess.reason || hostMaintenanceInfo(host.agent).detail || "",
    };
  }
  return getJobHostAccess(host, fallbackKind);
}

function buttonStateAttrs(disabled, title = "") {
  const attrs = [];
  if (disabled) {
    attrs.push("disabled");
  }
  if (title) {
    attrs.push(`title="${escapeHtml(title)}"`);
  }
  return attrs.length ? ` ${attrs.join(" ")}` : "";
}

function buildJobResultMarkup(job) {
  const result = job?.result;
  if (!result || typeof result !== "object") {
    return "";
  }

  if (job.kind === "docker_check" && result.report) {
    const report = result.report;
    const header = `${report.name || job.target_ref}: ${inspectionStateLabel(report.status || "unknown")}`;
    const detail = `${report.outdated_count || 0}/${report.image_count || 0} tracked image(s) outdated.`;
    return [header, detail].map((line) => escapeHtml(line)).join("<br>");
  }

  if (job.kind === "docker_update" && result.update_summary && Array.isArray(result.update_summary.stacks)) {
    const stacks = result.update_summary.stacks;
    const changedStacks = stacks.filter((stack) => (stack.changed_services || 0) > 0);
    const header = changedStacks.length
      ? `Updated ${result.update_summary.changed_services || 0} service${(result.update_summary.changed_services || 0) === 1 ? "" : "s"} across ${changedStacks.length} stack${changedStacks.length === 1 ? "" : "s"}.`
      : "No service image changes were detected in this docker update run.";
    const lines = [header];

    stacks.forEach((stack) => {
      const services = Array.isArray(stack.services) ? stack.services : [];
      if (!services.length) {
        return;
      }
      services.forEach((service) => {
        lines.push(
          `${stack.stack}/${service.service}: ${formatDockerVersion(service.from_ref, service.from_short)} -> ${formatDockerVersion(service.to_ref, service.to_short)}`
        );
      });
    });
    return lines.map((line) => escapeHtml(line)).join("<br>");
  }

  if (result.update_summary_error) {
    return escapeHtml(`Update summary unavailable: ${result.update_summary_error}`);
  }

  return "";
}

function getJobKindConfig(kind) {
  return getJobKindItems().find((item) => item.kind === kind) || FALLBACK_JOB_KIND;
}

function getJobStacksForKind(kind) {
  const config = getJobKindConfig(kind);
  const stacks = [...(entitiesState.stacks?.items || [])].sort((left, right) => left.name.localeCompare(right.name));
  if (!["stack_multi", "stack_single"].includes(config.mode)) {
    return [];
  }
  return stacks;
}

function getJobHostsForKind(kind) {
  const config = getJobKindConfig(kind);
  const hosts = [...(entitiesState.hosts?.items || [])].sort((left, right) => left.name.localeCompare(right.name));
  if (config.mode !== "host_multi") {
    return [];
  }
  return hosts.filter((host) => {
    const group = host.group || "";
    const includeGroups = config.host_groups_include || [];
    const excludeGroups = config.host_groups_exclude || [];
    if (includeGroups.length && !includeGroups.includes(group)) {
      return false;
    }
    if (excludeGroups.includes(group)) {
      return false;
    }
    return true;
  });
}

function getSelectedJobStacks(kind = jobFormState.kind || jobKindSelect.value) {
  const allowed = getJobStacksForKind(kind).map((stack) => stack.name);
  return normalizeSelection(jobFormState.selectedStacks, allowed);
}

function setSelectedJobStacks(names, kind = jobFormState.kind || jobKindSelect.value) {
  const allowed = getJobStacksForKind(kind).map((stack) => stack.name);
  jobFormState.selectedStacks = normalizeSelection(names, allowed);
}

function getSelectedJobHosts(kind = jobFormState.kind || jobKindSelect.value) {
  const allowed = getJobHostsForKind(kind)
    .filter((host) => getJobHostAccess(host, kind).eligible)
    .map((host) => host.name);
  return normalizeSelection(jobFormState.selectedHosts, allowed);
}

function setSelectedJobHosts(names, kind = jobFormState.kind || jobKindSelect.value) {
  const allowed = getJobHostsForKind(kind)
    .filter((host) => getJobHostAccess(host, kind).eligible)
    .map((host) => host.name);
  jobFormState.selectedHosts = normalizeSelection(names, allowed);
}

function getJobOptionDefinitions(kind) {
  return getJobKindConfig(kind).fields || [];
}

function getJobOptionInputId(name) {
  return `job-option-${name}`;
}

function getStoredJobOptionValues(kind) {
  return jobFormState.optionValues[kind] || {};
}

function getRenderedJobOptionValues(kind) {
  const values = {};
  getJobOptionDefinitions(kind).forEach((field) => {
    const input = document.getElementById(getJobOptionInputId(field.name));
    if (!input) {
      return;
    }
    if (field.type === "toggle") {
      values[field.name] = input.checked;
      return;
    }
    const value = String(input.value || "").trim();
    if (field.optional && !value) {
      return;
    }
    values[field.name] = value;
  });
  return values;
}

function storeRenderedJobOptionValues(kind) {
  jobFormState.optionValues[kind] = getRenderedJobOptionValues(kind);
}

function renderJobOptions(kind, resetOptions = false) {
  const config = getJobKindConfig(kind);
  const fields = getJobOptionDefinitions(kind);
  if (resetOptions) {
    delete jobFormState.optionValues[kind];
  }
  const currentValues = { ...config.defaults, ...getStoredJobOptionValues(kind) };

  if (!fields.length) {
    jobOptions.innerHTML = `<p class="hint">No extra options for this job. Use the target picker above and queue it when ready.</p>`;
    return;
  }

  jobOptions.innerHTML = fields
    .map((field) => {
      const inputId = getJobOptionInputId(field.name);
      const label = escapeHtml(field.label);
      const hint = field.hint ? `<p class="job-option-hint">${escapeHtml(field.hint)}</p>` : "";
      const value = currentValues[field.name];

      if (field.type === "toggle") {
        return `
          <label class="job-option-row job-option-toggle" for="${inputId}">
            <span class="job-option-copy">
              <span class="job-option-label">${label}</span>
              ${hint}
            </span>
            <input id="${inputId}" name="${field.name}" type="checkbox"${value ? " checked" : ""} />
          </label>
        `;
      }

      if (field.type === "select") {
        const options = (field.options || [])
          .map((option) => {
            const selected = option.value === value ? " selected" : "";
            return `<option value="${escapeHtml(option.value)}"${selected}>${escapeHtml(option.label)}</option>`;
          })
          .join("");
        return `
          <div class="job-option-row">
            <label class="field-label" for="${inputId}">${label}</label>
            <select id="${inputId}" name="${field.name}">${options}</select>
            ${hint}
          </div>
        `;
      }

      return `
        <div class="job-option-row">
          <label class="field-label" for="${inputId}">${label}</label>
          <input
            id="${inputId}"
            name="${field.name}"
            type="text"
            value="${escapeHtml(value || "")}"
            placeholder="${escapeHtml(field.placeholder || "")}"
          />
          ${hint}
        </div>
      `;
    })
    .join("");
}

function setJobStackMenu(open) {
  const nextState = typeof open === "boolean" ? open : jobStackMenu.classList.contains("hidden");
  jobStackMenu.classList.toggle("hidden", !nextState);
  jobStackPicker.classList.toggle("open", nextState);
}

function setJobHostMenu(open) {
  const nextState = typeof open === "boolean" ? open : jobHostMenu.classList.contains("hidden");
  jobHostMenu.classList.toggle("hidden", !nextState);
  jobHostPicker.classList.toggle("open", nextState);
}

function updateJobStackSelectionState(kind) {
  const stacks = getJobStacksForKind(kind);
  const selected = getSelectedJobStacks(kind);
  const selectedPreview = selected.slice(0, 3).join(", ");

  jobStackToggle.disabled = stacks.length === 0;
  if (!stacks.length) {
    jobStackToggle.textContent = "No stacks available";
    jobStackStatus.textContent = "No stacks match this job type.";
    setJobStackMenu(false);
    return;
  }

  if (selected.length === 0) {
    jobStackToggle.textContent = "Choose stack(s)";
    jobStackStatus.textContent = `${stacks.length} stack${stacks.length === 1 ? "" : "s"} available.`;
    return;
  }

  if (selected.length === stacks.length) {
    jobStackToggle.textContent = `All ${stacks.length} stacks selected`;
    jobStackStatus.textContent = selectedPreview;
    return;
  }

  jobStackToggle.textContent = `${selected.length} stack${selected.length === 1 ? "" : "s"} selected`;
  jobStackStatus.textContent =
    selected.length > 3 ? `${selectedPreview} +${selected.length - 3} more` : selectedPreview;
}

function renderJobStackOptions(kind, preserveSelection = true) {
  const config = getJobKindConfig(kind);
  const stacks = getJobStacksForKind(kind);
  const currentSelection = getSelectedJobStacks(kind);
  const shouldSelectAll = !preserveSelection && config.default_select_all;
  const previousSelection = shouldSelectAll ? new Set(stacks.map((stack) => stack.name)) : new Set(currentSelection);
  setSelectedJobStacks([...previousSelection], kind);

  if (!stacks.length) {
    jobStackOptions.innerHTML = emptyState("No matching stacks.");
    updateJobStackSelectionState(kind);
    return;
  }

  jobStackOptions.innerHTML = stacks
    .map((stack) => {
      const checked = previousSelection.has(stack.name) ? " checked" : "";
      const stackName = escapeHtml(stack.name);
      const host = escapeHtml(stack.host || "unknown");
      const mode = escapeHtml(stack.update_mode || "manual");
      return `
        <label class="job-target-option">
          <input type="checkbox" value="${stackName}"${checked} />
          <span>
            <strong>${stackName}</strong>
            <span class="subline">${host} · ${mode}</span>
          </span>
        </label>
      `;
    })
    .join("");

  updateJobStackSelectionState(kind);
}

function updateJobHostSelectionState(kind) {
  const hosts = getJobHostsForKind(kind);
  const eligibleHosts = hosts.filter((host) => getJobHostAccess(host, kind).eligible);
  const blockedCount = Math.max(hosts.length - eligibleHosts.length, 0);
  const access = getJobSpecialAccess(kind);
  const selected = getSelectedJobHosts(kind);
  const selectedPreview = selected.slice(0, 3).join(", ");

  jobHostToggle.disabled = hosts.length === 0;
  if (!hosts.length) {
    jobHostToggle.textContent = "No compatible hosts";
    jobHostStatus.textContent = "No hosts match this job type.";
    setJobHostMenu(false);
    return;
  }

  if (!eligibleHosts.length) {
    jobHostToggle.textContent = "No eligible hosts";
    jobHostStatus.textContent = access
      ? `${hosts.length} host${hosts.length === 1 ? "" : "s"} listed, but none currently satisfy the helper and policy requirements for this job.`
      : `${hosts.length} host${hosts.length === 1 ? "" : "s"} listed, but none are available right now.`;
    return;
  }

  if (selected.length === 0) {
    jobHostToggle.textContent = "Choose host(s)";
    jobHostStatus.textContent = `${eligibleHosts.length} host${eligibleHosts.length === 1 ? "" : "s"} available.${blockedCount ? ` ${blockedCount} greyed out until helper and policy requirements are satisfied.` : ""}`;
    return;
  }

  if (selected.length === eligibleHosts.length) {
    jobHostToggle.textContent = `All ${eligibleHosts.length} hosts selected`;
    jobHostStatus.textContent = selectedPreview;
    return;
  }

  jobHostToggle.textContent = `${selected.length} host${selected.length === 1 ? "" : "s"} selected`;
  jobHostStatus.textContent =
    selected.length > 3 ? `${selectedPreview} +${selected.length - 3} more` : selectedPreview;
}

function renderJobHostOptions(kind, preserveSelection = true) {
  const hosts = getJobHostsForKind(kind);
  const previousSelection = preserveSelection ? new Set(getSelectedJobHosts(kind)) : new Set();
  setSelectedJobHosts([...previousSelection], kind);

  if (!hosts.length) {
    jobHostOptions.innerHTML = emptyState("No matching hosts.");
    updateJobHostSelectionState(kind);
    return;
  }

  jobHostOptions.innerHTML = hosts
    .map((host) => {
      const access = getJobHostAccess(host, kind);
      const checked = access.eligible && previousSelection.has(host.name) ? " checked" : "";
      const disabled = access.eligible ? "" : " disabled";
      const hostName = escapeHtml(host.name);
      const group = escapeHtml(host.group || "all");
      const address = escapeHtml(host.ansible_host || "n/a");
      const accessDetail = access.detail ? `<span class="subline">${escapeHtml(access.detail)}</span>` : "";
      return `
        <label class="job-target-option${access.eligible ? "" : " disabled"}">
          <input type="checkbox" value="${hostName}"${checked}${disabled} />
          <span>
            <strong>${hostName}</strong>
            <span class="subline">${group} · ${address}</span>
            ${accessDetail}
          </span>
        </label>
      `;
    })
    .join("");

  updateJobHostSelectionState(kind);
}

function syncJobForm(kind = jobKindSelect.value, { resetOptions = false, preserveSelection = true } = {}) {
  const config = getJobKindConfig(kind);
  const access = getJobSpecialAccess(kind);
  jobFormState.kind = kind;
  jobTargetSummary.textContent = [config.summary, access?.summary].filter(Boolean).join(" ");

  const showStackPicker = ["stack_multi", "stack_single"].includes(config.mode);
  const showHostPicker = config.mode === "host_multi";
  const showManualTarget = config.mode === "manual";
  jobStackPicker.classList.toggle("hidden", !showStackPicker);
  jobHostPicker.classList.toggle("hidden", !showHostPicker);
  jobManualTargetWrap.classList.toggle("hidden", !showManualTarget);

  if (showStackPicker) {
    renderJobStackOptions(kind, preserveSelection);
  } else {
    setJobStackMenu(false);
  }

  if (showHostPicker) {
    renderJobHostOptions(kind, preserveSelection);
  } else {
    setJobHostMenu(false);
  }

  if (showManualTarget) {
    jobManualTargetLabel.textContent = config.manual_label;
    jobManualTargetInput.placeholder = config.manual_placeholder;
    jobManualTargetInput.value = jobFormState.manualTarget;
  } else {
    jobManualTargetInput.value = "";
    jobFormState.manualTarget = "";
  }

  renderJobOptions(kind, resetOptions);
}

function buildJobRequest() {
  const kind = jobKindSelect.value;
  const config = getJobKindConfig(kind);
  const payload = { ...config.defaults, ...getRenderedJobOptionValues(kind) };
  let targetRef = "";

  if (config.mode === "stack_multi") {
    const selectedStacks = getSelectedJobStacks(kind);
    const availableStacks = getJobStacksForKind(kind);
    if (!selectedStacks.length) {
      throw new Error("Select at least one stack.");
    }
    const selectedAll = selectedStacks.length === availableStacks.length;
    targetRef = selectedAll ? "all" : selectedStacks.join(",");
    if (kind === "docker_update") {
      if (selectedAll) {
        payload.selected_stacks = availableStacks.map((stack) => stack.name);
        payload.window = "all";
      } else {
        payload.selected_stacks = selectedStacks;
      }
    }
  } else if (config.mode === "stack_single") {
    const selectedStacks = getSelectedJobStacks(kind);
    if (selectedStacks.length !== 1) {
      throw new Error("Select exactly one stack.");
    }
    targetRef = selectedStacks[0];
  } else if (config.mode === "host_multi") {
    const selectedHosts = getSelectedJobHosts(kind);
    const availableHosts = getJobHostsForKind(kind);
    if (!selectedHosts.length) {
      throw new Error("Select at least one host.");
    }
    const blockedHosts = selectedHosts.map((hostName) => {
      const host = availableHosts.find((item) => item.name === hostName);
      const hostAccess = host ? getJobHostAccess(host, kind) : { eligible: true, detail: "" };
      return { hostName, ...hostAccess };
    }).filter((item) => !item.eligible);
    if (blockedHosts.length) {
      throw new Error(
        blockedHosts
          .slice(0, 4)
          .map((item) => `${item.hostName}: ${item.detail || "not eligible"}`)
          .join("; ")
      );
    }
    targetRef = selectedHosts.join(",");
    if (kind === "package_check") {
      payload.hosts = selectedHosts;
    } else {
      payload.limit = targetRef;
    }
  } else {
    targetRef = jobFormState.manualTarget.trim();
    if (!targetRef) {
      throw new Error(`Enter a ${String(config.manual_label || "target").toLowerCase()}.`);
    }
    if (kind === "backup" && !payload.volume) {
      payload.volume = targetRef;
    }
  }

  return { kind, targetType: config.target_type, targetRef, payload };
}

function syncJobLogPanel() {
  jobLogPanel.classList.toggle("expanded", uiState.jobLogExpanded);
  jobLogExpandButton.textContent = uiState.jobLogExpanded ? "Collapse" : "Expand";
}

function clearSelectedJobDetails(message = "No job selected.") {
  uiState.selectedJob = null;
  jobResult.innerHTML = "";
  jobEvents.textContent = message;
}

function canCancelJob(job) {
  return ["queued", "pending_approval"].includes(job.status);
}

function renderInstallPreviews() {
  if (!entitiesState.settings) {
    return;
  }
  const blocks = entitiesState.settings.agent_install || {};
  const helperBlocks = entitiesState.settings.agent_host_maintenance || {};
  const selected = blocks[uiState.installPreviewMode] || blocks.compose || "";
  const helper = helperBlocks[uiState.installPreviewMode] || helperBlocks.compose || "";
  const preview = [
    "# Base agent install",
    selected,
    "",
    "# Optional: enable limited host maintenance (requires root)",
    helper,
  ]
    .filter(Boolean)
    .join("\n");
  document.getElementById("overview-install").textContent = preview;
  document.getElementById("settings-install").textContent = preview;
  document.querySelectorAll("[data-install-mode]").forEach((node) => {
    node.classList.toggle("active", node.dataset.installMode === uiState.installPreviewMode);
  });
}

function renderOverview() {
  const overview = entitiesState.overview;
  const jobs = entitiesState.jobs.items;
  const approvals = jobs.filter((item) => item.approval_status === "pending");
  const release = entitiesState.settings.release || {};

  const stats = [
    ["Agents", overview.counts.agents],
    ["Hosts", overview.hosts],
    ["Stacks", overview.stacks],
    ["Jobs", overview.counts.jobs],
    ["Running", overview.counts.running_jobs],
    ["Approvals", overview.counts.pending_approvals],
  ];
  document.getElementById("overview-stats").innerHTML = stats
    .map(
      ([label, value]) => `
        <article class="stat-card">
          <div class="stat-label">${escapeHtml(label)}</div>
          <div class="stat-value">${escapeHtml(value)}</div>
        </article>
      `
    )
    .join("");

  renderTable(
    "overview-jobs",
    ["Job", "Target", "State", "Action"],
    jobs.slice(0, 6).map(
      (item) => `
        <tr>
          <td>
            <strong>${escapeHtml(item.kind)}</strong>
            <span class="subline mono">${shortId(item.id)}</span>
          </td>
          <td>${escapeHtml(item.target_ref)}</td>
          <td>
            <div class="badge-row">
              ${statusBadge(item.status)}
              ${statusBadge(item.approval_status)}
            </div>
          </td>
          <td>
            <div class="table-actions">
              <button class="secondary" data-job-log="${escapeHtml(item.id)}">Logs</button>
            </div>
          </td>
        </tr>
      `
    ),
    "No jobs yet."
  );

  renderTable(
    "overview-approvals",
    ["Job", "Target", "Requested By", "Action"],
    approvals.slice(0, 6).map(
      (item) => `
        <tr>
          <td>
            <strong>${escapeHtml(item.kind)}</strong>
            <span class="subline mono">${shortId(item.id)}</span>
          </td>
          <td>${escapeHtml(item.target_ref)}</td>
          <td>${escapeHtml(item.requested_by)}</td>
          <td>
            <div class="table-actions">
              <button data-job-approve="${escapeHtml(item.id)}">Approve</button>
            </div>
          </td>
        </tr>
      `
    ),
    "No pending approvals."
  );
  if (overviewRelease) {
    const latest = release.latest || {};
    const stack = release.stack || {};
    const agentSummary = (release.agents || {}).summary || {};
    overviewRelease.textContent = [
      `Stack version: v${stack.current_version || "unknown"} (${releaseLabel(stack.release_state || "unknown")})`,
      `Latest upstream: ${latest.version || "unavailable"}${latest.source ? ` via GitHub ${latest.source}` : ""}`,
      `Agents current/outdated/unknown: ${agentSummary.current || 0}/${agentSummary.outdated || 0}/${agentSummary.unknown || 0}`,
    ].join("\n");
  }
  renderInstallPreviews();
}

function renderStacks() {
  const payload = entitiesState.dockerUpdates || { summary: {}, items: [] };
  const items = Array.isArray(payload.items) ? payload.items : [];
  const summary = payload.summary || {};
  const selected = selectedStackNames();

  if (stacksSummary) {
    const selectable = Number(summary.selectable_stacks || 0);
    const canUpdateSelected = selected.length > 0;
    const lastFullCheck = summary.last_full_check_at
      ? `Last full eligible check: ${formatTimestamp(summary.last_full_check_at)}`
      : "Last full eligible check: not completed yet";
    stacksSummary.innerHTML = `
      <div class="stack-toolbar">
        <div class="stack-summary-grid">
          <div class="stack-summary-card">
            <span class="stat-label">Coverage</span>
            <strong>${pluralize(Number(summary.checked_stacks || 0), "stack")} checked of ${summary.total_stacks || 0}</strong>
            <span class="subline">${pluralize(Number(summary.running_checks || 0), "check")} still running</span>
            <span class="subline">${escapeHtml(lastFullCheck)}</span>
          </div>
          <div class="stack-summary-card">
            <span class="stat-label">Updates</span>
            <strong>${pluralize(Number(summary.outdated_stacks || 0), "stack")} with updates</strong>
            <span class="subline">${pluralize(Number(summary.outdated_images || 0), "image")} outdated across the fleet</span>
          </div>
          <div class="stack-summary-card">
            <span class="stat-label">Selection</span>
            <strong>${pluralize(selected.length, "stack")} selected</strong>
            <span class="subline">${selectable} stack(s) are ready for bulk live updates</span>
          </div>
        </div>
        <div class="stack-selection-controls">
          <div class="table-actions">
            <button type="button" data-stack-bulk-check="all">Check All</button>
            <button type="button" class="secondary" data-stack-select-mode="outdated">Select Outdated</button>
            <button type="button" class="secondary" data-stack-select-mode="none">Clear</button>
            <button type="button" data-stack-update-selected${buttonStateAttrs(!canUpdateSelected, "Select at least one eligible outdated stack first.")}>Update Selected</button>
          </div>
          <label class="checkbox-row">
            <input type="checkbox" data-stack-update-approval ${uiState.stackUpdateRequiresApproval ? "checked" : ""} />
            Require approval before live updates
          </label>
        </div>
        ${payload.error ? `<div class="error">Docker update inventory is temporarily unavailable: ${escapeHtml(payload.error)}</div>` : ""}
      </div>
    `;
  }

  const root = document.getElementById("stacks-table");
  if (!items.length) {
    root.innerHTML = emptyState("No stacks configured.");
    return;
  }

  root.innerHTML = `
    <table class="data-table stack-update-table">
      <thead>
        <tr>
          <th>Select</th>
          <th>Stack</th>
          <th>Host</th>
          <th>Inspection</th>
          <th>Latest Update</th>
          <th>Policy</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        ${items
          .map((item) => {
            const stackName = escapeHtml(item.name);
            const inspection = item.inspection || {};
            const report = inspection.report || {};
            const latestUpdate = item.latest_update || {};
            const liveAccess = item.job_access?.docker_update_live || {};
            const checkAccess = item.job_access?.docker_check || {};
            const dryAccess = item.job_access?.docker_update_dry_run || {};
            const dockerUpdateSettings = entitiesState.settings?.docker_updates || {};
            const resolvedHost = item.resolved_host || (item.host === "localhost" && item.guest_host ? item.guest_host : item.host);
            const host = escapeHtml(resolvedHost || "unknown");
            const projectDir = escapeHtml(item.project_dir || item.path || "not set");
            const envCount = (item.compose_env_files || []).length;
            const backupCommandCount = (item.backup_commands || []).length;
            const sourceLabel =
              item.catalog_source === "discovered"
                ? `Discovered from agent${item.agent_status ? ` · ${item.agent_status}` : ""}`
                : "From site overlay";
            const inspectionState = inspectionStateLabel(inspection.state || "unknown");
            const updateStatus = latestUpdate.status ? String(latestUpdate.status).replaceAll("_", " ") : "never run";
            const changedServices = Number((latestUpdate.summary || {}).changed_services || 0);
            const policyBits = [
              item.backup_before ? "backup_before" : "",
              item.backup_before && backupCommandCount
                ? dockerUpdateSettings.run_backup_commands
                  ? `backup_commands x${backupCommandCount}`
                  : "backup_commands disabled"
                : "",
            ].filter(Boolean);
            const accessPrimary = liveAccess.eligible
              ? "Live updates available through the stack agent."
              : liveAccess.reason || checkAccess.reason || "Inspection unavailable.";
            const accessSecondary =
              !dryAccess.eligible && dryAccess.reason && dryAccess.reason !== accessPrimary ? dryAccess.reason : "";
            const detailsExpanded = Boolean(uiState.expandedStacks[item.name]);
            const detailReport = report && Object.keys(report).length ? renderInspectionServices(report) : emptyState("Run a check to load image-by-image details.");
            const detailSummary = changedServices
              ? `<span class="subline">${pluralize(changedServices, "service")} changed in the latest successful update.</span>`
              : latestUpdate.error
                ? `<span class="subline error">${escapeHtml(latestUpdate.error)}</span>`
                : `<span class="subline">${escapeHtml(latestUpdate.finished_at ? `Last update finished ${formatTimestamp(latestUpdate.finished_at)}.` : "No completed live update recorded yet.")}</span>`;
            return `
              <tr>
                <td>
                  <input
                    type="checkbox"
                    data-stack-select="${stackName}"
                    ${item.selection_eligible ? "" : "disabled"}
                    ${isStackSelected(item.name) ? "checked" : ""}
                  />
                </td>
                <td>
                  <strong>${stackName}</strong>
                  <span class="subline">${envCount ? `${envCount} env file(s)` : "No extra env files"}</span>
                  <span class="subline">${escapeHtml(sourceLabel)}</span>
                  <span class="subline">${badge(item.update_mode || "manual", "accent")} ${badge(item.risk || "unknown")}</span>
                </td>
                <td>
                  <strong>${host}</strong>
                  <span class="path-pill mono" title="${projectDir}">${projectDir}</span>
                </td>
                <td>
                  ${statusBadge(inspectionState)}
                  <span class="subline">
                    ${report.outdated_count ? `${report.outdated_count}/${report.image_count || 0} outdated` : report.image_count ? `${report.image_count} tracked image(s)` : "No inspection data"}
                  </span>
                  <span class="subline">${escapeHtml(inspection.checked_at ? formatTimestamp(inspection.checked_at) : "Not checked yet")}</span>
                  ${inspection.error ? `<span class="subline error">${escapeHtml(inspection.error)}</span>` : ""}
                </td>
                <td>
                  ${statusBadge(updateStatus === "never run" ? "never run" : updateStatus)}
                  <span class="subline">
                    ${changedServices ? `${pluralize(changedServices, "service")} changed` : latestUpdate.finished_at ? `Finished ${formatTimestamp(latestUpdate.finished_at)}` : "No update run yet"}
                  </span>
                  ${latestUpdate.error ? `<span class="subline error">${escapeHtml(latestUpdate.error)}</span>` : ""}
                </td>
                <td>
                  ${policyBits.length ? policyBits.map((value) => badge(value, "warn")).join(" ") : badge("agent-ready", "good")}
                  <span class="subline">${escapeHtml(accessPrimary)}</span>
                  ${accessSecondary ? `<span class="subline">${escapeHtml(accessSecondary)}</span>` : ""}
                </td>
                <td>
                  <div class="table-actions">
                    <button class="secondary" data-stack-action="check" data-stack-name="${stackName}"${buttonStateAttrs(
                      !checkAccess.eligible,
                      checkAccess.reason || ""
                    )}>Check</button>
                    <button data-stack-action="update" data-stack-name="${stackName}"${buttonStateAttrs(
                      !liveAccess.eligible,
                      liveAccess.reason || ""
                    )}>Live</button>
                    <button class="secondary" data-stack-action="rollback" data-stack-name="${stackName}">Rollback</button>
                    <button class="secondary" data-stack-action="details" data-stack-name="${stackName}">${detailsExpanded ? "Hide" : "Details"}</button>
                  </div>
                </td>
              </tr>
              ${
                detailsExpanded
                  ? `
                    <tr class="stack-detail-shell">
                      <td colspan="7">
                        <div class="stack-detail-grid">
                          <div class="stack-detail-card">
                            <strong>Inspection Details</strong>
                            <span class="subline">${escapeHtml(inspection.checked_at ? `Checked ${formatTimestamp(inspection.checked_at)}` : "No completed inspection yet.")}</span>
                            ${detailReport}
                          </div>
                          <div class="stack-detail-card">
                            <strong>Update Readiness</strong>
                            <span class="subline">${escapeHtml(liveAccess.eligible ? "Bulk and single-stack live updates are available." : liveAccess.reason || "Live updates are blocked for this stack.")}</span>
                            <span class="subline">${escapeHtml(checkAccess.eligible ? "Inspection checks can run on this stack." : checkAccess.reason || "Inspection checks are blocked.")}</span>
                            ${detailSummary}
                          </div>
                        </div>
                      </td>
                    </tr>
                  `
                  : ""
              }
            `;
          })
          .join("")}
      </tbody>
    </table>
  `;
}

function renderHosts() {
  const items = entitiesState.hosts.items;
  renderTable(
    "hosts-table",
    ["Host", "Group", "Address", "Agent", "Actions"],
    items.map((item) => {
      const hostName = escapeHtml(item.name);
      const group = escapeHtml(item.group || "all");
      const address = escapeHtml(item.ansible_host || "n/a");
      const agent = item.agent;
      const runtime = item.runtime || {};
      const maintenance = hostMaintenanceInfo(agent);
      const agentCell = agent
        ? `${statusBadge(agent.status)}<span class="subline mono">${escapeHtml(agent.display_name || agent.name)}</span><span class="subline">${escapeHtml(maintenance.detail)}</span>`
        : `${statusBadge(runtime.status || "No agent")}<span class="subline">${escapeHtml(runtime.detail || "Agent enrollment is required for Docker updates and helper-backed host jobs.")}</span>`;
      const isProxmoxNode = item.group === "proxmox_nodes";
      const packageCheckAccess = getNamedHostAccess(item, "package_check", "package_check");
      const packagePatchDryAccess = getNamedHostAccess(item, "package_patch_dry_run", "package_patch");
      const packagePatchLiveAccess = getNamedHostAccess(item, "package_patch_live", "package_patch");
      const proxmoxPatchDryAccess = getNamedHostAccess(item, "proxmox_patch_dry_run", "proxmox_patch");
      const proxmoxPatchLiveAccess = getNamedHostAccess(item, "proxmox_patch_live", "proxmox_patch");
      const proxmoxRebootDryAccess = getNamedHostAccess(item, "proxmox_reboot_dry_run", "proxmox_reboot");
      const proxmoxRebootLiveAccess = getNamedHostAccess(item, "proxmox_reboot_live", "proxmox_reboot");
      const packageActionNote = packagePatchLiveAccess.eligible
        ? "Package actions limited to approved host-maintenance helper access."
        : packagePatchDryAccess.eligible
          ? "Dry-run helper patching is enabled. Live helper patching is blocked by host policy."
          : packageCheckAccess.eligible
            ? "Package checks are enabled. Package patch access is not enabled on this host."
            : "Package jobs require the limited host-maintenance helper on this host.";
      const proxmoxActionNote = proxmoxRebootLiveAccess.eligible
        ? "Proxmox patch and reboot are enabled through approved helper actions. Multi-node live runs stay approval-gated."
        : proxmoxPatchLiveAccess.eligible
          ? "Proxmox patch is enabled. Reboot helper access is not enabled on this node."
          : proxmoxPatchDryAccess.eligible || proxmoxRebootDryAccess.eligible
            ? "Dry-run Proxmox helper access is enabled. Multi-node live actions stay approval-gated."
            : "Proxmox jobs require the limited Proxmox helper actions on this node.";
      const actionButtons = isProxmoxNode
        ? `
            <button class="secondary" data-host-kind="proxmox_patch" data-host-name="${hostName}" data-dry-run="true"${buttonStateAttrs(
              !proxmoxPatchDryAccess.eligible,
              proxmoxPatchDryAccess.detail
            )}>Patch Dry</button>
            <button data-host-kind="proxmox_patch" data-host-name="${hostName}" data-dry-run="false"${buttonStateAttrs(
              !proxmoxPatchLiveAccess.eligible,
              proxmoxPatchLiveAccess.detail
            )}>Patch Live</button>
            <button class="secondary" data-host-kind="proxmox_reboot" data-host-name="${hostName}" data-dry-run="true" data-reboot-mode="soft"${buttonStateAttrs(
              !proxmoxRebootDryAccess.eligible,
              proxmoxRebootDryAccess.detail
            )}>Reboot Dry</button>
            <button data-host-kind="proxmox_reboot" data-host-name="${hostName}" data-dry-run="false" data-reboot-mode="soft"${buttonStateAttrs(
              !proxmoxRebootLiveAccess.eligible,
              proxmoxRebootLiveAccess.detail
            )}>Reboot Live</button>
            <span class="subline">${escapeHtml(proxmoxActionNote)}</span>
          `
        : `
            <button class="secondary" data-host-kind="package_check" data-host-name="${hostName}" data-dry-run="true"${buttonStateAttrs(
              !packageCheckAccess.eligible,
              packageCheckAccess.detail
            )}>Check</button>
            <button class="secondary" data-host-kind="package_patch" data-host-name="${hostName}" data-dry-run="true"${buttonStateAttrs(
              !packagePatchDryAccess.eligible,
              packagePatchDryAccess.detail
            )}>Patch Dry</button>
            <button data-host-kind="package_patch" data-host-name="${hostName}" data-dry-run="false"${buttonStateAttrs(
              !packagePatchLiveAccess.eligible,
              packagePatchLiveAccess.detail
            )}>Patch Live</button>
            <span class="subline">${escapeHtml(packageActionNote)}</span>
          `;
      return `
        <tr>
          <td><strong>${hostName}</strong></td>
          <td>${group}</td>
          <td><span class="mono">${address}</span></td>
          <td>${agentCell}</td>
          <td>
            <div class="table-actions">
              ${actionButtons}
              <button class="secondary" data-host-edit="${hostName}">Edit</button>
              <button class="danger" data-host-delete="${hostName}">Delete</button>
            </div>
          </td>
        </tr>
      `;
    }),
    "No hosts configured."
  );
  syncHostEditorOptions();
}

function renderAgents() {
  const items = entitiesState.agents.items;
  renderTable(
    "agents-table",
    ["Agent", "Transport", "Platform", "Version", "Capabilities", "Last Seen"],
    items.map((item) => {
      const maintenance = hostMaintenanceInfo(item);
      const capabilityList = normalizeDisplayedCapabilities(item.capabilities);
      return `
        <tr>
          <td>
            <strong>${escapeHtml(item.display_name)}</strong>
            <span class="subline mono">${escapeHtml(item.name)}</span>
          </td>
          <td>
            ${statusBadge(item.status)}
            <span class="subline">${escapeHtml(item.transport)}</span>
          </td>
          <td>${escapeHtml(item.platform || "unknown")}</td>
          <td>
            ${statusBadge(releaseLabel(item.release_state || "unknown"))}
            <span class="subline mono">${escapeHtml(item.version || "unknown")}</span>
            <span class="subline">${escapeHtml(item.update_mode || "unknown")}</span>
          </td>
          <td>
            ${escapeHtml(capabilityList.join(", ") || "none")}
            <span class="subline">${escapeHtml(maintenance.detail)}</span>
          </td>
          <td>${escapeHtml(formatTimestamp(item.last_seen_at))}</td>
        </tr>
      `;
    }),
    "No agents registered."
  );
}

function renderJobs() {
  const root = document.getElementById("jobs-table");
  const items = getJobItems();
  if (!items.length) {
    root.innerHTML = emptyState("No jobs yet.");
    return;
  }

  const selectedIds = selectedJobIds();
  const selectedCount = selectedIds.length;
  const deletableCount = items.filter((item) => canDeleteJob(item)).length;
  root.innerHTML = `
    <div class="stack-toolbar">
      <div class="stack-selection-controls">
        <div>
          <strong>${pluralize(selectedCount, "job")} selected</strong>
          <span class="subline">Delete completed, failed, or cancelled jobs in bulk. Deleting a job also removes its saved event log.</span>
        </div>
        <div class="table-actions">
          <button type="button" class="secondary" data-job-select-mode="deletable" ${deletableCount ? "" : "disabled"}>Select deletable</button>
          <button type="button" class="secondary" data-job-select-mode="older-1d" ${deletableCount ? "" : "disabled"}>Older than 1 day</button>
          <button type="button" class="secondary" data-job-select-mode="older-7d" ${deletableCount ? "" : "disabled"}>Older than 7 days</button>
          <button type="button" class="secondary" data-job-select-mode="none">Clear</button>
          <button type="button" class="danger" data-job-delete-selected ${selectedCount ? "" : "disabled"}>
            ${selectedCount ? `Delete selected (${selectedCount})` : "Delete selected"}
          </button>
        </div>
      </div>
      <div class="table-shell">
        <table class="data-table job-table">
          <thead>
            <tr>
              <th>Select</th>
              <th>Job</th>
              <th>Target</th>
              <th>Execution</th>
              <th>Status</th>
              <th>Created</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            ${items
              .map((item) => {
                const config = getJobKindConfig(item.kind);
                const access = config.special_access || null;
                const deletable = canDeleteJob(item);
                return `
                  <tr>
                    <td>
                      <input
                        type="checkbox"
                        aria-label="Select job ${escapeHtml(item.id)}"
                        data-job-select="${escapeHtml(item.id)}"
                        ${isJobSelected(item.id) ? "checked" : ""}
                        ${deletable ? "" : "disabled"}
                      />
                    </td>
                    <td>
                      <strong>${escapeHtml(item.kind)}</strong>
                      ${access ? `<span class="subline">${escapeHtml(access.short_label || access.summary || "")}</span>` : ""}
                      <span class="subline mono">${escapeHtml(item.id)}</span>
                    </td>
                    <td>
                      ${escapeHtml(item.target_type)}:${escapeHtml(item.target_ref)}
                      <span class="subline">${escapeHtml(item.source)} by ${escapeHtml(item.requested_by)}</span>
                    </td>
                    <td>
                      ${badge(item.executor, "accent")}
                      <span class="subline">${escapeHtml(item.target_agent_id || "control-plane-local")}</span>
                    </td>
                    <td>
                      <div class="badge-row">
                        ${statusBadge(item.status)}
                        ${statusBadge(item.approval_status)}
                      </div>
                      <span class="subline">${deletable ? "Ready to delete" : "Delete available after the job reaches a terminal state."}</span>
                    </td>
                    <td>${escapeHtml(formatTimestamp(item.created_at))}</td>
                    <td>
                      <div class="table-actions">
                        <button class="secondary" data-job-log="${escapeHtml(item.id)}">Logs</button>
                        ${
                          item.approval_status === "pending"
                            ? `<button data-job-approve="${escapeHtml(item.id)}">Approve</button>`
                            : ""
                        }
                        ${canCancelJob(item) ? `<button class="danger" data-job-cancel="${escapeHtml(item.id)}">Cancel</button>` : ""}
                        ${deletable ? `<button class="danger" data-job-delete="${escapeHtml(item.id)}">Delete</button>` : ""}
                      </div>
                    </td>
                  </tr>
                `;
              })
              .join("")}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

function renderApprovals() {
  const items = entitiesState.jobs.items.filter((item) => item.approval_status === "pending");
  renderTable(
    "approvals-table",
    ["Job", "Target", "Executor", "Requested By", "Action"],
    items.map(
      (item) => `
        <tr>
          <td>
            <strong>${escapeHtml(item.kind)}</strong>
            <span class="subline mono">${escapeHtml(item.id)}</span>
          </td>
          <td>${escapeHtml(item.target_ref)}</td>
          <td>${badge(item.executor, "accent")}</td>
          <td>${escapeHtml(item.requested_by)}</td>
          <td>
            <div class="table-actions">
              <button data-job-approve="${escapeHtml(item.id)}">Approve</button>
              <button class="secondary" data-job-log="${escapeHtml(item.id)}">Logs</button>
              ${canCancelJob(item) ? `<button class="danger" data-job-cancel="${escapeHtml(item.id)}">Cancel</button>` : ""}
            </div>
          </td>
        </tr>
      `
    ),
    "No pending approvals."
  );
}

function renderSchedules() {
  const items = entitiesState.schedules.items;
  renderTable(
    "schedules-table",
    ["Schedule", "Kind", "Cron", "Next Run", "State", "Action"],
    items.map(
      (item) => `
        <tr>
          <td>
            <strong>${escapeHtml(item.name)}</strong>
            <span class="subline mono">${escapeHtml(item.id)}</span>
          </td>
          <td>${escapeHtml(item.kind)}</td>
          <td><span class="mono">${escapeHtml(item.cron_expr)}</span></td>
          <td>${escapeHtml(formatTimestamp(item.next_run_at))}</td>
          <td>${item.enabled ? statusBadge("enabled") : statusBadge("disabled")}</td>
          <td>
            <div class="table-actions">
              <button
                data-schedule-id="${escapeHtml(item.id)}"
                data-schedule-enabled="${item.enabled ? "false" : "true"}"
              >
                ${item.enabled ? "Disable" : "Enable"}
              </button>
            </div>
          </td>
        </tr>
      `
    ),
    "No schedules configured."
  );
}

function renderBackups() {
  const root = document.getElementById("backups-table");
  const items = getBackupItems();
  if (!items.length) {
    root.innerHTML = emptyState("No backup artifacts recorded.");
    return;
  }

  const selectedIds = selectedBackupIds();
  const selectedCount = selectedIds.length;
  root.innerHTML = `
    <div class="stack-toolbar">
      <div class="stack-selection-controls">
        <div>
          <strong>${pluralize(selectedCount, "backup")} selected</strong>
          <span class="subline">Local files will be deleted when managed here. Remote or virtual artifacts will have only their records removed.</span>
        </div>
        <div class="table-actions">
          <button type="button" class="secondary" data-backup-select-mode="all">Select all</button>
          <button type="button" class="secondary" data-backup-select-mode="none">Clear</button>
          <button type="button" class="danger" data-backup-delete-selected ${selectedCount ? "" : "disabled"}>
            ${selectedCount ? `Delete selected (${selectedCount})` : "Delete selected"}
          </button>
        </div>
      </div>
      <div class="table-shell">
        <table class="data-table backup-table">
          <thead>
            <tr>
              <th>Select</th>
              <th>Kind</th>
              <th>Target</th>
              <th>File</th>
              <th>Size</th>
              <th>Created</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            ${items
              .map((item) => {
                const metadata = item.metadata || {};
                const artifactLine = [item.artifact_host || metadata.host, item.artifact_source || metadata.source]
                  .filter(Boolean)
                  .join(" · ");
                return `
                  <tr>
                    <td>
                      <input
                        type="checkbox"
                        aria-label="Select backup ${escapeHtml(item.file_name || item.path || item.target_ref)}"
                        data-backup-select="${escapeHtml(item.id)}"
                        ${isBackupSelected(item.id) ? "checked" : ""}
                      />
                    </td>
                    <td>${badge(item.kind, "accent")}</td>
                    <td>
                      ${escapeHtml(item.target_ref)}
                      ${artifactLine ? `<span class="subline">${escapeHtml(artifactLine)}</span>` : ""}
                    </td>
                    <td>
                      <strong>${escapeHtml(item.file_name || item.path || item.target_ref)}</strong>
                      <span class="subline">${item.exists ? "Present" : "Missing or virtual artifact"}</span>
                      <span class="path-pill mono" title="${escapeHtml(item.path)}">${escapeHtml(item.path)}</span>
                    </td>
                    <td>${escapeHtml(formatBytes(item.size_bytes))}</td>
                    <td>${escapeHtml(formatTimestamp(item.created_at))}</td>
                    <td>
                      <div class="table-actions">
                        <button
                          class="${item.delete_supported ? "danger" : "secondary"}"
                          data-backup-delete="${escapeHtml(item.id)}"
                          data-backup-delete-supported="${item.delete_supported ? "true" : "false"}"
                        >
                          ${item.delete_supported ? "Delete" : "Remove"}
                        </button>
                      </div>
                    </td>
                  </tr>
                `;
              })
              .join("")}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

function renderSettings() {
  const settings = entitiesState.settings;
  const context = entitiesState.context || {};
  const dockerUpdates = settings.docker_updates || {};
  siteChip.textContent = settings.site_name;
  document.title = settings.ui.app_display_name || `${settings.ui.app_name} v${settings.ui.app_version}`;

  setInputValue("public-base-url", settings.public.base_url || "");
  setInputValue("public-repo-url", settings.public.repo_url || "");
  setInputValue("public-repo-ref", settings.public.repo_ref || "");
  setInputValue("public-install-script-url", settings.public.install_script_url_override || "");
  setInputValue("public-agent-compose-dir", settings.public.agent_compose_dir || "");
  setInputValue("public-rackpatch-compose-dir", settings.public.rackpatch_compose_dir || "");
  setInputValue("docker-backup-retention", String(dockerUpdates.backup_retention || 3));
  document.getElementById("docker-run-backup-commands").checked = Boolean(dockerUpdates.run_backup_commands);
  setInputValue("telegram-chat-ids", settings.telegram.chat_ids_csv || "");
  if (document.activeElement !== document.getElementById("telegram-bot-token")) {
    document.getElementById("telegram-bot-token").value = "";
  }
  document.getElementById("telegram-clear-token").checked = false;

  document.getElementById("telegram-status").textContent = [
    `Bot token: ${settings.telegram.bot_token_configured ? settings.telegram.masked_bot_token : "not configured"}`,
    `Allowed chats: ${settings.telegram.chat_ids.length ? settings.telegram.chat_ids.join(", ") : "none"}`,
    `Service mode: polling getUpdates`,
  ].join("\n");

  document.getElementById("settings-paths").textContent = [
    `Site: ${settings.site_name}`,
    `Site root: ${settings.site_root}`,
    `Inventory: ${settings.inventory_path}`,
    `Stacks: ${settings.stacks_path}`,
    `Maintenance: ${settings.maintenance_path}`,
    `Public repo: ${settings.public.repo_url}@${settings.public.repo_ref}`,
    `Agent compose dir: ${settings.public.agent_compose_dir}`,
    `Rackpatch compose dir: ${settings.public.rackpatch_compose_dir}`,
    `Docker backup retention: ${dockerUpdates.backup_retention || 3}`,
    `Docker backup commands: ${dockerUpdates.run_backup_commands ? "enabled" : "disabled"}`,
    `Host maintenance: opt-in via a limited helper command, not broad sudo.`,
  ].join("\n");

  document.getElementById("telegram-help").textContent = [
    "/status",
    "/stacks",
    "/hosts",
    "/jobs [limit]",
    "/logs <job-id>",
    "/approvals",
    "/approve <job-id>",
    "/update <stack|all> [dry|live]",
    "/patch <host|all> [dry|live]",
    "/proxmox-patch <host|proxmox_nodes> [dry|live]",
    "/proxmox-reboot <host|proxmox_nodes> [dry|live]",
    "/backup <volume>",
    "/rollback <stack>",
    "/schedules",
    "/schedule <name-or-id> on|off",
    '/job <kind> <target_type> <target_ref> {"executor":"agent"}',
  ].join("\n");

  if (automationApi) {
    const resources = context.api?.resources || {};
    const examples = context.api?.examples || {};
    const jobKinds = (context.job_kinds || []).map(
      (item) => `- ${item.kind} (${item.target_type}, ${item.mode})`
    );
    automationApi.textContent = [
      "Machine-friendly operator surface",
      "Primary endpoint: /api/v1/context",
      `Auth: ${context.api?.auth?.header || "Authorization: Bearer <token>"}`,
      "Release status is included in settings.release and context.release.",
      "",
      "Endpoints:",
      ...Object.entries(resources).map(([name, value]) => `- ${name}: ${value}`),
      "",
      "Examples:",
      `- login: ${examples.login || "n/a"}`,
      `- context: ${examples.context || "n/a"}`,
      `- jobs: ${examples.jobs || "n/a"}`,
      "",
      "Job kinds:",
      ...(jobKinds.length ? jobKinds : ["- none reported"]),
    ].join("\n");
  }

  if (automationLive) {
    const running = context.jobs?.running || [];
    const approvals = context.jobs?.pending_approvals || [];
    automationLive.textContent = [
      `Running jobs: ${running.length}`,
      ...(running.length
        ? running.map((item) => `- [${shortId(item.id)}] ${item.kind} ${item.target_ref} (${item.executor})`)
        : ["- none"]),
      "",
      `Pending approvals: ${approvals.length}`,
      ...(approvals.length
        ? approvals.map((item) => `- [${shortId(item.id)}] ${item.kind} ${item.target_ref}`)
        : ["- none"]),
    ].join("\n");
  }
  if (releaseStatus) {
    const latest = settings.release.latest || {};
    const stack = settings.release.stack || {};
    const agentSummary = (settings.release.agents || {}).summary || {};
    releaseStatus.textContent = [
      `Current stack: v${stack.current_version || "unknown"}`,
      `Latest upstream: ${latest.version || "unavailable"}`,
      `Stack state: ${releaseLabel(stack.release_state || "unknown")}`,
      `Checked at: ${latest.checked_at || latest.published_at || "n/a"}`,
      latest.url ? `Release URL: ${latest.url}` : "Release URL: n/a",
      latest.error ? `Error: ${latest.error}` : "",
      "",
      `Agents total/current/outdated/unknown: ${agentSummary.total || 0}/${agentSummary.current || 0}/${agentSummary.outdated || 0}/${agentSummary.unknown || 0}`,
    ]
      .filter(Boolean)
      .join("\n");
  }
  if (releaseUpdateCommands) {
    const updateCommands = settings.release.update_commands || {};
    const agentUpdates = updateCommands.agents || {};
    releaseUpdateCommands.textContent = [
      "Update rackpatch stack:",
      updateCommands.stack || "unavailable",
      "",
      "Update compose-mode agents:",
      agentUpdates.compose || "unavailable",
      "",
      "Update container-mode agents:",
      agentUpdates.container || "unavailable",
      "",
      "Update systemd agents:",
      agentUpdates.systemd || "unavailable",
    ].join("\n");
  }
  if (fleetUpdateSummary || fleetUpdateCommands) {
    const fleet = settings.release.update_commands?.fleet_agents || {};
    const skippedNames = new Set(
      (Array.isArray(fleet.skipped) ? fleet.skipped : []).flatMap((item) =>
        [item.name, item.agent_name].map((value) => String(value || "")).filter(Boolean)
      )
    );
    const queueableAgents = (entitiesState.agents?.items || []).filter((item) => {
      const identity = [item.display_name, item.name].map((value) => String(value || "")).filter(Boolean);
      if (identity.some((value) => skippedNames.has(value))) {
        return false;
      }
      const capabilities = Array.isArray(item.capabilities) ? item.capabilities.map((value) => String(value)) : [];
      if (!capabilities.includes("agent-self-update")) {
        return false;
      }
      const agentStatus = String(item.status || "").toLowerCase();
      const releaseState = String(item.release_state || "").toLowerCase();
      if (agentStatus !== "online") {
        return false;
      }
      return !["current", "ahead"].includes(releaseState);
    });
    if (fleetUpdateSummary) {
      const skipped = Array.isArray(fleet.skipped) ? fleet.skipped : [];
      fleetUpdateSummary.textContent = [
        fleet.summary || "Unavailable",
        `Included commands: ${fleet.included ?? 0}`,
        `Queueable agents: ${queueableAgents.length}`,
        `Skipped agents: ${skipped.length}`,
        ...(skipped.length
          ? [
              "",
              ...skipped.map((item) => {
                const reason = item.reason ? `: ${item.reason}` : "";
                return `- ${item.name || "unknown"} (${item.mode || "unknown"})${reason}`;
              }),
            ]
          : []),
      ].join("\n");
    }
    if (fleetUpdateCommands) {
      fleetUpdateCommands.textContent = fleet.command || "unavailable";
    }
    if (fleetUpdateQueueButton) {
      fleetUpdateQueueButton.disabled = queueableAgents.length < 1;
    }
  }
  renderInstallPreviews();
}

function renderAll() {
  renderOverview();
  renderStacks();
  renderHosts();
  renderAgents();
  populateJobKindSelect();
  syncJobForm(jobKindSelect.value, {
    resetOptions: jobFormState.kind !== jobKindSelect.value,
    preserveSelection: jobFormState.kind === jobKindSelect.value,
  });
  syncJobLogPanel();
  renderJobs();
  renderApprovals();
  renderSchedules();
  renderBackups();
  renderSettings();
}

function pruneSelectionState() {
  setSelectedJobs(selectionState.jobs);
  setSelectedStacks(selectionState.stacks);
  setSelectedBackups(selectionState.backups);
  setSelectedJobStacks(jobFormState.selectedStacks, jobFormState.kind || jobKindSelect.value);
  setSelectedJobHosts(jobFormState.selectedHosts, jobFormState.kind || jobKindSelect.value);
}

async function loadDashboard() {
  const [overview, agents, hosts, stacks, dockerUpdates, jobs, schedules, backups, settings, jobKinds, context] = await Promise.all([
    api("/api/v1/overview"),
    api("/api/v1/agents"),
    api("/api/v1/hosts"),
    api("/api/v1/stacks"),
    api("/api/v1/docker/updates").catch((error) => ({
      ...EMPTY_DOCKER_UPDATES,
      error: error.message,
    })),
    api("/api/v1/jobs"),
    api("/api/v1/schedules"),
    api("/api/v1/backups"),
    api("/api/v1/settings"),
    api("/api/v1/job-kinds"),
    api("/api/v1/context"),
  ]);
  Object.assign(entitiesState, { overview, agents, hosts, stacks, dockerUpdates, jobs, schedules, backups, settings, jobKinds, context });
  pruneSelectionState();
  renderAll();
}

async function refreshDashboard() {
  await loadDashboard();
  if (uiState.selectedJob) {
    try {
      await selectJob(uiState.selectedJob, true);
    } catch (_) {
      clearSelectedJobDetails("Previously selected job was deleted.");
    }
  }
}

async function selectJob(jobId, silent = false) {
  uiState.selectedJob = jobId;
  const [job, events] = await Promise.all([api(`/api/v1/jobs/${jobId}`), api(`/api/v1/jobs/${jobId}/events`)]);
  const resultMarkup = buildJobResultMarkup(job);
  jobResult.innerHTML = resultMarkup;
  jobEvents.textContent = events.items.map((item) => `[${item.ts}] ${item.message}`).join("\n") || "No events yet.";
  if (!silent && uiState.currentPage !== "jobs") {
    setPage("jobs");
  }
}

async function approveJob(jobId) {
  await api(`/api/v1/jobs/${jobId}/approve`, { method: "POST" });
  showFlash(`Approved job ${shortId(jobId)}.`);
  await refreshDashboard();
}

async function cancelJob(jobId) {
  await api(`/api/v1/jobs/${jobId}/cancel`, { method: "POST" });
  showFlash(`Cancelled job ${shortId(jobId)}.`);
  await refreshDashboard();
}

async function deleteJob(jobId) {
  const item = getJobItems().find((job) => job.id === jobId);
  if (!canDeleteJob(item)) {
    showFlash("Only completed, failed, or cancelled jobs can be deleted.", "error");
    return;
  }
  if (!window.confirm("Delete this job and its saved event log?")) {
    return;
  }
  const result = await api(`/api/v1/jobs/${jobId}`, { method: "DELETE" });
  setSelectedJobs(selectedJobIds().filter((id) => id !== jobId));
  if (uiState.selectedJob === jobId) {
    clearSelectedJobDetails("Deleted job log.");
  }
  await refreshDashboard();
  const deletedEvents = Number(result.deleted_event_count || 0);
  showFlash(
    `Deleted job ${shortId(jobId)}.${deletedEvents ? ` Removed ${deletedEvents} log event${deletedEvents === 1 ? "" : "s"}.` : ""}`
  );
}

async function deleteSelectedJobs() {
  const selectedIds = selectedJobIds();
  if (!selectedIds.length) {
    showFlash("Select at least one finished job first.", "error");
    return;
  }

  const message = `Delete ${selectedIds.length} selected job${selectedIds.length === 1 ? "" : "s"} and their saved event logs?`;
  if (!window.confirm(message)) {
    return;
  }

  const results = await Promise.allSettled(
    selectedIds.map((jobId) => api(`/api/v1/jobs/${jobId}`, { method: "DELETE" }))
  );
  const deletedIds = [];
  let deletedEvents = 0;
  let failedCount = 0;

  results.forEach((result, index) => {
    if (result.status === "fulfilled") {
      deletedIds.push(selectedIds[index]);
      deletedEvents += Number(result.value.deleted_event_count || 0);
    } else {
      failedCount += 1;
    }
  });

  setSelectedJobs(selectedJobIds().filter((id) => !deletedIds.includes(id)));
  if (uiState.selectedJob && deletedIds.includes(uiState.selectedJob)) {
    clearSelectedJobDetails("Deleted job log.");
  }
  await refreshDashboard();

  const summary = [
    `Deleted ${deletedIds.length} job${deletedIds.length === 1 ? "" : "s"}.`,
    deletedEvents ? `Removed ${deletedEvents} log event${deletedEvents === 1 ? "" : "s"}.` : "",
    failedCount ? `${failedCount} job${failedCount === 1 ? "" : "s"} could not be deleted.` : "",
  ]
    .filter(Boolean)
    .join(" ");
  showFlash(summary, failedCount && !deletedIds.length ? "error" : "success");
}

async function toggleSchedule(scheduleId, enabled) {
  await api(`/api/v1/schedules/${scheduleId}/toggle`, {
    method: "POST",
    body: JSON.stringify({ enabled }),
  });
  showFlash(`${enabled ? "Enabled" : "Disabled"} schedule ${shortId(scheduleId)}.`);
  await refreshDashboard();
}

async function deleteBackup(backupId, deleteSupported) {
  const message = deleteSupported
    ? "Delete this backup file and remove its record?"
    : "Remove this backup record from the UI list?";
  if (!window.confirm(message)) {
    return;
  }
  const result = await api(`/api/v1/backups/${backupId}`, { method: "DELETE" });
  if (result.file_deleted) {
    showFlash(`Deleted backup ${shortId(backupId)}.`);
  } else if (result.delete_reason) {
    showFlash(`Removed backup ${shortId(backupId)} record (${result.delete_reason}).`);
  } else {
    showFlash(`Removed backup ${shortId(backupId)} record.`);
  }
  await refreshDashboard();
}

async function deleteSelectedBackups() {
  const selectedIds = selectedBackupIds();
  if (!selectedIds.length) {
    showFlash("Select at least one backup first.", "error");
    return;
  }

  const itemsById = new Map(getBackupItems().map((item) => [item.id, item]));
  const localDeleteCount = selectedIds.filter((id) => itemsById.get(id)?.delete_supported).length;
  const recordOnlyCount = selectedIds.length - localDeleteCount;
  const messageParts = [`Delete ${selectedIds.length} selected backup record${selectedIds.length === 1 ? "" : "s"}?`];
  if (localDeleteCount) {
    messageParts.push(`${localDeleteCount} local file${localDeleteCount === 1 ? "" : "s"} will also be deleted.`);
  }
  if (recordOnlyCount) {
    messageParts.push(`${recordOnlyCount} remote or virtual artifact${recordOnlyCount === 1 ? "" : "s"} will only be removed from the list.`);
  }
  if (!window.confirm(messageParts.join(" "))) {
    return;
  }

  const outcomes = {
    fileDeleted: 0,
    recordOnly: 0,
  };
  for (const backupId of selectedIds) {
    const result = await api(`/api/v1/backups/${backupId}`, { method: "DELETE" });
    if (result.file_deleted) {
      outcomes.fileDeleted += 1;
    } else {
      outcomes.recordOnly += 1;
    }
  }

  setSelectedBackups([]);
  await refreshDashboard();
  const summary = [
    `Removed ${selectedIds.length} backup record${selectedIds.length === 1 ? "" : "s"}.`,
    outcomes.fileDeleted ? `${outcomes.fileDeleted} file${outcomes.fileDeleted === 1 ? "" : "s"} deleted.` : "",
    outcomes.recordOnly ? `${outcomes.recordOnly} record-only removal${outcomes.recordOnly === 1 ? "" : "s"}.` : "",
  ]
    .filter(Boolean)
    .join(" ");
  showFlash(summary);
}

function formatQueueResult(result, fallbackKind, fallbackTarget) {
  const kind = result?.kind || fallbackKind;
  const kindLabel = String(getJobKindConfig(kind).label || kind || "job");
  const targetRef = result?.target_ref || fallbackTarget;
  const queuedJobs = Array.isArray(result?.jobs) ? result.jobs : [];
  const jobIds = Array.isArray(result?.job_ids) ? result.job_ids : [];
  const skipped = Array.isArray(result?.skipped) ? result.skipped : [];
  if (!result?.fanout) {
    const jobId = result?.id ? shortId(result.id) : "unknown";
    return {
      detail: `Queued job ${jobId}`,
      flash: `Queued ${kindLabel} for ${targetRef}.`,
    };
  }

  const queuedCount = Number(result?.queued_count || queuedJobs.length || jobIds.length || 0);
  const lines = [`Queued ${queuedCount} ${kindLabel} job${queuedCount === 1 ? "" : "s"} for ${targetRef}.`];
  if (jobIds.length) {
    lines.push(`Jobs: ${jobIds.map((value) => shortId(value)).join(", ")}`);
  }
  if (skipped.length) {
    lines.push(
      `Skipped: ${skipped
        .slice(0, 4)
        .map((item) => `${item.target_ref} (${item.reason})`)
        .join("; ")}${skipped.length > 4 ? ` +${skipped.length - 4} more` : ""}`
    );
  }
  return {
    detail: lines.join("\n"),
    flash: `Queued ${queuedCount} ${kindLabel} job${queuedCount === 1 ? "" : "s"}${skipped.length ? `; skipped ${skipped.length}` : ""}.`,
  };
}

async function queuePreset(kind, targetType, targetRef, payload) {
  const result = await api("/api/v1/jobs", {
    method: "POST",
    body: JSON.stringify({ kind, target_type: targetType, target_ref: targetRef, payload }),
  });
  const queueSummary = formatQueueResult(result, kind, targetRef);
  showFlash(queueSummary.flash);
  await refreshDashboard();
  return result;
}

async function savePublicSettings() {
  await api("/api/v1/settings/public", {
    method: "POST",
    body: JSON.stringify({
      base_url: document.getElementById("public-base-url").value,
      repo_url: document.getElementById("public-repo-url").value,
      repo_ref: document.getElementById("public-repo-ref").value,
      install_script_url: document.getElementById("public-install-script-url").value,
      agent_compose_dir: document.getElementById("public-agent-compose-dir").value,
      rackpatch_compose_dir: document.getElementById("public-rackpatch-compose-dir").value,
    }),
  });
  await refreshDashboard();
  document.getElementById("public-settings-result").textContent = "Saved public repo settings.";
  showFlash("Saved public repo settings.");
}

async function saveHost() {
  const payload = hostFormPayload();
  if (!payload.name) {
    throw new Error("Host name is required.");
  }
  const originalName = currentHostEditorName();
  const isEdit = Boolean(originalName);
  const path = isEdit ? `/api/v1/hosts/${encodeURIComponent(originalName)}` : "/api/v1/hosts";
  const method = isEdit ? "PUT" : "POST";
  await api(path, {
    method,
    body: JSON.stringify(payload),
  });
  await refreshDashboard();
  populateHostEditor();
  syncHostEditorOptions();
  hostFormResult.textContent = isEdit
    ? `Saved host ${payload.name}.`
    : `Created host ${payload.name}.`;
  showFlash(isEdit ? `Saved host ${payload.name}.` : `Created host ${payload.name}.`);
}

async function deleteHostEntry(hostName) {
  if (!hostName) {
    return;
  }
  const confirmed = window.confirm(`Delete host ${hostName} from the inventory?`);
  if (!confirmed) {
    return;
  }
  await api(`/api/v1/hosts/${encodeURIComponent(hostName)}`, { method: "DELETE" });
  await refreshDashboard();
  if (currentHostEditorName() === hostName) {
    populateHostEditor();
  }
  syncHostEditorOptions();
  hostFormResult.textContent = `Deleted host ${hostName}.`;
  showFlash(`Deleted host ${hostName}.`);
}

function editHostEntry(hostName) {
  const host = (entitiesState.hosts?.items || []).find((item) => item.name === hostName);
  if (!host) {
    showFlash(`Host ${hostName} was not found.`, "error");
    return;
  }
  populateHostEditor(host);
  syncHostEditorOptions();
  if (uiState.currentPage !== "hosts") {
    setPage("hosts");
  }
  document.getElementById("host-name").focus();
}

async function saveDockerUpdateSettings() {
  const retentionValue = document.getElementById("docker-backup-retention").value;
  await api("/api/v1/settings/docker-updates", {
    method: "POST",
    body: JSON.stringify({
      backup_retention: retentionValue ? Number(retentionValue) : 3,
      run_backup_commands: document.getElementById("docker-run-backup-commands").checked,
    }),
  });
  await refreshDashboard();
  document.getElementById("docker-update-settings-result").textContent = "Saved Docker update settings.";
  showFlash("Saved Docker update settings.");
}

async function saveTelegramSettings() {
  const payload = {
    chat_ids: document.getElementById("telegram-chat-ids").value,
  };
  const tokenInput = document.getElementById("telegram-bot-token").value;
  const clearToken = document.getElementById("telegram-clear-token").checked;
  if (clearToken) {
    payload.bot_token = "";
  } else if (tokenInput.trim()) {
    payload.bot_token = tokenInput.trim();
  }
  await api("/api/v1/settings/telegram", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  await refreshDashboard();
  document.getElementById("telegram-settings-result").textContent = "Saved Telegram settings.";
  showFlash("Saved Telegram settings.");
}

async function createAgentToken() {
  const label = document.getElementById("agent-token-label").value || "manual-token";
  const result = await api("/api/v1/settings/agent-tokens", {
    method: "POST",
    body: JSON.stringify({ label }),
  });
  const blocks = result.agent_install || {};
  document.getElementById("agent-token-result").textContent = [
    `Label: ${result.label}`,
    `Token: ${result.token}`,
    "",
    `Docker Compose command:`,
    blocks.compose,
    "",
    `Docker deploy command:`,
    blocks.container,
    "",
    `Systemd install:`,
    blocks.systemd,
  ].join("\n");
  showFlash(`Created agent token ${result.label}.`);
}

function logoutUser() {
  localStorage.removeItem("ops_token");
  const freshState = createState();
  sessionState.token = "";
  Object.assign(uiState, freshState.ui);
  Object.assign(jobFormState, freshState.jobForm);
  Object.assign(selectionState, freshState.selection);
  Object.assign(entitiesState, freshState.entities);
  clearSelectedJobDetails();
  appScreen.classList.add("hidden");
  loginScreen.classList.remove("hidden");
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  loginError.textContent = "";
  try {
    const result = await api("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({
        username: document.getElementById("username").value,
        password: document.getElementById("password").value,
      }),
      headers: { "Content-Type": "application/json" },
    });
    sessionState.token = result.token;
    localStorage.setItem("ops_token", sessionState.token);
    loginScreen.classList.add("hidden");
    appScreen.classList.remove("hidden");
    syncPageFromHash();
    await refreshDashboard();
  } catch (error) {
    loginError.textContent =
      error.message === "invalid credentials" ? "Invalid username or password." : error.message;
  }
});

document.getElementById("refresh-all").addEventListener("click", async () => {
  await refreshDashboard();
  showFlash("Dashboard refreshed.");
});

document.getElementById("logout").addEventListener("click", () => {
  logoutUser();
});

jobLogExpandButton.addEventListener("click", () => {
  uiState.jobLogExpanded = !uiState.jobLogExpanded;
  syncJobLogPanel();
});

jobStackToggle.addEventListener("click", () => {
  if (jobStackToggle.disabled) {
    return;
  }
  setJobStackMenu();
});

jobStackMenu.addEventListener("click", (event) => {
  const bulkAction = event.target.closest("[data-job-stack-bulk]");
  if (!bulkAction) {
    return;
  }
  const nextSelection = bulkAction.dataset.jobStackBulk === "all" ? getJobStacksForKind(jobKindSelect.value).map((stack) => stack.name) : [];
  setSelectedJobStacks(nextSelection, jobKindSelect.value);
  renderJobStackOptions(jobKindSelect.value, true);
});

jobStackOptions.addEventListener("change", (event) => {
  const input = event.target.closest('input[type="checkbox"]');
  if (!input) {
    return;
  }
  const next = new Set(getSelectedJobStacks(jobKindSelect.value));
  if (input.checked) {
    next.add(input.value);
  } else {
    next.delete(input.value);
  }
  setSelectedJobStacks([...next], jobKindSelect.value);
  updateJobStackSelectionState(jobKindSelect.value);
});

jobKindSelect.addEventListener("change", () => {
  if (jobFormState.kind) {
    storeRenderedJobOptionValues(jobFormState.kind);
  }
  syncJobForm(jobKindSelect.value, { preserveSelection: false });
});

jobHostToggle.addEventListener("click", () => {
  if (jobHostToggle.disabled) {
    return;
  }
  setJobHostMenu();
});

jobHostMenu.addEventListener("click", (event) => {
  const bulkAction = event.target.closest("[data-job-host-bulk]");
  if (!bulkAction) {
    return;
  }
  const nextSelection =
    bulkAction.dataset.jobHostBulk === "all"
      ? getJobHostsForKind(jobKindSelect.value)
          .filter((host) => getJobHostAccess(host, jobKindSelect.value).eligible)
          .map((host) => host.name)
      : [];
  setSelectedJobHosts(nextSelection, jobKindSelect.value);
  renderJobHostOptions(jobKindSelect.value, true);
});

jobHostOptions.addEventListener("change", (event) => {
  const input = event.target.closest('input[type="checkbox"]');
  if (!input) {
    return;
  }
  const next = new Set(getSelectedJobHosts(jobKindSelect.value));
  if (input.checked) {
    next.add(input.value);
  } else {
    next.delete(input.value);
  }
  setSelectedJobHosts([...next], jobKindSelect.value);
  updateJobHostSelectionState(jobKindSelect.value);
});

const rerenderJobHostOptions = debounce(() => {
  if (getJobKindConfig(jobKindSelect.value).mode === "host_multi") {
    renderJobHostOptions(jobKindSelect.value, true);
  }
}, 120);

jobOptions.addEventListener("change", () => {
  storeRenderedJobOptionValues(jobKindSelect.value);
  rerenderJobHostOptions();
});

jobOptions.addEventListener("input", () => {
  storeRenderedJobOptionValues(jobKindSelect.value);
  rerenderJobHostOptions();
});

jobManualTargetInput.addEventListener("input", () => {
  jobFormState.manualTarget = jobManualTargetInput.value;
});

document.addEventListener("click", (event) => {
  if (!jobStackPicker.contains(event.target)) {
    setJobStackMenu(false);
  }
  if (!jobHostPicker.contains(event.target)) {
    setJobHostMenu(false);
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && uiState.jobLogExpanded) {
    uiState.jobLogExpanded = false;
    syncJobLogPanel();
  }
});

document.getElementById("job-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  jobResult.textContent = "";
  await withAsyncAction(async () => {
    const request = buildJobRequest();
    const result = await api("/api/v1/jobs", {
      method: "POST",
      body: JSON.stringify({
        kind: request.kind,
        target_type: request.targetType,
        target_ref: request.targetRef,
        payload: request.payload,
      }),
    });
    const queueSummary = formatQueueResult(result, request.kind, request.targetRef);
    jobResult.textContent = queueSummary.detail;
    showFlash(queueSummary.flash);
    await refreshDashboard();
  }, (error) => {
    jobResult.textContent = error.message;
  });
});

if (fleetUpdateQueueButton) {
  fleetUpdateQueueButton.addEventListener("click", async () => {
    await withAsyncAction(async () => {
      const result = await queuePreset("agent_update", "agent", "all", {
        executor: "agent",
        requires_approval: false,
      });
      if (fleetUpdateSummary && result?.fanout) {
        const skipped = Array.isArray(result.skipped) ? result.skipped : [];
        fleetUpdateSummary.textContent = [
          `Queued ${result.queued_count || 0} agent update job${Number(result.queued_count || 0) === 1 ? "" : "s"}.`,
          `Skipped agents: ${skipped.length}`,
          ...(skipped.length
            ? [
                "",
                ...skipped.map((item) => `- ${item.target_ref || "unknown"}: ${item.reason || "not eligible"}`),
              ]
            : []),
          ].join("\n");
      }
    }, (error) => {
      showFlash(error.message, "error");
    });
  });
}

if (fleetUpdateCopyButton) {
  fleetUpdateCopyButton.addEventListener("click", async () => {
    const command = fleetUpdateCommands?.textContent || "";
    if (!command.trim() || command === "unavailable") {
      showFlash("No fleet update bundle is available yet.");
      return;
    }
    await withAsyncAction(async () => {
      await navigator.clipboard.writeText(command);
      showFlash("Copied the fleet update bundle.");
    }, (error) => {
      showFlash(`Copy failed: ${error.message}`);
    });
  });
}

document.getElementById("public-settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await savePublicSettings();
});

document.getElementById("docker-update-settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await saveDockerUpdateSettings();
});

document.getElementById("telegram-settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await saveTelegramSettings();
});

document.getElementById("agent-token-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await createAgentToken();
});

hostForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await saveHost();
});

hostFormResetButton.addEventListener("click", () => {
  populateHostEditor();
  syncHostEditorOptions();
});

appScreen.addEventListener("change", (event) => {
  const jobSelect = event.target.closest("[data-job-select]");
  if (jobSelect) {
    toggleJobSelection(jobSelect.dataset.jobSelect, Boolean(jobSelect.checked));
    return;
  }

  const stackSelect = event.target.closest("[data-stack-select]");
  if (stackSelect) {
    toggleStackSelection(stackSelect.dataset.stackSelect, Boolean(stackSelect.checked));
    return;
  }

  const backupSelect = event.target.closest("[data-backup-select]");
  if (backupSelect) {
    toggleBackupSelection(backupSelect.dataset.backupSelect, Boolean(backupSelect.checked));
    return;
  }

  if (event.target.matches("[data-stack-update-approval]")) {
    uiState.stackUpdateRequiresApproval = Boolean(event.target.checked);
  }
});

const stackActionHandlers = {
  async check(stackName) {
    await queuePreset("docker_check", "stack", stackName, {
      executor: "agent",
      selected_stacks: [stackName],
      requires_approval: false,
    });
  },
  async "dry-run"(stackName) {
    await queuePreset("docker_update", "stack", stackName, {
      executor: "agent",
      selected_stacks: [stackName],
      dry_run: true,
      requires_approval: false,
    });
  },
  async update(stackName) {
    await queuePreset("docker_update", "stack", stackName, {
      executor: "agent",
      selected_stacks: [stackName],
      dry_run: false,
      requires_approval: uiState.stackUpdateRequiresApproval,
    });
  },
  async rollback(stackName) {
    await queuePreset("rollback", "stack", stackName, { executor: "worker" });
  },
  details(stackName) {
    toggleStackDetails(stackName);
  },
};

const appClickHandler = createDelegatedHandler([
  {
    selector: "[data-page-link]",
    handler: (node) => setPage(node.dataset.pageLink),
  },
  {
    selector: "[data-install-mode]",
    handler: (node) => {
      uiState.installPreviewMode = node.dataset.installMode || "compose";
      renderInstallPreviews();
    },
  },
  {
    selector: "[data-stack-action]",
    handler: async (node) => {
      const stackName = node.dataset.stackName;
      const action = node.dataset.stackAction;
      if (!stackName || !action || !stackActionHandlers[action]) {
        return;
      }
      await stackActionHandlers[action](stackName, node);
    },
  },
  {
    selector: "[data-stack-bulk-check]",
    handler: async () => {
      await queuePreset("docker_check", "stack", "all", {
        executor: "agent",
        selected_stacks: getDockerUpdateItems().map((item) => item.name),
        requires_approval: false,
      });
    },
  },
  {
    selector: "[data-stack-select-mode]",
    handler: (node) => {
      if (node.dataset.stackSelectMode === "outdated") {
        setSelectedStacks(
          getDockerUpdateItems()
            .filter((item) => item.selection_eligible)
            .map((item) => item.name)
        );
      } else {
        setSelectedStacks([]);
      }
      renderStacks();
    },
  },
  {
    selector: "[data-stack-update-selected]",
    handler: async () => {
      const selectedStacks = selectedStackNames();
      if (!selectedStacks.length) {
        showFlash("Select at least one eligible outdated stack first.", "error");
        return;
      }
      const confirmMessage = uiState.stackUpdateRequiresApproval
        ? `Queue ${selectedStacks.length} live stack update job(s) for approval?`
        : `Queue ${selectedStacks.length} live stack update job(s) now?`;
      if (!window.confirm(confirmMessage)) {
        return;
      }
      await queuePreset("docker_update", "stack", "all", {
        executor: "agent",
        selected_stacks: selectedStacks,
        dry_run: false,
        requires_approval: uiState.stackUpdateRequiresApproval,
      });
      setSelectedStacks([]);
      renderStacks();
    },
  },
  {
    selector: "[data-host-kind]",
    handler: async (node) => {
      const hostName = node.dataset.hostName;
      const kind = node.dataset.hostKind;
      const dryRun = node.dataset.dryRun === "true";
      if (!hostName || !kind) {
        return;
      }
      const payload = { executor: "agent" };
      if (kind !== "package_check") {
        payload.dry_run = dryRun;
      }
      if (dryRun || kind === "package_check") {
        payload.requires_approval = false;
      }
      if (kind === "package_check") {
        payload.hosts = [hostName];
      }
      if (kind.startsWith("proxmox")) {
        payload.limit = hostName;
      }
      if (kind === "proxmox_reboot") {
        payload.reboot_mode = node.dataset.rebootMode || "soft";
      }
      await queuePreset(kind, "host", hostName, payload);
    },
  },
  {
    selector: "[data-host-edit]",
    handler: (node) => {
      editHostEntry(node.dataset.hostEdit);
    },
  },
  {
    selector: "[data-host-delete]",
    handler: async (node) => {
      await deleteHostEntry(node.dataset.hostDelete);
    },
  },
  {
    selector: "[data-job-log]",
    handler: async (node) => {
      await selectJob(node.dataset.jobLog);
    },
  },
  {
    selector: "[data-job-approve]",
    handler: async (node) => {
      await approveJob(node.dataset.jobApprove);
    },
  },
  {
    selector: "[data-job-cancel]",
    handler: async (node) => {
      await cancelJob(node.dataset.jobCancel);
    },
  },
  {
    selector: "[data-job-delete]",
    handler: async (node) => {
      await deleteJob(node.dataset.jobDelete);
    },
  },
  {
    selector: "[data-job-select-mode]",
    handler: (node) => {
      const now = Date.now();
      const matchingIds = getJobItems()
        .filter((item) => canDeleteJob(item))
        .filter((item) => {
          if (node.dataset.jobSelectMode === "deletable") {
            return true;
          }
          const createdAt = new Date(item.created_at).getTime();
          if (!Number.isFinite(createdAt)) {
            return false;
          }
          if (node.dataset.jobSelectMode === "older-1d") {
            return now - createdAt >= 24 * 60 * 60 * 1000;
          }
          if (node.dataset.jobSelectMode === "older-7d") {
            return now - createdAt >= 7 * 24 * 60 * 60 * 1000;
          }
          return false;
        })
        .map((item) => item.id);
      setSelectedJobs(node.dataset.jobSelectMode === "none" ? [] : matchingIds);
      renderJobs();
    },
  },
  {
    selector: "[data-job-delete-selected]",
    handler: async () => {
      await deleteSelectedJobs();
    },
  },
  {
    selector: "[data-schedule-id]",
    handler: async (node) => {
      await toggleSchedule(node.dataset.scheduleId, node.dataset.scheduleEnabled === "true");
    },
  },
  {
    selector: "[data-backup-delete]",
    handler: async (node) => {
      await deleteBackup(node.dataset.backupDelete, node.dataset.backupDeleteSupported === "true");
    },
  },
  {
    selector: "[data-backup-select-mode]",
    handler: (node) => {
      if (node.dataset.backupSelectMode === "all") {
        setSelectedBackups(getBackupItems().map((item) => item.id));
      } else {
        setSelectedBackups([]);
      }
      renderBackups();
    },
  },
  {
    selector: "[data-backup-delete-selected]",
    handler: async () => {
      await deleteSelectedBackups();
    },
  },
]);

appScreen.addEventListener("click", async (event) => {
  await withAsyncAction(() => appClickHandler(event), (error) => {
    showFlash(error.message, "error");
  });
});

window.addEventListener("hashchange", syncPageFromHash);

async function bootstrap() {
  populateHostEditor();
  syncHostEditorOptions();
  await loadAppVersion();
  syncPageFromHash();

  if (sessionState.token) {
    loginScreen.classList.add("hidden");
    appScreen.classList.remove("hidden");
    refreshDashboard().catch((error) => {
      loginError.textContent = error.message;
      logoutUser();
    });
  }

  setInterval(async () => {
    if (!sessionState.token) {
      return;
    }
    if (
      document.activeElement &&
      (document.activeElement.closest('[data-page="settings"] form') ||
        document.activeElement.closest("#job-form") ||
        document.activeElement.closest("#host-form"))
    ) {
      return;
    }
    try {
      await refreshDashboard();
    } catch (error) {
      if (String(error.message).includes("session")) {
        logoutUser();
      }
    }
  }, 8000);
}

bootstrap();
