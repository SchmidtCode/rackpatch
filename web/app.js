const state = {
  token: localStorage.getItem("ops_token") || "",
  selectedJob: null,
};

const loginScreen = document.getElementById("login-screen");
const appScreen = document.getElementById("app-screen");
const loginForm = document.getElementById("login-form");
const loginError = document.getElementById("login-error");
const jobResult = document.getElementById("job-result");
const jobEvents = document.getElementById("job-events");
const stacksTable = document.getElementById("stacks");

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

function renderCardGrid(overview) {
  const root = document.getElementById("overview");
  const cards = [
    ["Agents", overview.counts.agents],
    ["Hosts", overview.hosts],
    ["Stacks", overview.stacks],
    ["Jobs", overview.counts.jobs],
    ["Running", overview.counts.running_jobs],
    ["Approvals", overview.counts.pending_approvals],
  ];
  root.innerHTML = cards
    .map(
      ([label, value]) => `
        <article class="stat-card">
          <div class="stat-label">${label}</div>
          <div class="stat-value">${value}</div>
        </article>
      `
    )
    .join("");
}

function renderList(id, items, renderer) {
  const root = document.getElementById(id);
  if (!items.length) {
    root.innerHTML = `<div class="empty">Nothing here yet.</div>`;
    return;
  }
  root.innerHTML = items.map(renderer).join("");
}

function renderStacksTable(items) {
  if (!items.length) {
    stacksTable.innerHTML = `<div class="empty">Nothing here yet.</div>`;
    return;
  }

  const rows = items
    .map((item) => {
      const stackName = escapeHtml(item.name);
      const host = escapeHtml(item.host || "unknown");
      const updateMode = escapeHtml(item.update_mode || "manual");
      const risk = escapeHtml(item.risk || "unknown");
      const projectDir = escapeHtml(item.project_dir || item.path || "not set");
      const composeEnvFiles = item.compose_env_files || [];
      return `
        <tr>
          <td>
            <strong>${stackName}</strong>
            <span class="stack-subline">${composeEnvFiles.length ? `${composeEnvFiles.length} env file(s)` : "No extra env files"}</span>
          </td>
          <td>${host}</td>
          <td>
            <div class="badge-row">
              <span class="badge accent">${updateMode}</span>
            </div>
          </td>
          <td>
            <div class="badge-row">
              <span class="badge">${risk}</span>
            </div>
          </td>
          <td><span class="stack-path" title="${projectDir}">${projectDir}</span></td>
          <td>
            <div class="table-actions">
              <button type="button" data-stack-action="discover" data-stack-name="${stackName}">Discover</button>
              <button type="button" data-stack-action="dry-run" data-stack-name="${stackName}">Dry Run</button>
              <button type="button" data-stack-action="rollback" data-stack-name="${stackName}">Rollback</button>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");

  stacksTable.innerHTML = `
    <table class="data-table">
      <thead>
        <tr>
          <th>Stack</th>
          <th>Host</th>
          <th>Mode</th>
          <th>Risk</th>
          <th>Project Dir</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
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

  renderCardGrid(overview);

  document.getElementById("install-commands").textContent =
    `${settings.agent_install.container}\n\n${settings.agent_install.systemd}`;

  renderList(
    "agents",
    agents.items,
    (item) => `
      <article class="list-item">
        <div>
          <strong>${item.display_name}</strong>
          <span>${item.name}</span>
        </div>
        <div class="meta">${(item.capabilities || []).join(", ") || "no capabilities"}</div>
      </article>
    `
  );

  renderList(
    "hosts",
    hosts.items,
    (item) => `
      <article class="list-item">
        <div>
          <strong>${item.name}</strong>
          <span>${item.group || "host"}</span>
        </div>
        <div class="meta">${item.agent ? "agent enrolled" : "no agent"}${item.ansible_host ? ` · ${item.ansible_host}` : ""}</div>
      </article>
    `
  );

  renderStacksTable(stacks.items);

  const approvalItems = jobs.items.filter((item) => item.approval_status === "pending");
  renderList(
    "approvals",
    approvalItems,
    (item) => `
      <article class="list-item action-row">
        <div>
          <strong>${item.kind}</strong>
          <span>${item.target_ref}</span>
        </div>
        <div class="meta">
          <button onclick="approveJob('${item.id}')">Approve</button>
        </div>
      </article>
    `
  );

  renderList(
    "schedules",
    schedules.items,
    (item) => `
      <article class="list-item action-row">
        <div>
          <strong>${item.name}</strong>
          <span>${item.cron_expr}</span>
        </div>
        <div class="meta">
          <span>${item.enabled ? "enabled" : "disabled"}</span>
          <button onclick="toggleSchedule('${item.id}', ${!item.enabled})">${item.enabled ? "Disable" : "Enable"}</button>
        </div>
      </article>
    `
  );

  renderList(
    "jobs",
    jobs.items,
    (item) => `
      <article class="list-item action-row">
        <div>
          <strong>${item.kind}</strong>
          <span>${item.target_ref} · ${item.status}</span>
        </div>
        <div class="meta">
          <button onclick="selectJob('${item.id}')">Logs</button>
        </div>
      </article>
    `
  );

  renderList(
    "backups",
    backups.items,
    (item) => `
      <article class="list-item">
        <div>
          <strong>${item.kind}</strong>
          <span>${item.target_ref}</span>
        </div>
        <div class="meta">${item.path}</div>
      </article>
    `
  );
}

async function selectJob(jobId) {
  state.selectedJob = jobId;
  const events = await api(`/api/v1/jobs/${jobId}/events`);
  jobEvents.textContent = events.items.map((item) => `[${item.ts}] ${item.message}`).join("\n") || "No events yet.";
}

async function approveJob(jobId) {
  await api(`/api/v1/jobs/${jobId}/approve`, { method: "POST" });
  await loadDashboard();
}

async function toggleSchedule(scheduleId, enabled) {
  await api(`/api/v1/schedules/${scheduleId}/toggle`, {
    method: "POST",
    body: JSON.stringify({ enabled }),
  });
  await loadDashboard();
}

async function queuePreset(kind, targetType, targetRef, payload) {
  await api("/api/v1/jobs", {
    method: "POST",
    body: JSON.stringify({ kind, target_type: targetType, target_ref: targetRef, payload }),
  });
  await loadDashboard();
}

window.queuePreset = queuePreset;
window.approveJob = approveJob;
window.toggleSchedule = toggleSchedule;
window.selectJob = selectJob;

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
    await loadDashboard();
  } catch (error) {
    loginError.textContent =
      error.message === "invalid credentials" ? "Invalid username or password." : error.message;
  }
});

document.getElementById("refresh-all").addEventListener("click", async () => {
  await loadDashboard();
  if (state.selectedJob) {
    await selectJob(state.selectedJob);
  }
});

document.getElementById("logout").addEventListener("click", () => {
  localStorage.removeItem("ops_token");
  state.token = "";
  state.selectedJob = null;
  appScreen.classList.add("hidden");
  loginScreen.classList.remove("hidden");
});

stacksTable.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-stack-action]");
  if (!button) {
    return;
  }

  const stackName = button.dataset.stackName;
  const action = button.dataset.stackAction;
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
    });
    return;
  }

  if (action === "rollback") {
    await queuePreset("rollback", "stack", stackName, {
      executor: "worker",
    });
  }
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
    await loadDashboard();
  } catch (error) {
    jobResult.textContent = error.message;
  }
});

if (state.token) {
  loginScreen.classList.add("hidden");
  appScreen.classList.remove("hidden");
  loadDashboard().catch((error) => {
    loginError.textContent = error.message;
  });
}

setInterval(async () => {
  if (!state.token) {
    return;
  }
  await loadDashboard();
  if (state.selectedJob) {
    await selectJob(state.selectedJob);
  }
}, 5000);
