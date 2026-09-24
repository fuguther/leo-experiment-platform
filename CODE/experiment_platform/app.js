const state = { catalog: null, group: "all", status: "all", search: "" };

const blocked = new Set(["declared_unwired", "env_only", "locked_constant", "sealed"]);

function readableDefault(value) {
  if (value === null) return "null";
  if (typeof value === "string") return value === "" ? '""' : value;
  return JSON.stringify(value);
}

function renderTree() {
  const tree = document.querySelector("#group-tree");
  const groups = [{ id: "all", label: "全部参数" }, ...state.catalog.groups];
  tree.innerHTML = groups.map((group, index) => {
    const count = group.id === "all"
      ? state.catalog.parameters.length
      : state.catalog.parameters.filter(p => p.group === group.id).length;
    return `<button class="group-button ${state.group === group.id ? "active" : ""}" data-group="${group.id}">
      <span class="index">${String(index).padStart(2, "0")}</span>
      <span>${group.label}</span><span class="count">${count}</span>
    </button>`;
  }).join("");
  tree.querySelectorAll("button").forEach(button => button.addEventListener("click", () => {
    state.group = button.dataset.group;
    render();
  }));
}

function card(parameter) {
  const statusLabel = blocked.has(parameter.status) ? `blocked · ${parameter.status}` : parameter.status;
  const bounds = [parameter.minimum !== undefined ? `min ${parameter.minimum}` : "", parameter.maximum !== undefined ? `max ${parameter.maximum}` : ""].filter(Boolean);
  return `<article class="parameter-card" data-status="${parameter.status}">
    <p class="path">${parameter.path}</p>
    <h3>${parameter.label}</h3>
    <div class="chips">
      <span class="chip">${parameter.type}</span>
      <span class="chip">${statusLabel}</span>
      ${bounds.map(value => `<span class="chip">${value}</span>`).join("")}
    </div>
    <p class="default"><span>default</span><code>${readableDefault(parameter.default)}</code></p>
    ${parameter.enabled_when ? `<p class="condition">仅在 ${parameter.enabled_when} 时生效</p>` : ""}
    ${parameter.warning ? `<p class="warning">⚠ ${parameter.warning}</p>` : ""}
    ${parameter.agent_guidance ? `<p class="condition">Agent: ${parameter.agent_guidance}</p>` : ""}
    <p class="source">source · ${parameter.source || "unregistered"}</p>
  </article>`;
}

function render() {
  renderTree();
  const group = state.catalog.groups.find(item => item.id === state.group);
  document.querySelector("#active-group-id").textContent = state.group === "all" ? "ALL PARAMETERS" : state.group.toUpperCase();
  document.querySelector("#active-group-label").textContent = group?.label || "全量参数面";
  const query = state.search.trim().toLowerCase();
  const parameters = state.catalog.parameters.filter(parameter => {
    const inGroup = state.group === "all" || parameter.group === state.group;
    const inStatus = state.status === "all" || parameter.status === state.status;
    const haystack = `${parameter.path} ${parameter.label} ${parameter.source || ""} ${parameter.warning || ""}`.toLowerCase();
    return inGroup && inStatus && (!query || haystack.includes(query));
  });
  document.querySelector("#result-count").textContent = `${parameters.length} / ${state.catalog.parameters.length}`;
  document.querySelector("#parameter-grid").innerHTML = parameters.length
    ? parameters.map(card).join("")
    : '<p class="empty">NO PARAMETERS MATCH THIS VIEW.</p>';
}

async function boot() {
  try {
    const response = await fetch("parameter-catalog.json");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.catalog = await response.json();
    document.querySelector("#catalog-count").textContent = `${state.catalog.parameters.length} parameters`;
    document.querySelector("#search").addEventListener("input", event => { state.search = event.target.value; render(); });
    document.querySelector("#status-filter").addEventListener("change", event => { state.status = event.target.value; render(); });
    render();
  } catch (error) {
    document.querySelector("#parameter-grid").innerHTML = `<p class="empty">CATALOG LOAD FAILED · ${error.message}<br>请从项目根目录启动本地 HTTP 服务。</p>`;
  }
}

boot();

