const state = {
  token: localStorage.getItem("ops_token") || "",
  selectedJob: null,
  currentPage: "overview",
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
  if (["completed", "online", "enabled", "approved"].includes(lower)) {
    return badge(status, "good");
  }
  if (["failed", "offline", "pending_approval", "pending"].includes(lower)) {
    return badge(status, "warn");
  }
  return badge(status, "accent");
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
}

function setInputValue(id, value) {
  const element = document.getElementById(id);
  if (document.activeElement === element) {
    return;
  }
  element.value = value;
}

function renderOverview() {
  const overview = state.data.overview;
  const jobs = state.data.jobs.items;
  const approvals = jobs.filter((item) => item.approval_status === "pending");
  const settings = state.data.settings;

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

  document.getElementById("overview-install").textContent =
    `${settings.agent_install.container}\n\n${settings.agent_install.systemd}`;
}

function renderStacks() {
  const items = state.data.stacks.items;
  renderTable(
    "stacks-table",
    ["Stack", "Host", "Mode", "Risk", "Project Dir", "Actions"],
    items.map((item) => {
      const stackName = escapeHtml(item.name);
      const host = escapeHtml(item.host || "unknown");
      const updateMode = badge(item.update_mode || "manual", "accent");
      const risk = badge(item.risk || "unknown");
      const projectDir = escapeHtml(item.project_dir || item.path || "not set");
      const envCount = (item.compose_env_files || []).length;
      return `
        <tr>
          <td>
            <strong>${stackName}</strong>
            <span class="subline">${envCount ? `${envCount} env file(s)` : "No extra env files"}</span>
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
      const agentCell = agent
        ? `${statusBadge(agent.status)}<span class="subline mono">${escapeHtml(agent.display_name || agent.name)}</span>`
        : `<span class="subline">No agent enrolled</span>`;
      const isProxmoxNode = item.group === "proxmox_nodes";
      const actionButtons = isProxmoxNode
        ? `
            <button class="secondary" data-host-kind="proxmox_patch" data-host-name="${hostName}" data-dry-run="true">Patch Dry</button>
            <button data-host-kind="proxmox_patch" data-host-name="${hostName}" data-dry-run="false">Patch Live</button>
            <button class="secondary" data-host-kind="proxmox_reboot" data-host-name="${hostName}" data-dry-run="true">Reboot Dry</button>
            <button data-host-kind="proxmox_reboot" data-host-name="${hostName}" data-dry-run="false">Reboot Live</button>
          `
        : `
            <button class="secondary" data-host-kind="package_check" data-host-name="${hostName}" data-dry-run="true">Check</button>
            <button class="secondary" data-host-kind="package_patch" data-host-name="${hostName}" data-dry-run="true">Patch Dry</button>
            <button data-host-kind="package_patch" data-host-name="${hostName}" data-dry-run="false">Patch Live</button>
            <button class="secondary" data-host-kind="snapshot" data-host-name="${hostName}" data-dry-run="false">Snapshot</button>
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
    ["Agent", "Transport", "Platform", "Capabilities", "Last Seen"],
    items.map(
      (item) => `
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
          <td>${escapeHtml((item.capabilities || []).join(", ") || "none")}</td>
          <td>${escapeHtml(formatTimestamp(item.last_seen_at))}</td>
        </tr>
      `
    ),
    "No agents registered."
  );
}

function renderJobs() {
  const items = state.data.jobs.items;
  renderTable(
    "jobs-table",
    ["Job", "Target", "Execution", "Status", "Created", "Actions"],
    items.map(
      (item) => `
        <tr>
          <td>
            <strong>${escapeHtml(item.kind)}</strong>
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
            </div>
          </td>
        </tr>
      `
    ),
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
    ["Kind", "Target", "Path", "Created"],
    items.map(
      (item) => `
        <tr>
          <td>${badge(item.kind, "accent")}</td>
          <td>${escapeHtml(item.target_ref)}</td>
          <td><span class="path-pill mono" title="${escapeHtml(item.path)}">${escapeHtml(item.path)}</span></td>
          <td>${escapeHtml(formatTimestamp(item.created_at))}</td>
        </tr>
      `
    ),
    "No backup artifacts recorded."
  );
}

function renderSettings() {
  const settings = state.data.settings;
  siteChip.textContent = settings.site_name;

  setInputValue("public-base-url", settings.public.base_url || "");
  setInputValue("public-repo-url", settings.public.repo_url || "");
  setInputValue("public-repo-ref", settings.public.repo_ref || "");
  setInputValue("public-install-script-url", settings.public.install_script_url_override || "");
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

  document.getElementById("settings-install").textContent =
    `${settings.agent_install.container}\n\n${settings.agent_install.systemd}`;
  document.getElementById("settings-paths").textContent = [
    `Site: ${settings.site_name}`,
    `Site root: ${settings.site_root}`,
    `Inventory: ${settings.inventory_path}`,
    `Stacks: ${settings.stacks_path}`,
    `Maintenance: ${settings.maintenance_path}`,
    `Public repo: ${settings.public.repo_url}@${settings.public.repo_ref}`,
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
}

function renderAll() {
  renderOverview();
  renderStacks();
  renderHosts();
  renderAgents();
  renderJobs();
  renderApprovals();
  renderSchedules();
  renderBackups();
  renderSettings();
}

async function loadDashboard() {
  const [overview, agents, hosts, stacks, jobs, schedules, backups, settings] = await Promise.all([
    api("/api/v1/overview"),
    api("/api/v1/agents"),
    api("/api/v1/hosts"),
    api("/api/v1/stacks"),
    api("/api/v1/jobs"),
    api("/api/v1/schedules"),
    api("/api/v1/backups"),
    api("/api/v1/settings"),
  ]);
  state.data = { overview, agents, hosts, stacks, jobs, schedules, backups, settings };
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
  const events = await api(`/api/v1/jobs/${jobId}/events`);
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

async function toggleSchedule(scheduleId, enabled) {
  await api(`/api/v1/schedules/${scheduleId}/toggle`, {
    method: "POST",
    body: JSON.stringify({ enabled }),
  });
  showFlash(`${enabled ? "Enabled" : "Disabled"} schedule ${shortId(scheduleId)}.`);
  await refreshDashboard();
}

async function queuePreset(kind, targetType, targetRef, payload) {
  const result = await api("/api/v1/jobs", {
    method: "POST",
    body: JSON.stringify({ kind, target_type: targetType, target_ref: targetRef, payload }),
  });
  showFlash(`Queued ${kind} for ${targetRef}.`);
  await refreshDashboard();
  return result;
}

async function savePublicSettings() {
  const result = await api("/api/v1/settings/public", {
    method: "POST",
    body: JSON.stringify({
      base_url: document.getElementById("public-base-url").value,
      repo_url: document.getElementById("public-repo-url").value,
      repo_ref: document.getElementById("public-repo-ref").value,
      install_script_url: document.getElementById("public-install-script-url").value,
    }),
  });
  state.data.settings = result;
  renderSettings();
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
  const result = await api("/api/v1/settings/telegram", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  state.data.settings = result;
  renderSettings();
  document.getElementById("telegram-settings-result").textContent = "Saved Telegram settings.";
  showFlash("Saved Telegram settings.");
}

async function createAgentToken() {
  const label = document.getElementById("agent-token-label").value || "manual-token";
  const result = await api("/api/v1/settings/agent-tokens", {
    method: "POST",
    body: JSON.stringify({ label }),
  });
  const settings = state.data.settings;
  document.getElementById("agent-token-result").textContent = [
    `Label: ${result.label}`,
    `Token: ${result.token}`,
    "",
    `Container install:`,
    `curl -fsSL ${settings.public.install_script_url} | sh -s -- --server-url ${settings.public.base_url} --bootstrap-token ${result.token} --mode container --install-source ${settings.public.repo_url}`,
    "",
    `Systemd install:`,
    `curl -fsSL ${settings.public.install_script_url} | sh -s -- --server-url ${settings.public.base_url} --bootstrap-token ${result.token} --mode systemd --install-source ${settings.public.repo_url}`,
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

document.getElementById("job-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  jobResult.textContent = "";
  try {
    const payload = JSON.parse(document.getElementById("job-payload").value);
    const result = await api("/api/v1/jobs", {
      method: "POST",
      body: JSON.stringify({
        kind: document.getElementById("job-kind").value,
        target_type: document.getElementById("target-type").value,
        target_ref: document.getElementById("target-ref").value,
        payload,
      }),
    });
    jobResult.textContent = `Queued job ${result.id}`;
    showFlash(`Queued ${result.kind} for ${result.target_ref}.`);
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
    const payload = { executor: kind.startsWith("proxmox") || kind === "snapshot" ? "worker" : "auto" };
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

  const scheduleButton = event.target.closest("[data-schedule-id]");
  if (scheduleButton) {
    await toggleSchedule(scheduleButton.dataset.scheduleId, scheduleButton.dataset.scheduleEnabled === "true");
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
  if (document.activeElement && document.activeElement.closest('[data-page="settings"] form')) {
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
