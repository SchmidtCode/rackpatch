export const PAGE_META = {
  overview: { kicker: "Control Plane", title: "Overview" },
  stacks: { kicker: "Compose", title: "Stacks" },
  history: { kicker: "Version Log", title: "Update History" },
  hosts: { kicker: "Inventory", title: "Hosts" },
  agents: { kicker: "Polling Agents", title: "Agents" },
  jobs: { kicker: "Execution", title: "Jobs" },
  approvals: { kicker: "Change Control", title: "Approvals" },
  schedules: { kicker: "Automation", title: "Schedules" },
  backups: { kicker: "Artifacts", title: "Backups" },
  settings: { kicker: "Configuration", title: "Settings" },
};

export const EMPTY_DOCKER_UPDATES = {
  summary: {
    total_stacks: 0,
    checkable_stacks: 0,
    checked_stacks: 0,
    outdated_stacks: 0,
    outdated_images: 0,
    selectable_stacks: 0,
    blocked_live_updates: 0,
    running_checks: 0,
    failed_checks: 0,
    running_updates: 0,
  },
  items: [],
};

export const EMPTY_DOCKER_HISTORY = {
  summary: {
    total_rows: 0,
    total_jobs: 0,
    total_stacks: 0,
    total_components: 0,
    manual_rows: 0,
    automation_rows: 0,
    last_updated_at: null,
  },
  items: [],
  loaded: false,
  error: "",
};

export function createState() {
  return {
    session: {
      token: localStorage.getItem("ops_token") || "",
    },
    ui: {
      selectedJob: null,
      currentPage: "overview",
      installPreviewMode: "compose",
      jobLogExpanded: false,
      expandedStacks: {},
      stackUpdateRequiresApproval: false,
      dockerHistoryFilters: {
        updated_at: "",
        stack: "",
        component: "",
        host: "",
        image: "",
        from_version: "",
        to_version: "",
        mode: "",
        source: "",
        requested_by: "",
      },
      flashTimer: null,
    },
    jobForm: {
      kind: null,
      optionValues: {},
      selectedStacks: [],
      selectedHosts: [],
      manualTarget: "",
    },
    selection: {
      jobs: [],
      stacks: [],
      backups: [],
    },
    entities: {
      overview: null,
      agents: { items: [] },
      hosts: { items: [] },
      stacks: { items: [] },
      dockerUpdates: { ...EMPTY_DOCKER_UPDATES },
      dockerHistory: { ...EMPTY_DOCKER_HISTORY },
      jobs: { items: [] },
      schedules: { items: [] },
      backups: { items: [] },
      settings: null,
      jobKinds: { items: [] },
      context: {},
    },
  };
}

export function normalizeSelection(values, allowedValues) {
  const allowed = allowedValues instanceof Set ? allowedValues : new Set(allowedValues);
  return [...new Set((values || []).filter((value) => allowed.has(value)))];
}
