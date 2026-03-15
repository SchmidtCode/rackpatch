const state = {
  token: localStorage.getItem("ops_token") || "",
  selectedJob: null,
  currentPage: "overview",
  installPreviewMode: "compose",
  jobFormKind: null,
  jobOptionValues: {},
  jobLogExpanded: false,
  data: null,
  flashTimer: null,
};

const PAGE_META = {
  overview: { kicker: "Control Plane", title: "Overview" },
  stacks: { kicker: "Compose", title: "Stacks" },
  hosts: { kicker: "Inventory", title: "Hosts" },
  agents: { kicker: "Polling Agents", title: "Agents" },
  jobs: { kicker: "Execution", title: "Jobs" },
  approvals: { kicker: "Change Control", title: "Approvals" },
  schedules: { kicker: "Automation", title: "Schedules" },
  backups: { kicker: "Artifacts", title: "Backups" },
  settings: { kicker: "Configuration", title: "Settings" },
};

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
const overviewRelease = document.getElementById("overview-release");
const automationApi = document.getElementById("automation-api");
const automationLive = document.getElementById("automation-live");
const releaseStatus = document.getElementById("release-status");
const releaseUpdateCommands = document.getElementById("release-update-commands");

const FALLBACK_JOB_KIND = {
  kind: "docker_discover",
  label: "Docker discover",
  mode: "stack_multi",
  target_type: "stack",
  summary: "Select one or more stacks to inspect.",
  defaults: { executor: "worker", window: "all", requires_approval: false },
  default_select_all: true,
  fields: [],
};

function getJobKindItems() {
  return state.data?.jobKinds?.items || state.data?.context?.job_kinds || [];
}

function populateJobKindSelect() {
  const items = getJobKindItems();
  if (!items.length) {
    jobKindSelect.innerHTML = `<option value="${escapeHtml(FALLBACK_JOB_KIND.kind)}">${escapeHtml(FALLBACK_JOB_KIND.label)}</option>`;
    jobKindSelect.disabled = true;
    return;
  }

  const current = state.jobFormKind || jobKindSelect.value;
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

function apiHeaders() {
  return state.token
    ? { Authorization: `Bearer ${state.token}`, "Content-Type": "application/json" }
    : { "Content-Type": "application/json" };
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      ...apiHeaders(),
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(parseApiError(text, response.status));
  }
  return response.json();
}

function parseApiError(text, status) {
  if (!text) {
    return `Request failed (${status})`;
  }
  try {
    const payload = JSON.parse(text);
    if (typeof payload.detail === "string") {
      return payload.detail;
    }
    if (Array.isArray(payload.detail) && payload.detail.length) {
      return payload.detail.map((item) => item.msg || item.detail || "Request failed").join(", ");
    }
  } catch (_) {
    return text;
  }
  return text;
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
  if (["completed", "online", "enabled", "approved", "current"].includes(lower)) {
    return badge(status, "good");
  }
  if (["failed", "offline", "pending_approval", "pending", "outdated"].includes(lower)) {
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
  clearTimeout(state.flashTimer);
  state.flashTimer = window.setTimeout(() => {
    flash.classList.add("hidden");
  }, 3500);
}

function syncPageFromHash() {
  const page = window.location.hash.replace(/^#/, "") || "overview";
  state.currentPage = PAGE_META[page] ? page : "overview";
  applyPageState();
}

function setPage(page) {
  if (!PAGE_META[page]) {
    return;
  }
  state.currentPage = page;
  window.location.hash = page;
  applyPageState();
}

function applyPageState() {
  const meta = PAGE_META[state.currentPage] || PAGE_META.overview;
  pageTitle.textContent = meta.title;
  pageKicker.textContent = meta.kicker;

  document.querySelectorAll("[data-page]").forEach((node) => {
    node.classList.toggle("active", node.dataset.page === state.currentPage);
  });
  document.querySelectorAll("[data-page-link]").forEach((node) => {
    node.classList.toggle("active", node.dataset.pageLink === state.currentPage);
  });
  const activeLink = document.querySelector(`[data-page-link="${state.currentPage}"]`);
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
  const stacks = [...(state.data?.stacks?.items || [])].sort((left, right) => left.name.localeCompare(right.name));
  if (!["stack_multi", "stack_single"].includes(config.mode)) {
    return [];
  }
  return stacks;
}

function getJobHostsForKind(kind) {
  const config = getJobKindConfig(kind);
  const hosts = [...(state.data?.hosts?.items || [])].sort((left, right) => left.name.localeCompare(right.name));
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

function getSelectedJobStacks() {
  return Array.from(jobStackOptions.querySelectorAll('input[type="checkbox"]:checked')).map((input) => input.value);
}

function getSelectedJobHosts() {
  return Array.from(jobHostOptions.querySelectorAll('input[type="checkbox"]:checked')).map((input) => input.value);
}

function getJobOptionDefinitions(kind) {
  return getJobKindConfig(kind).fields || [];
}

function getJobOptionInputId(name) {
  return `job-option-${name}`;
}

function getStoredJobOptionValues(kind) {
  return state.jobOptionValues[kind] || {};
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
  state.jobOptionValues[kind] = getRenderedJobOptionValues(kind);
}

function renderJobOptions(kind, resetOptions = false) {
  const config = getJobKindConfig(kind);
  const fields = getJobOptionDefinitions(kind);
  if (resetOptions) {
    delete state.jobOptionValues[kind];
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
  const selected = getSelectedJobStacks();
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
  const currentSelection = getSelectedJobStacks();
  const shouldSelectAll = !preserveSelection && config.default_select_all;
  const previousSelection = shouldSelectAll ? new Set(stacks.map((stack) => stack.name)) : new Set(currentSelection);

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
  const selected = getSelectedJobHosts();
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
  const previousSelection = preserveSelection ? new Set(getSelectedJobHosts()) : new Set();

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
  state.jobFormKind = kind;
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
  } else {
    jobManualTargetInput.value = "";
  }

  renderJobOptions(kind, resetOptions);
}

function buildJobRequest() {
  const kind = jobKindSelect.value;
  const config = getJobKindConfig(kind);
  const payload = { ...config.defaults, ...getRenderedJobOptionValues(kind) };
  let targetRef = "";

  if (config.mode === "stack_multi") {
    const selectedStacks = getSelectedJobStacks();
    const availableStacks = getJobStacksForKind(kind);
    if (!selectedStacks.length) {
      throw new Error("Select at least one stack.");
    }
    const selectedAll = selectedStacks.length === availableStacks.length;
    targetRef = selectedAll ? "all" : selectedStacks.join(",");
    if (kind === "docker_discover") {
      if (selectedAll) {
        delete payload.stacks;
        payload.window = payload.window || "all";
      } else {
        payload.stacks = selectedStacks;
      }
    }
    if (kind === "docker_update") {
      if (selectedAll) {
        payload.selected_stacks = availableStacks.map((stack) => stack.name);
        payload.window = "all";
      } else {
        payload.selected_stacks = selectedStacks;
      }
    }
  } else if (config.mode === "stack_single") {
    const selectedStacks = getSelectedJobStacks();
    if (selectedStacks.length !== 1) {
      throw new Error("Select exactly one stack.");
    }
    targetRef = selectedStacks[0];
  } else if (config.mode === "host_multi") {
    const selectedHosts = getSelectedJobHosts();
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
    targetRef = jobManualTargetInput.value.trim();
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
  jobLogPanel.classList.toggle("expanded", state.jobLogExpanded);
  jobLogExpandButton.textContent = state.jobLogExpanded ? "Collapse" : "Expand";
}

function canCancelJob(job) {
  return ["queued", "pending_approval"].includes(job.status);
}

function renderInstallPreviews() {
  if (!state.data?.settings) {
    return;
  }
  const blocks = state.data.settings.agent_install || {};
  const helperBlocks = state.data.settings.agent_host_maintenance || {};
  const selected = blocks[state.installPreviewMode] || blocks.compose || "";
  const helper = helperBlocks[state.installPreviewMode] || helperBlocks.compose || "";
  const preview = [
    "# Base agent install",
    selected,
    "",
    "# Optional: enable limited host maintenance",
    helper,
  ]
    .filter(Boolean)
    .join("\n");
  document.getElementById("overview-install").textContent = preview;
  document.getElementById("settings-install").textContent = preview;
  document.querySelectorAll("[data-install-mode]").forEach((node) => {
    node.classList.toggle("active", node.dataset.installMode === state.installPreviewMode);
  });
}

function renderOverview() {
  const overview = state.data.overview;
  const jobs = state.data.jobs.items;
  const approvals = jobs.filter((item) => item.approval_status === "pending");
  const release = state.data.settings.release || {};

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
  const items = state.data.stacks.items;
  renderTable(
    "stacks-table",
    ["Stack", "Host", "Mode", "Risk", "Project Dir", "Actions"],
    items.map((item) => {
      const stackName = escapeHtml(item.name);
      const resolvedHost = item.host === "localhost" && item.guest_host ? item.guest_host : item.host;
      const host = escapeHtml(resolvedHost || "unknown");
      const updateMode = badge(item.update_mode || "manual", "accent");
      const risk = badge(item.risk || "unknown");
      const projectDir = escapeHtml(item.project_dir || item.path || "not set");
      const envCount = (item.compose_env_files || []).length;
      const sourceLabel =
        item.catalog_source === "discovered"
          ? `Discovered from agent${item.agent_status ? ` · ${item.agent_status}` : ""}`
          : "From site overlay";
      return `
        <tr>
          <td>
            <strong>${stackName}</strong>
            <span class="subline">${envCount ? `${envCount} env file(s)` : "No extra env files"}</span>
            <span class="subline">${escapeHtml(sourceLabel)}</span>
          </td>
          <td>${host}</td>
          <td>${updateMode}</td>
          <td>${risk}</td>
          <td><span class="path-pill mono" title="${projectDir}">${projectDir}</span></td>
          <td>
            <div class="table-actions">
              <button class="secondary" data-stack-action="discover" data-stack-name="${stackName}">Discover</button>
              <button class="secondary" data-stack-action="dry-run" data-stack-name="${stackName}">Dry Run</button>
              <button data-stack-action="update" data-stack-name="${stackName}">Live</button>
              <button class="secondary" data-stack-action="rollback" data-stack-name="${stackName}">Rollback</button>
            </div>
          </td>
        </tr>
      `;
    }),
    "No stacks configured."
  );
}

function renderHosts() {
  const items = state.data.hosts.items;
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
        : `${statusBadge(runtime.status || "Worker-routed")}<span class="subline">${escapeHtml(runtime.detail || "Agent optional. Worker and inventory jobs still available.")}</span>`;
      const isProxmoxNode = item.group === "proxmox_nodes";
      const packageCheckAccess = getNamedHostAccess(item, "package_check", "package_check");
      const packagePatchDryAccess = getNamedHostAccess(item, "package_patch_dry_run", "package_patch");
      const packagePatchLiveAccess = getNamedHostAccess(item, "package_patch_live", "package_patch");
      const packageActionNote = packagePatchLiveAccess.eligible
        ? "Package actions limited to approved host-maintenance helper access."
        : packagePatchDryAccess.eligible
          ? "Dry-run helper patching is enabled. Live helper patching is blocked by host policy."
          : packageCheckAccess.eligible
            ? "Package checks are enabled. Package patch access is not enabled on this host."
            : "Package jobs require the limited host-maintenance helper on this host.";
      const actionButtons = isProxmoxNode
        ? `
            <button class="secondary" data-host-kind="proxmox_patch" data-host-name="${hostName}" data-dry-run="true">Patch Dry</button>
            <button data-host-kind="proxmox_patch" data-host-name="${hostName}" data-dry-run="false">Patch Live</button>
            <button class="secondary" data-host-kind="proxmox_reboot" data-host-name="${hostName}" data-dry-run="true">Reboot Dry</button>
            <button data-host-kind="proxmox_reboot" data-host-name="${hostName}" data-dry-run="false">Reboot Live</button>
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
            <button class="secondary" data-host-kind="snapshot" data-host-name="${hostName}" data-dry-run="false">Snapshot</button>
            <span class="subline">${escapeHtml(packageActionNote)}</span>
          `;
      return `
        <tr>
          <td><strong>${hostName}</strong></td>
          <td>${group}</td>
          <td><span class="mono">${address}</span></td>
          <td>${agentCell}</td>
          <td><div class="table-actions">${actionButtons}</div></td>
        </tr>
      `;
    }),
    "No hosts configured."
  );
}

function renderAgents() {
  const items = state.data.agents.items;
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
  const items = state.data.jobs.items;
  renderTable(
    "jobs-table",
    ["Job", "Target", "Execution", "Status", "Created", "Actions"],
    items.map((item) => {
      const config = getJobKindConfig(item.kind);
      const access = config.special_access || null;
      return `
        <tr>
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
            <span class="subline">${escapeHtml(item.target_agent_id || "worker-routed")}</span>
          </td>
          <td>
            <div class="badge-row">
              ${statusBadge(item.status)}
              ${statusBadge(item.approval_status)}
            </div>
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
            </div>
          </td>
        </tr>
      `;
    }),
    "No jobs yet."
  );
}

function renderApprovals() {
  const items = state.data.jobs.items.filter((item) => item.approval_status === "pending");
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
  const items = state.data.schedules.items;
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
  const items = state.data.backups.items;
  renderTable(
    "backups-table",
    ["Kind", "Target", "File", "Size", "Created", "Action"],
    items.map(
      (item) => `
        <tr>
          <td>${badge(item.kind, "accent")}</td>
          <td>${escapeHtml(item.target_ref)}</td>
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
      `
    ),
    "No backup artifacts recorded."
  );
}

function renderSettings() {
  const settings = state.data.settings;
  const context = state.data.context || {};
  siteChip.textContent = settings.site_name;
  document.title = `${settings.ui.app_name} v${settings.ui.app_version}`;

  setInputValue("public-base-url", settings.public.base_url || "");
  setInputValue("public-repo-url", settings.public.repo_url || "");
  setInputValue("public-repo-ref", settings.public.repo_ref || "");
  setInputValue("public-install-script-url", settings.public.install_script_url_override || "");
  setInputValue("public-agent-compose-dir", settings.public.agent_compose_dir || "");
  setInputValue("public-rackpatch-compose-dir", settings.public.rackpatch_compose_dir || "");
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
    "/discover <stack|all>",
    "/update <stack|all> [dry|live]",
    "/patch <host|all> [dry|live]",
    "/snapshot <host>",
    "/proxmox-patch <limit> [dry|live]",
    "/proxmox-reboot <limit> [dry|live]",
    "/backup <volume>",
    "/rollback <stack>",
    "/schedules",
    "/schedule <name-or-id> on|off",
    '/job <kind> <target_type> <target_ref> {"executor":"auto"}',
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
  renderInstallPreviews();
}

function renderAll() {
  renderOverview();
  renderStacks();
  renderHosts();
  renderAgents();
  populateJobKindSelect();
  syncJobForm(jobKindSelect.value, {
    resetOptions: state.jobFormKind !== jobKindSelect.value,
    preserveSelection: state.jobFormKind === jobKindSelect.value,
  });
  syncJobLogPanel();
  renderJobs();
  renderApprovals();
  renderSchedules();
  renderBackups();
  renderSettings();
}

async function loadDashboard() {
  const [overview, agents, hosts, stacks, jobs, schedules, backups, settings, jobKinds, context] = await Promise.all([
    api("/api/v1/overview"),
    api("/api/v1/agents"),
    api("/api/v1/hosts"),
    api("/api/v1/stacks"),
    api("/api/v1/jobs"),
    api("/api/v1/schedules"),
    api("/api/v1/backups"),
    api("/api/v1/settings"),
    api("/api/v1/job-kinds"),
    api("/api/v1/context"),
  ]);
  state.data = { overview, agents, hosts, stacks, jobs, schedules, backups, settings, jobKinds, context };
  renderAll();
}

async function refreshDashboard() {
  await loadDashboard();
  if (state.selectedJob) {
    await selectJob(state.selectedJob, true);
  }
}

async function selectJob(jobId, silent = false) {
  state.selectedJob = jobId;
  const [job, events] = await Promise.all([api(`/api/v1/jobs/${jobId}`), api(`/api/v1/jobs/${jobId}/events`)]);
  const resultMarkup = buildJobResultMarkup(job);
  jobResult.innerHTML = resultMarkup;
  jobEvents.textContent = events.items.map((item) => `[${item.ts}] ${item.message}`).join("\n") || "No events yet.";
  if (!silent && state.currentPage !== "jobs") {
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

function formatQueueResult(result, fallbackKind, fallbackTarget) {
  const kind = result?.kind || fallbackKind;
  const targetRef = result?.target_ref || fallbackTarget;
  const queuedJobs = Array.isArray(result?.jobs) ? result.jobs : [];
  const jobIds = Array.isArray(result?.job_ids) ? result.job_ids : [];
  const skipped = Array.isArray(result?.skipped) ? result.skipped : [];
  if (!result?.fanout) {
    const jobId = result?.id ? shortId(result.id) : "unknown";
    return {
      detail: `Queued job ${jobId}`,
      flash: `Queued ${kind} for ${targetRef}.`,
    };
  }

  const queuedCount = Number(result?.queued_count || queuedJobs.length || jobIds.length || 0);
  const lines = [`Queued ${queuedCount} ${kind} job${queuedCount === 1 ? "" : "s"} for ${targetRef}.`];
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
    flash: `Queued ${queuedCount} ${kind} job${queuedCount === 1 ? "" : "s"}${skipped.length ? `; skipped ${skipped.length}` : ""}.`,
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
  state.token = "";
  state.selectedJob = null;
  state.data = null;
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
    state.token = result.token;
    localStorage.setItem("ops_token", state.token);
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
  state.jobLogExpanded = !state.jobLogExpanded;
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
  const checked = bulkAction.dataset.jobStackBulk === "all";
  jobStackOptions.querySelectorAll('input[type="checkbox"]').forEach((input) => {
    input.checked = checked;
  });
  updateJobStackSelectionState(jobKindSelect.value);
});

jobStackOptions.addEventListener("change", () => {
  updateJobStackSelectionState(jobKindSelect.value);
});

jobKindSelect.addEventListener("change", () => {
  if (state.jobFormKind) {
    storeRenderedJobOptionValues(state.jobFormKind);
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
  const checked = bulkAction.dataset.jobHostBulk === "all";
  jobHostOptions.querySelectorAll('input[type="checkbox"]:not(:disabled)').forEach((input) => {
    input.checked = checked;
  });
  updateJobHostSelectionState(jobKindSelect.value);
});

jobHostOptions.addEventListener("change", () => {
  updateJobHostSelectionState(jobKindSelect.value);
});

jobOptions.addEventListener("change", () => {
  storeRenderedJobOptionValues(jobKindSelect.value);
  if (getJobKindConfig(jobKindSelect.value).mode === "host_multi") {
    renderJobHostOptions(jobKindSelect.value, true);
  }
});

jobOptions.addEventListener("input", () => {
  storeRenderedJobOptionValues(jobKindSelect.value);
  if (getJobKindConfig(jobKindSelect.value).mode === "host_multi") {
    renderJobHostOptions(jobKindSelect.value, true);
  }
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
  if (event.key === "Escape" && state.jobLogExpanded) {
    state.jobLogExpanded = false;
    syncJobLogPanel();
  }
});

document.getElementById("job-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  jobResult.textContent = "";
  try {
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
  } catch (error) {
    jobResult.textContent = error.message;
  }
});

document.getElementById("public-settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await savePublicSettings();
});

document.getElementById("telegram-settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await saveTelegramSettings();
});

document.getElementById("agent-token-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await createAgentToken();
});

appScreen.addEventListener("click", async (event) => {
  const pageLink = event.target.closest("[data-page-link]");
  if (pageLink) {
    setPage(pageLink.dataset.pageLink);
    return;
  }

  const installModeButton = event.target.closest("[data-install-mode]");
  if (installModeButton) {
    state.installPreviewMode = installModeButton.dataset.installMode || "compose";
    renderInstallPreviews();
    return;
  }

  const stackButton = event.target.closest("[data-stack-action]");
  if (stackButton) {
    const stackName = stackButton.dataset.stackName;
    const action = stackButton.dataset.stackAction;
    if (!stackName || !action) {
      return;
    }
    if (action === "discover") {
      await queuePreset("docker_discover", "stack", stackName, {
        executor: "worker",
        window: "all",
        stacks: [stackName],
        requires_approval: false,
      });
      return;
    }
    if (action === "dry-run") {
      await queuePreset("docker_update", "stack", stackName, {
        executor: "auto",
        selected_stacks: [stackName],
        dry_run: true,
        requires_approval: false,
      });
      return;
    }
    if (action === "update") {
      await queuePreset("docker_update", "stack", stackName, {
        executor: "auto",
        selected_stacks: [stackName],
        dry_run: false,
      });
      return;
    }
    if (action === "rollback") {
      await queuePreset("rollback", "stack", stackName, { executor: "worker" });
      return;
    }
  }

  const hostButton = event.target.closest("[data-host-kind]");
  if (hostButton) {
    const hostName = hostButton.dataset.hostName;
    const kind = hostButton.dataset.hostKind;
    const dryRun = hostButton.dataset.dryRun === "true";
    if (!hostName || !kind) {
      return;
    }
    const payload = { executor: kind.startsWith("proxmox") || kind === "snapshot" ? "worker" : "agent" };
    if (kind !== "package_check") {
      payload.dry_run = dryRun;
    }
    if (dryRun || kind === "package_check" || kind === "snapshot") {
      payload.requires_approval = false;
    }
    if (kind === "package_check") {
      payload.hosts = [hostName];
    }
    if (kind.startsWith("proxmox")) {
      payload.limit = hostName;
    }
    await queuePreset(kind, "host", hostName, payload);
    return;
  }

  const jobLogButton = event.target.closest("[data-job-log]");
  if (jobLogButton) {
    await selectJob(jobLogButton.dataset.jobLog);
    return;
  }

  const approveButton = event.target.closest("[data-job-approve]");
  if (approveButton) {
    await approveJob(approveButton.dataset.jobApprove);
    return;
  }

  const cancelButton = event.target.closest("[data-job-cancel]");
  if (cancelButton) {
    await cancelJob(cancelButton.dataset.jobCancel);
    return;
  }

  const scheduleButton = event.target.closest("[data-schedule-id]");
  if (scheduleButton) {
    await toggleSchedule(scheduleButton.dataset.scheduleId, scheduleButton.dataset.scheduleEnabled === "true");
    return;
  }

  const backupDeleteButton = event.target.closest("[data-backup-delete]");
  if (backupDeleteButton) {
    await deleteBackup(
      backupDeleteButton.dataset.backupDelete,
      backupDeleteButton.dataset.backupDeleteSupported === "true"
    );
  }
});

window.addEventListener("hashchange", syncPageFromHash);

if (state.token) {
  loginScreen.classList.add("hidden");
  appScreen.classList.remove("hidden");
  syncPageFromHash();
  refreshDashboard().catch((error) => {
    loginError.textContent = error.message;
    logoutUser();
  });
}

setInterval(async () => {
  if (!state.token) {
    return;
  }
  if (
    document.activeElement &&
    (document.activeElement.closest('[data-page="settings"] form') ||
      document.activeElement.closest("#job-form"))
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

syncPageFromHash();
