/* ==========================================================================
   Green LLM Bench — MTA-Themed Rendering & Interactivity
   Models = Subway Lines | Configs = Stations | Metrics = Departures
   Requires: data.js (BENCHMARK_DATA, CARBON_PRESETS), Chart.js
   ========================================================================== */

// ─── Cyberpunk Color Palette ───────────────────────────────────────────────────
const MTA = {
  red: "#E03030", green: "#5DB800", yellow: "#D4860A", blue: "#00C8A0",
  orange: "#C8470A", purple: "#9B59B6", gray: "#444444", lime: "#76FF03",
  black: "#080808", white: "#E8E8E8"
};

// Model color mapping
const MODEL_LINES = {
  "Qwen3.5-0.8B": { bg: MTA.green,  border: "#3A7A00", circle: "green",  letter: "1", label: "Model 1 (0.8B)" },
  "Qwen3.5-4B":   { bg: MTA.yellow, border: "#9A6000", circle: "yellow", letter: "2", label: "Model 2 (4B)" },
  "Qwen3.5-9B":   { bg: MTA.red,    border: "#A02020", circle: "red",    letter: "3", label: "Model 3 (9B)" }
};

function getModelLine(modelName) {
  const short = shortModel(modelName);
  return MODEL_LINES[short] || { bg: MTA.gray, border: "#666", circle: "gray", letter: "?", label: short };
}

// ─── Animation Helpers ──────────────────────────────────────────────────────────
function animateCountUp(el, target, duration = 600, suffix = "") {
  const startTime = performance.now();
  function tick(now) {
    const progress = Math.min((now - startTime) / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    const current = target * eased;
    el.textContent = (Number.isInteger(target) ? Math.round(current) : current.toFixed(target < 10 ? 2 : 1)) + suffix;
    if (progress < 1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

function staggerRows(container, selector, delayStep = 25) {
  container.querySelectorAll(selector).forEach((row, i) => {
    row.classList.add("mta-stagger");
    row.style.animationDelay = `${i * delayStep}ms`;
  });
}

function flashElement(el) {
  el.classList.remove("mta-flash");
  void el.offsetWidth;
  el.classList.add("mta-flash");
}

// ─── Helpers ────────────────────────────────────────────────────────────────────
function shortModel(name) { return name.split("/").pop(); }

function fmtNum(v, digits = 2) {
  if (v == null) return "--";
  if (Math.abs(v) < 0.001 && v !== 0) return v.toExponential(digits);
  return v.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: digits });
}

function completedData() { return BENCHMARK_DATA.filter(d => d.status === "completed"); }

function quantLabel(q) { return q === "none" ? "FP16" : q.toUpperCase(); }

function sciScoreClass(sci) {
  const ug = sci * 1e6;
  return ug <= 60 ? "score--good" : ug <= 200 ? "score--ok" : "score--bad";
}
function energyScoreClass(j) { return j <= 0.3 ? "score--good" : j <= 1.0 ? "score--ok" : "score--bad"; }

// Model circle badge HTML
function modelBadge(modelName, size = "sm") {
  const ml = getModelLine(modelName);
  return `<span class="mta-circle mta-circle--${size} mta-circle--${ml.circle}">${ml.letter}</span>`;
}

// ─── Navigation ─────────────────────────────────────────────────────────────────
const pages = {
  dashboard: document.getElementById("page-dashboard"),
  leaderboards: document.getElementById("page-leaderboards"),
  calculator: document.getElementById("page-calculator"),
  methodology: document.getElementById("page-methodology")
};
const pageInitialized = { dashboard: false, leaderboards: false, calculator: false, methodology: true };

function navigate(page) {
  Object.values(pages).forEach(p => p.classList.remove("is-active"));
  if (pages[page]) pages[page].classList.add("is-active");
  document.querySelectorAll(".mta-nav__link").forEach(link => {
    const isActive = link.dataset.nav === page;
    link.classList.toggle("is-active", isActive);
    isActive ? link.setAttribute("aria-current", "page") : link.removeAttribute("aria-current");
  });
  if (!pageInitialized[page]) { pageInitialized[page] = true; if (page === "calculator") initCalculator(); }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

document.querySelectorAll("[data-nav]").forEach(el => {
  el.addEventListener("click", e => {
    e.preventDefault(); navigate(el.dataset.nav);
    document.getElementById("nav-links").classList.remove("is-open");
    document.getElementById("nav-toggle").setAttribute("aria-expanded", "false");
  });
});

const navToggle = document.getElementById("nav-toggle");
navToggle.addEventListener("click", () => {
  const links = document.getElementById("nav-links");
  const isOpen = links.classList.toggle("is-open");
  navToggle.setAttribute("aria-expanded", String(isOpen));
});

// ─── Stats Bar ──────────────────────────────────────────────────────────────────
function renderStatsBar() {
  const data = completedData();
  const bestSCI = Math.min(...data.map(d => d.metrics.sci_per_token));
  const bestEff = Math.max(...data.map(d => d.metrics.gpu_efficiency));
  const stats = [
    { label: "Experiments", value: 6226, unit: "", cls: "count", target: 6226 },
    { label: "Best SCI", value: (bestSCI * 1e6).toFixed(1), unit: "µgCO₂/tok", cls: "sci", target: bestSCI * 1e6 },
    { label: "Best Efficiency", value: bestEff.toFixed(2), unit: "tok/J", cls: "eff", target: bestEff },
    { label: "Grid Region", value: "CT", unit: "ISO-NE", cls: "region" }
  ];
  const bar = document.getElementById("stats-bar");
  bar.innerHTML = stats.map(s => `
    <div class="mta-stat mta-stat--${s.cls}">
      <div class="mta-stat__label">${s.label}</div>
      <div><span class="mta-stat__value" data-target="${s.target || ""}">${s.value}</span>
      <span class="mta-stat__unit">${s.unit}</span></div>
    </div>`).join("");
  bar.querySelectorAll(".mta-stat__value[data-target]").forEach(el => {
    const t = parseFloat(el.dataset.target);
    if (!isNaN(t) && t > 0) animateCountUp(el, t, 800);
  });
}

// ─── Preview Table ──────────────────────────────────────────────────────────────
function renderPreviewTable() {
  const data = completedData().sort((a, b) => a.metrics.sci_per_token - b.metrics.sci_per_token).slice(0, 5);
  document.getElementById("preview-table-body").innerHTML = data.map((d, i) => {
    const c = d.config, m = d.metrics, pareto = d.pareto_rank === 0;
    return `<tr class="--clickable ${pareto ? "--pareto" : ""}" data-hash="${d.config_hash}" tabindex="0">
      <td class="--rank">${i + 1}</td>
      <td><span class="flex flex--center flex--gap-8">${modelBadge(c.model_name)} <strong>${shortModel(c.model_name)}</strong></span></td>
      <td><span class="mta-tag mta-tag--blue mta-tag--sm">${quantLabel(c.quantization)}</span></td>
      <td class="--right">${c.batch_size}</td>
      <td class="--right --mono ${sciScoreClass(m.sci_per_token)}">${(m.sci_per_token * 1e6).toFixed(2)}µg</td>
      <td class="--right --mono">${m.val_bpb.toFixed(3)}</td>
      <td class="--right --mono">${m.tokens_per_sec.toFixed(0)}</td>
      <td class="--center">${pareto ? '<span class="mta-badge mta-badge--pareto">Pareto</span>' : ""}</td>
    </tr>`;
  }).join("");
  staggerRows(document.getElementById("preview-table-body"), "tr");
}

// Table click delegation
["preview-table-body", "leaderboard-tbody"].forEach(id => {
  document.getElementById(id).addEventListener("click", e => {
    const row = e.target.closest("tr.--clickable");
    if (row) { const exp = BENCHMARK_DATA.find(d => d.config_hash === row.dataset.hash); if (exp) openDetailModal(exp); }
  });
  document.getElementById(id).addEventListener("keydown", e => {
    if ((e.key === "Enter" || e.key === " ") && e.target.closest("tr.--clickable")) {
      e.preventDefault();
      const exp = BENCHMARK_DATA.find(d => d.config_hash === e.target.closest("tr").dataset.hash);
      if (exp) openDetailModal(exp);
    }
  });
});

// ─── Leaderboard Definitions ────────────────────────────────────────────────────
const BOARDS = {
  greenest:  { title: "Greenest", sortKey: "sci_per_token", dir: "asc", cols: [
    { key: "rank", label: "#", align: "center" }, { key: "model", label: "Model" }, { key: "quant", label: "Quant" },
    { key: "batch", label: "Batch", align: "right" },
    { key: "sci_per_token", label: "SCI (µg)", align: "right", fmt: v => (v*1e6).toFixed(2), color: sciScoreClass },
    { key: "carbon_operational_g", label: "Op (g)", align: "right", fmt: v => v.toExponential(2) },
    { key: "carbon_embodied_g", label: "Emb (g)", align: "right", fmt: v => v.toExponential(2) },
    { key: "val_bpb", label: "BPB", align: "right", fmt: v => v.toFixed(3) },
    { key: "tokens_per_sec", label: "tok/s", align: "right", fmt: v => v.toFixed(0) },
    { key: "pareto", label: "Pareto", align: "center" }
  ]},
  efficient: { title: "Most Efficient", sortKey: "energy_per_token_j", dir: "asc", cols: [
    { key: "rank", label: "#", align: "center" }, { key: "model", label: "Model" }, { key: "quant", label: "Quant" },
    { key: "batch", label: "Batch", align: "right" },
    { key: "energy_per_token_j", label: "J/tok", align: "right", fmt: v => v.toFixed(3), color: energyScoreClass },
    { key: "gpu_power_avg_w", label: "Watts", align: "right", fmt: v => v.toFixed(1) },
    { key: "gpu_util_avg_pct", label: "GPU%", align: "right", fmt: v => v.toFixed(1)+"%" },
    { key: "tokens_per_sec", label: "tok/s", align: "right", fmt: v => v.toFixed(0) },
    { key: "val_bpb", label: "BPB", align: "right", fmt: v => v.toFixed(3) },
    { key: "pareto", label: "Pareto", align: "center" }
  ]},
  perfwatt:  { title: "Perf/Watt", sortKey: "gpu_efficiency", dir: "desc", cols: [
    { key: "rank", label: "#", align: "center" }, { key: "model", label: "Model" }, { key: "quant", label: "Quant" },
    { key: "batch", label: "Batch", align: "right" },
    { key: "gpu_efficiency", label: "tok/J", align: "right", fmt: v => v.toFixed(3), color: v => v>5?"score--good":v>1?"score--ok":"score--bad" },
    { key: "tokens_per_sec", label: "tok/s", align: "right", fmt: v => v.toFixed(0) },
    { key: "gpu_power_avg_w", label: "Watts", align: "right", fmt: v => v.toFixed(1) },
    { key: "val_bpb", label: "BPB", align: "right", fmt: v => v.toFixed(3) },
    { key: "pareto", label: "Pareto", align: "center" }
  ]},
  quality:   { title: "Best Quality", sortKey: "val_bpb", dir: "asc", cols: [
    { key: "rank", label: "#", align: "center" }, { key: "model", label: "Model" }, { key: "quant", label: "Quant" },
    { key: "batch", label: "Batch", align: "right" },
    { key: "val_bpb", label: "BPB", align: "right", fmt: v => v.toFixed(3), color: v => v<=1.5?"score--good":v<=2?"score--ok":"score--bad" },
    { key: "sci_per_token", label: "SCI (µg)", align: "right", fmt: v => (v*1e6).toFixed(2) },
    { key: "tokens_per_sec", label: "tok/s", align: "right", fmt: v => v.toFixed(0) },
    { key: "mem_used_gb", label: "Mem GB", align: "right", fmt: v => v.toFixed(1) },
    { key: "pareto", label: "Pareto", align: "center" }
  ]},
  fastest:   { title: "Fastest", sortKey: "tokens_per_sec", dir: "desc", cols: [
    { key: "rank", label: "#", align: "center" }, { key: "model", label: "Model" }, { key: "quant", label: "Quant" },
    { key: "batch", label: "Batch", align: "right" },
    { key: "tokens_per_sec", label: "tok/s", align: "right", fmt: v => v.toFixed(0), color: v => v>100?"score--good":v>30?"score--ok":"score--bad" },
    { key: "latency_p50_ms", label: "P50 ms", align: "right", fmt: v => v.toFixed(1) },
    { key: "gpu_power_avg_w", label: "Watts", align: "right", fmt: v => v.toFixed(1) },
    { key: "val_bpb", label: "BPB", align: "right", fmt: v => v.toFixed(3) },
    { key: "pareto", label: "Pareto", align: "center" }
  ]},
  cheapest:  { title: "Cheapest", sortKey: "cost_per_token_usd", dir: "asc", cols: [
    { key: "rank", label: "#", align: "center" }, { key: "model", label: "Model" }, { key: "quant", label: "Quant" },
    { key: "batch", label: "Batch", align: "right" },
    { key: "cost_per_token_usd", label: "$/1M tok", align: "right", fmt: v => "$"+(v*1e6).toFixed(4), color: v => v*1e6<.005?"score--good":v*1e6<.05?"score--ok":"score--bad" },
    { key: "energy_kwh_per_token", label: "kWh/tok", align: "right", fmt: v => v.toExponential(2) },
    { key: "sci_per_token", label: "SCI (µg)", align: "right", fmt: v => (v*1e6).toFixed(2) },
    { key: "val_bpb", label: "BPB", align: "right", fmt: v => v.toFixed(3) },
    { key: "pareto", label: "Pareto", align: "center" }
  ]}
};

let currentBoard = "greenest";
let currentSort = { key: "sci_per_token", dir: "asc" };

function getFilteredData() {
  let data = completedData();
  const mf = document.getElementById("filter-model").value;
  const qf = document.getElementById("filter-quant").value;
  const bf = document.getElementById("filter-batch").value;
  if (mf !== "all") data = data.filter(d => shortModel(d.config.model_name) === mf);
  if (qf !== "all") data = data.filter(d => d.config.quantization === qf);
  if (bf !== "all") data = data.filter(d => String(d.config.batch_size) === bf);
  return data;
}

function cellVal(d, key) {
  if (key === "rank") return 0;
  if (key === "model") return shortModel(d.config.model_name);
  if (key === "quant") return d.config.quantization;
  if (key === "batch") return d.config.batch_size;
  if (key === "pareto") return d.pareto_rank === 0 ? 0 : 1;
  return d.metrics[key] ?? 0;
}

// ─── Leaderboard Rendering ──────────────────────────────────────────────────────
function renderLeaderboard() {
  const def = BOARDS[currentBoard]; if (!def) return;
  let data = getFilteredData();
  const mul = currentSort.dir === "asc" ? 1 : -1;
  data.sort((a, b) => {
    const av = cellVal(a, currentSort.key), bv = cellVal(b, currentSort.key);
    return typeof av === "string" ? mul * av.localeCompare(bv) : mul * (av - bv);
  });

  document.getElementById("leaderboard-thead").innerHTML = `<tr>${def.cols.map(col => {
    const sorted = currentSort.key === col.key;
    const sortCls = sorted ? (currentSort.dir === "asc" ? " --sorted-asc" : " --sorted-desc") : "";
    const align = col.align === "right" ? " --right" : col.align === "center" ? " --center" : "";
    const sortable = !["rank","pareto"].includes(col.key) ? " --sortable" : "";
    const aria = sorted ? ` aria-sort="${currentSort.dir === "asc" ? "ascending" : "descending"}"` : "";
    return `<th class="${align}${sortable}${sortCls}" data-sort="${col.key}"${sortable ? ' tabindex="0"' : ""}${aria}>${col.label}</th>`;
  }).join("")}</tr>`;

  document.getElementById("leaderboard-tbody").innerHTML = data.map((d, i) => {
    const pareto = d.pareto_rank === 0;
    return `<tr class="--clickable${pareto ? " --pareto" : ""}" data-hash="${d.config_hash}" tabindex="0">
    ${def.cols.map(col => {
      const align = col.align === "right" ? " --right" : col.align === "center" ? " --center" : "";
      let val;
      if (col.key === "rank") val = `<span class="--rank">${i + 1}</span>`;
      else if (col.key === "model") val = `<span class="flex flex--center flex--gap-8">${modelBadge(d.config.model_name)} <strong>${shortModel(d.config.model_name)}</strong></span>`;
      else if (col.key === "quant") val = `<span class="mta-tag mta-tag--blue mta-tag--sm">${quantLabel(d.config.quantization)}</span>`;
      else if (col.key === "batch") val = d.config.batch_size;
      else if (col.key === "pareto") val = pareto ? '<span class="mta-badge mta-badge--pareto">Pareto</span>' : "";
      else {
        const raw = d.metrics[col.key]; const cls = col.color ? " " + col.color(raw) : "";
        val = `<span class="--mono${cls}">${col.fmt ? col.fmt(raw) : fmtNum(raw)}</span>`;
      }
      return `<td class="${align}">${val}</td>`;
    }).join("")}</tr>`;
  }).join("");

  staggerRows(document.getElementById("leaderboard-tbody"), "tr");
  document.getElementById("leaderboard-panel").setAttribute("aria-labelledby", `tab-${currentBoard}`);
}

// Sort + Tab handlers
document.getElementById("leaderboard-thead").addEventListener("click", e => {
  const th = e.target.closest("th.--sortable"); if (!th) return; handleSort(th.dataset.sort);
});
document.getElementById("leaderboard-thead").addEventListener("keydown", e => {
  if ((e.key === "Enter" || e.key === " ") && e.target.closest("th.--sortable")) {
    e.preventDefault(); handleSort(e.target.closest("th").dataset.sort);
  }
});

function handleSort(key) {
  if (currentSort.key === key) currentSort.dir = currentSort.dir === "asc" ? "desc" : "asc";
  else { currentSort.key = key; currentSort.dir = BOARDS[currentBoard].dir || "asc"; }
  renderLeaderboard();
}

const tabContainer = document.getElementById("leaderboard-tabs");
tabContainer.addEventListener("click", e => { const tab = e.target.closest(".mta-tab"); if (tab) activateTab(tab); });
tabContainer.addEventListener("keydown", e => {
  const tabs = [...tabContainer.querySelectorAll(".mta-tab")];
  const cur = tabs.indexOf(e.target); if (cur === -1) return;
  let next;
  if (e.key === "ArrowRight") next = (cur + 1) % tabs.length;
  else if (e.key === "ArrowLeft") next = (cur - 1 + tabs.length) % tabs.length;
  else if (e.key === "Home") next = 0;
  else if (e.key === "End") next = tabs.length - 1;
  else return;
  e.preventDefault(); tabs[next].focus(); activateTab(tabs[next]);
});

function activateTab(tab) {
  tabContainer.querySelectorAll(".mta-tab").forEach(t => {
    t.classList.remove("is-selected"); t.setAttribute("aria-selected", "false"); t.setAttribute("tabindex", "-1");
  });
  tab.classList.add("is-selected"); tab.setAttribute("aria-selected", "true"); tab.setAttribute("tabindex", "0");
  currentBoard = tab.dataset.board;
  const def = BOARDS[currentBoard];
  currentSort = { key: def.sortKey, dir: def.dir };
  renderLeaderboard();
}

// Filters
function populateFilters() {
  const data = completedData();
  const add = (id, items, fmt) => items.forEach(v => {
    const o = document.createElement("option"); o.value = v; o.textContent = fmt ? fmt(v) : v;
    document.getElementById(id).appendChild(o);
  });
  add("filter-model", [...new Set(data.map(d => shortModel(d.config.model_name)))].sort());
  add("filter-quant", [...new Set(data.map(d => d.config.quantization))].sort(), q => q === "none" ? "No Quant" : q.toUpperCase());
  add("filter-batch", [...new Set(data.map(d => d.config.batch_size))].sort((a,b) => a-b));
}
["filter-model","filter-quant","filter-batch"].forEach(id => document.getElementById(id).addEventListener("change", renderLeaderboard));

// ─── Detail Modal ───────────────────────────────────────────────────────────────
let modalPieChart = null, modalLatencyChart = null, prevFocus = null;

function openDetailModal(exp) {
  const c = exp.config, m = exp.metrics, ml = getModelLine(c.model_name);
  prevFocus = document.activeElement;
  document.getElementById("modal-title").textContent = shortModel(c.model_name) + " — Detail";
  document.getElementById("modal-config-tags").innerHTML = [
    `<span class="mta-tag mta-tag--blue">${quantLabel(c.quantization)}</span>`,
    `<span class="mta-tag">Batch ${c.batch_size}</span>`,
    `<span class="mta-tag">Seq ${c.sequence_length}</span>`,
    `<span class="mta-tag">${c.dtype}</span>`,
    c.use_kv_cache ? `<span class="mta-tag mta-tag--green">KV Cache</span>` : "",
    exp.pareto_rank === 0 ? `<span class="mta-badge mta-badge--pareto">Pareto</span>` : "",
    `<span class="mta-tag mta-tag--orange">${exp.strategy_used}</span>`
  ].filter(Boolean).join(" ");

  const metrics = [
    { l: "SCI Score", v: (m.sci_per_token*1e6).toFixed(2)+" µg", c: "green" },
    { l: "Energy/Token", v: m.energy_per_token_j.toFixed(3)+" J", c: "green" },
    { l: "kWh/Token", v: m.energy_kwh_per_token.toExponential(2) },
    { l: "Throughput", v: m.tokens_per_sec.toFixed(0)+" tok/s", c: "blue" },
    { l: "Efficiency", v: m.gpu_efficiency.toFixed(3)+" tok/J", c: "blue" },
    { l: "Cost/1M tok", v: "$"+(m.cost_per_token_usd*1e6).toFixed(4) },
    { l: "Avg Power", v: m.gpu_power_avg_w.toFixed(1)+" W" },
    { l: "Max Power", v: m.gpu_power_max_w.toFixed(1)+" W", c: m.gpu_power_max_w>80?"yellow":"" },
    { l: "GPU Util", v: m.gpu_util_avg_pct.toFixed(1)+"%" },
    { l: "Temp (avg)", v: m.gpu_temp_avg_c.toFixed(1)+" °C", c: m.gpu_temp_avg_c>60?"yellow":"" },
    { l: "Temp (max)", v: m.gpu_temp_max_c.toFixed(1)+" °C", c: m.gpu_temp_max_c>75?"red":"" },
    { l: "Throttled", v: m.thermal_throttled?"YES":"No", c: m.thermal_throttled?"red":"green" },
    { l: "Memory", v: m.mem_used_gb.toFixed(1)+" GB" },
    { l: "Mem Pressure", v: m.mem_pressure_pct.toFixed(1)+"%" },
    { l: "Val BPB", v: m.val_bpb.toFixed(4), c: "blue" },
    { l: "Total Tokens", v: m.total_tokens.toLocaleString() }
  ];
  document.getElementById("modal-metrics").innerHTML = metrics.map(met =>
    `<div class="mta-metric${met.c ? " mta-metric--"+met.c : ""}"><div class="mta-metric__label">${met.l}</div><div class="mta-metric__value">${met.v}</div></div>`
  ).join("");

  // SCI breakdown
  const opPct = (m.carbon_operational_g / (m.carbon_operational_g + m.carbon_embodied_g) * 100).toFixed(1);
  const emPct = (100 - parseFloat(opPct)).toFixed(1);
  document.getElementById("modal-sci-details").innerHTML = `
    <div class="mta-well mta-well--gray mb-0">
      <div class="t-h4 mb-8">SCI = (E × I) + M per token</div>
      <div class="t-small mb-16">
        <div class="flex flex--between mb-8"><span>Operational</span><span class="t-bold t-green">${opPct}%</span></div>
        <div class="mta-score-bar"><div class="mta-score-bar__fill" style="width:${opPct}%"></div></div>
        <div class="t-xs t-muted mt-8">${m.carbon_operational_g.toExponential(2)} g/tok</div>
      </div>
      <div class="t-small">
        <div class="flex flex--between mb-8"><span>Embodied</span><span class="t-bold" style="color:${MTA.purple}">${emPct}%</span></div>
        <div class="mta-score-bar"><div class="mta-score-bar__fill" style="width:${emPct}%;background:${MTA.purple}"></div></div>
        <div class="t-xs t-muted mt-8">${m.carbon_embodied_g.toExponential(2)} g/tok</div>
      </div>
    </div>`;

  // Charts
  const pieCtx = document.getElementById("modal-pie-chart").getContext("2d");
  if (modalPieChart) modalPieChart.destroy();
  modalPieChart = new Chart(pieCtx, {
    type: "doughnut",
    data: { labels: ["Operational","Embodied"], datasets: [{ data: [m.carbon_operational_g, m.carbon_embodied_g], backgroundColor: [MTA.green, MTA.gray], borderWidth: 0, hoverOffset: 6 }] },
    options: { responsive: true, plugins: { legend: { position: "bottom", labels: { padding: 16 } } }, cutout: "60%" }
  });

  const latCtx = document.getElementById("modal-latency-chart").getContext("2d");
  if (modalLatencyChart) modalLatencyChart.destroy();
  modalLatencyChart = new Chart(latCtx, {
    type: "bar",
    data: { labels: ["P50","P95","P99"], datasets: [{ data: [m.latency_p50_ms, m.latency_p95_ms, m.latency_p99_ms], backgroundColor: [MTA.blue, MTA.green, MTA.orange], borderRadius: 4, barPercentage: .5 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { title: { display: true, text: "ms" }, beginAtZero: true, grid: { color: "rgba(255,255,255,0.05)" } }, x: { grid: { display: false } } } }
  });

  const overlay = document.getElementById("detail-modal");
  overlay.classList.add("is-open"); overlay.setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
  document.getElementById("modal-close").focus();
}

function closeDetailModal() {
  const overlay = document.getElementById("detail-modal");
  overlay.classList.remove("is-open"); overlay.setAttribute("aria-hidden", "true");
  document.body.style.overflow = "";
  if (prevFocus) { prevFocus.focus(); prevFocus = null; }
}

// Modal events
document.getElementById("detail-modal").addEventListener("keydown", e => {
  if (e.key === "Escape") { closeDetailModal(); return; }
  if (e.key !== "Tab") return;
  const focusable = document.querySelector(".mta-modal").querySelectorAll('button, [href], input, select, [tabindex]:not([tabindex="-1"])');
  if (!focusable.length) return;
  const first = focusable[0], last = focusable[focusable.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
});
document.getElementById("modal-close").addEventListener("click", closeDetailModal);
document.getElementById("detail-modal").addEventListener("click", e => { if (e.target === e.currentTarget) closeDetailModal(); });

// ─── Calculator ─────────────────────────────────────────────────────────────────
let regionChart = null;

function initCalculator() {
  const expSel = document.getElementById("calc-experiment");
  completedData().forEach(d => {
    const o = document.createElement("option"); o.value = d.config_hash;
    o.textContent = `${shortModel(d.config.model_name)} | ${quantLabel(d.config.quantization)} | batch=${d.config.batch_size}`;
    expSel.appendChild(o);
  });
  const regSel = document.getElementById("calc-region");
  Object.keys(CARBON_PRESETS).forEach(r => {
    const o = document.createElement("option"); o.value = r;
    o.textContent = `${r} (${CARBON_PRESETS[r]} gCO₂/kWh)`; regSel.appendChild(o);
  });
  expSel.addEventListener("change", updateCalculator);
  regSel.addEventListener("change", updateCalculator);
  updateCalculator();
}

function updateCalculator() {
  const exp = BENCHMARK_DATA.find(d => d.config_hash === document.getElementById("calc-experiment").value);
  if (!exp) return;
  const region = document.getElementById("calc-region").value;
  const intensity = CARBON_PRESETS[region] || 210;
  document.getElementById("calc-intensity-value").textContent = intensity;
  const m = exp.metrics;
  const op = m.energy_kwh_per_token * intensity, emb = m.carbon_embodied_g, sci = op + emb;
  const sciEl = document.getElementById("calc-sci-result");
  sciEl.textContent = (sci * 1e6).toFixed(2) + " µg"; flashElement(sciEl);
  document.getElementById("calc-operational").textContent = (op * 1e6).toFixed(2) + " µg";
  document.getElementById("calc-embodied").textContent = (emb * 1e6).toFixed(2) + " µg";

  // Region chart
  const pairs = Object.keys(CARBON_PRESETS).map(r => ({
    region: r, sci: (m.energy_kwh_per_token * CARBON_PRESETS[r] + emb) * 1e6, selected: r === region
  })).sort((a, b) => a.sci - b.sci);

  if (regionChart) regionChart.destroy();
  regionChart = new Chart(document.getElementById("region-chart").getContext("2d"), {
    type: "bar",
    data: {
      labels: pairs.map(p => p.region.split(" (")[0]),
      datasets: [{ label: "SCI (µg)", data: pairs.map(p => p.sci),
        backgroundColor: pairs.map(p => p.selected ? MTA.green : "#2A2A2A"), borderRadius: 4, barPercentage: .7 }]
    },
    options: {
      indexAxis: "y", responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => c.parsed.x.toFixed(2) + " µgCO₂/tok" } } },
      scales: { x: { title: { display: true, text: "SCI (µgCO₂/token)" }, grid: { color: "rgba(255,255,255,0.05)" } }, y: { grid: { display: false }, ticks: { font: { size: 11 } } } }
    }
  });
}


// ─── Per-Model Analysis Charts ──────────────────────────────────────────────────
const perModelCharts = [];

const BATCH_SIZES = [1, 2, 4, 8, 16, 32];
const BATCH_RADII = { 1: 4, 2: 6, 4: 8, 8: 11, 16: 15, 32: 20 };

function renderPerModelCharts() {
  const data = completedData();
  const models = [...new Set(data.map(d => shortModel(d.config.model_name)))].sort();
  const grid = document.getElementById("permodel-grid");
  grid.innerHTML = "";
  perModelCharts.forEach(c => c.destroy());
  perModelCharts.length = 0;

  models.forEach(model => {
    const ml = MODEL_LINES[model] || { bg: MTA.gray, border: "#666", circle: "gray", letter: "?", label: model };
    const points = data.filter(d => shortModel(d.config.model_name) === model);
    const avgBpb = (points.reduce((s, p) => s + p.metrics.val_bpb, 0) / points.length).toFixed(2);

    const card = document.createElement("div");
    card.className = "mta-permodel-card";
    card.innerHTML =
      `<div class="mta-permodel-card__header">
        <div class="mta-permodel-card__title">
          <span class="mta-circle mta-circle--sm mta-circle--${ml.circle}">${ml.letter}</span>
          <span style="color:${ml.bg}">${model}</span>
        </div>
        <div class="mta-permodel-card__subtitle">BPB=${avgBpb}</div>
      </div>
      <div class="mta-permodel-card__canvas"><canvas></canvas></div>`;
    grid.appendChild(card);

    const ctx = card.querySelector("canvas").getContext("2d");
    const chartData = points.map(p => ({
      x: p.metrics.tokens_per_sec,
      y: p.metrics.sci_per_token * 1e6,
      r: BATCH_RADII[p.config.batch_size] || 6,
      _raw: p
    }));

    const chart = new Chart(ctx, {
      type: "bubble",
      data: {
        datasets: [{
          label: model,
          data: chartData,
          backgroundColor: ml.bg + "AA",
          borderColor: ml.border,
          borderWidth: 1.5,
          hoverBorderWidth: 2.5
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label(ctx) {
                const r = ctx.raw._raw;
                return [
                  quantLabel(r.config.quantization) + " | batch=" + r.config.batch_size,
                  r.metrics.tokens_per_sec.toFixed(1) + " tok/s",
                  (r.metrics.sci_per_token * 1e6).toFixed(1) + " \u00b5gCO\u2082/tok"
                ];
              }
            },
            backgroundColor: MTA.black, titleColor: MTA.white, bodyColor: MTA.white,
            padding: 10, cornerRadius: 4
          }
        },
        scales: {
          x: {
            type: "logarithmic",
            title: { display: true, text: "tok/s (log)", font: { size: 11, weight: "600" } },
            grid: { color: "rgba(255,255,255,0.05)" },
            ticks: { font: { size: 10 } }
          },
          y: {
            type: "logarithmic",
            title: { display: true, text: "SCI (\u00b5gCO\u2082/tok, log)", font: { size: 11, weight: "600" } },
            grid: { color: "rgba(255,255,255,0.05)" },
            ticks: { font: { size: 10 } }
          }
        },
        onClick(_, elements) {
          if (elements.length > 0) {
            const raw = chartData[elements[0].index]._raw;
            if (raw) openDetailModal(raw);
          }
        }
      }
    });
    perModelCharts.push(chart);
  });

  // Shared batch-size legend
  const legendEl = document.getElementById("permodel-legend");
  legendEl.innerHTML = BATCH_SIZES.map(bs => {
    const r = BATCH_RADII[bs] || 6;
    return `<span class="mta-permodel-legend__item">
      <span class="mta-permodel-legend__dot" style="width:${r * 2}px;height:${r * 2}px;background:${MTA.gray}AA"></span>
      batch=${bs}</span>`;
  }).join("");
}

// ─── SCI Scaling Law Chart ────────────────────────────────────────────────────
let scalingChart = null;
let scalingBarChart = null;

function renderScalingLawCharts() {
  const law = SCI_SCALING_LAW;

  // --- Left: Log-log scatter with regression line ---
  const scatterCtx = document.getElementById("scaling-chart").getContext("2d");

  // Regression line points (log-spaced from 0.5B to 600B)
  const linePoints = [];
  for (let i = -0.3; i <= 2.8; i += 0.05) {
    const p = Math.pow(10, i);
    const sci = law.coefficient * Math.pow(p, law.exponent) * 1e6; // µgCO2
    linePoints.push({ x: p, y: sci });
  }

  const datasets = [
    {
      label: "Regression: SCI = 0.000207 × params^0.374",
      type: "line",
      data: linePoints,
      borderColor: MTA.red,
      borderWidth: 2.5,
      borderDash: [6, 3],
      pointRadius: 0,
      fill: false,
      order: 2
    },
    {
      label: "Measured (DGX Spark)",
      data: law.measured.map(m => ({
        x: m.params_b,
        y: m.sci_per_token * 1e6,
        r: 12,
        _label: m.name
      })),
      backgroundColor: MTA.green + "CC",
      borderColor: MTA.green,
      borderWidth: 2,
      hoverBorderWidth: 3,
      order: 0
    },
    {
      label: "Predicted (Frontier)",
      data: law.predicted.map(m => ({
        x: m.params_b,
        y: m.sci_per_token * 1e6,
        r: 8,
        _label: m.name
      })),
      backgroundColor: MTA.orange + "99",
      borderColor: MTA.orange,
      borderWidth: 1.5,
      hoverBorderWidth: 3,
      order: 1
    }
  ];

  if (scalingChart) scalingChart.destroy();
  scalingChart = new Chart(scatterCtx, {
    type: "bubble",
    data: { datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: function(ctx) {
              const d = ctx.raw;
              if (d._label) {
                return `${d._label} (${d.x}B) — ${d.y.toFixed(1)} \u00B5gCO\u2082/tok`;
              }
              return ctx.dataset.label;
            }
          },
          backgroundColor: "#111111",
          titleColor: "#fff",
          bodyColor: "#fff",
          padding: 12,
          cornerRadius: 7
        }
      },
      scales: {
        x: {
          type: "logarithmic",
          title: { display: true, text: "Model Parameters (Billions)", font: { weight: 600 } },
          grid: { color: "rgba(0,0,0,0.05)" },
          ticks: {
            callback: v => {
              const vals = [1, 5, 10, 50, 100, 500];
              return vals.includes(v) ? v + "B" : "";
            }
          }
        },
        y: {
          type: "logarithmic",
          title: { display: true, text: "SCI (\u00B5gCO\u2082 / token)", font: { weight: 600 } },
          grid: { color: "rgba(0,0,0,0.05)" }
        }
      }
    }
  });

  // --- Right: Horizontal bar chart of all models sorted by SCI ---
  const barCtx = document.getElementById("scaling-bar-chart").getContext("2d");
  const allModels = [
    ...law.measured.map(m => ({ ...m, type: "measured" })),
    ...law.predicted.map(m => ({ ...m, type: "predicted" }))
  ].sort((a, b) => a.sci_per_token - b.sci_per_token);

  const barLabels = allModels.map(m => `${m.name} (${m.params_b}B)`);
  const barValues = allModels.map(m => m.sci_per_token * 1e6);
  const barColors = allModels.map(m =>
    m.type === "measured" ? (MTA.green + "CC") : (MTA.orange + "99")
  );

  if (scalingBarChart) scalingBarChart.destroy();
  scalingBarChart = new Chart(barCtx, {
    type: "bar",
    data: {
      labels: barLabels,
      datasets: [{
        data: barValues,
        backgroundColor: barColors,
        borderColor: barColors.map((_, i) => allModels[i].type === "measured" ? MTA.green : MTA.orange),
        borderWidth: 1
      }]
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => `${ctx.parsed.x.toFixed(1)} \u00B5gCO\u2082/token`
          },
          backgroundColor: "#111111",
          titleColor: "#fff",
          bodyColor: "#fff",
          padding: 12,
          cornerRadius: 7
        }
      },
      scales: {
        x: {
          title: { display: true, text: "SCI (\u00B5gCO\u2082 / token)", font: { weight: 600 } },
          grid: { color: "rgba(0,0,0,0.05)" }
        },
        y: {
          ticks: { font: { size: 11 } },
          grid: { display: false }
        }
      }
    }
  });

  // Legend
  const legendEl = document.getElementById("scaling-legend");
  legendEl.innerHTML =
    `<span class="mta-permodel-legend__item"><span class="mta-permodel-legend__dot" style="background:${MTA.green}"></span>Measured (DGX Spark)</span>` +
    `<span class="mta-permodel-legend__item"><span class="mta-permodel-legend__dot" style="background:${MTA.orange}"></span>Predicted (Frontier)</span>` +
    `<span class="mta-permodel-legend__item"><span class="mta-permodel-legend__dot" style="background:${MTA.red};border:2px dashed ${MTA.red}"></span>Power Law (R\u00B2=0.979)</span>`;
}

// ─── 3D Visualizations (Plotly.js) ────────────────────────────────────────────

const PLOTLY_AXIS_STYLE = {
  showgrid: true, gridcolor: "rgba(255,255,255,0.06)",
  showline: true, linecolor: "#2A2A2A", linewidth: 1,
  showspikes: false, zeroline: false,
  title: { font: { size: 13, color: "#888888", weight: 600 } },
  tickfont: { size: 11, color: "#666666" },
  backgroundcolor: "rgba(10,10,10,0)"
};
const PLOTLY_LOG_AXIS = { ...PLOTLY_AXIS_STYLE, type: "log", dtick: 1 };
const PLOTLY_LIN_AXIS = { ...PLOTLY_AXIS_STYLE };

const PLOTLY_LAYOUT_BASE = {
  paper_bgcolor: "rgba(0,0,0,0)",
  plot_bgcolor: "rgba(0,0,0,0)",
  font: { family: "Inter, sans-serif", size: 12, color: "#888888" },
  margin: { l: 0, r: 0, t: 40, b: 0 },
  showlegend: true,
  legend: { x: 0, y: 1, bgcolor: "rgba(17,17,17,0.9)", bordercolor: "#2A2A2A", borderwidth: 1, font: { size: 12, color: "#E8E8E8" } }
};

function render3DPareto() {
  const data = completedData();
  const models = [...new Set(data.map(d => shortModel(d.config.model_name)))];
  const lineColors = { "Qwen3.5-0.8B": MTA.green, "Qwen3.5-4B": MTA.yellow, "Qwen3.5-9B": MTA.red };

  const traces = models.map(model => {
    const pts = data.filter(d => shortModel(d.config.model_name) === model);
    return {
      type: "scatter3d",
      mode: "markers",
      name: model,
      x: pts.map(p => p.metrics.tokens_per_sec),
      y: pts.map(p => p.metrics.latency_p50_ms),
      z: pts.map(p => p.metrics.sci_per_token * 1e6),
      text: pts.map(p => `${model}<br>batch=${p.config.batch_size} seq=${p.config.sequence_length}<br>` +
        `${p.metrics.tokens_per_sec.toFixed(0)} tok/s | P50: ${p.metrics.latency_p50_ms.toFixed(1)}ms<br>` +
        `SCI: ${(p.metrics.sci_per_token * 1e6).toFixed(1)} \u00B5gCO\u2082/tok<br>` +
        `Power: ${p.metrics.gpu_power_avg_w.toFixed(0)}W | BPB: ${p.metrics.val_bpb.toFixed(2)}`),
      hoverinfo: "text",
      marker: {
        size: pts.map(p => Math.max(4, Math.min(16, p.config.batch_size / 2))),
        color: lineColors[model] || MTA.gray,
        opacity: 0.85,
        line: { width: 1, color: "#333" }
      }
    };
  });

  // Efficiency surface — Gaussian RBF interpolation for a smooth curved fit
  const logTps = data.map(d => Math.log10(d.metrics.tokens_per_sec));
  const logLat = data.map(d => Math.log10(d.metrics.latency_p50_ms));
  const logSci = data.map(d => Math.log10(d.metrics.sci_per_token * 1e6));
  const nPts = data.length;

  // Build surface grid in log space
  const SURF_RES = 35;
  const sigma = 0.45; // RBF bandwidth — controls smoothness
  const tpsMin = Math.min(...logTps) - 0.3, tpsMax = Math.max(...logTps) + 0.3;
  const latMin = Math.min(...logLat) - 0.3, latMax = Math.max(...logLat) + 0.3;
  const tpsGrid = [], latGrid = [];
  for (let i = 0; i < SURF_RES; i++) {
    tpsGrid.push(tpsMin + (tpsMax - tpsMin) * i / (SURF_RES - 1));
    latGrid.push(latMin + (latMax - latMin) * i / (SURF_RES - 1));
  }

  // Evaluate RBF at each grid point: weighted average of data SCI values
  const surfZ = [], surfColor = [];
  let zMin = Infinity, zMax = -Infinity;
  for (let j = 0; j < SURF_RES; j++) {
    const rowZ = [], rowC = [];
    for (let i = 0; i < SURF_RES; i++) {
      let wSum = 0, vSum = 0;
      for (let k = 0; k < nPts; k++) {
        const dx = tpsGrid[i] - logTps[k];
        const dy = latGrid[j] - logLat[k];
        const w = Math.exp(-(dx * dx + dy * dy) / (2 * sigma * sigma));
        wSum += w;
        vSum += w * logSci[k];
      }
      const val = Math.pow(10, vSum / wSum);
      rowZ.push(val);
      rowC.push(vSum / wSum); // log-scale value for coloring
      if (val < zMin) zMin = val;
      if (val > zMax) zMax = val;
    }
    surfZ.push(rowZ);
    surfColor.push(rowC);
  }

  // Convert grid to linear scale for Plotly axes
  const tpsLinear = tpsGrid.map(v => Math.pow(10, v));
  const latLinear = latGrid.map(v => Math.pow(10, v));

  traces.push({
    type: "surface",
    name: "Efficiency Surface",
    x: tpsLinear,
    y: latLinear,
    z: surfZ,
    surfacecolor: surfColor,
    colorscale: [
      [0,    "#5DB800"],
      [0.25, "#76FF03"],
      [0.5,  "#D4860A"],
      [0.75, "#C8470A"],
      [1,    "#E03030"]
    ],
    showscale: true,
    colorbar: {
      title: { text: "SCI (log)", side: "right", font: { size: 11 } },
      thickness: 14, len: 0.6, tickfont: { size: 10 }
    },
    opacity: 0.7,
    contours: {
      z: { show: true, usecolormap: true, highlightcolor: "#fff", width: 2, size: (zMax - zMin) / 8 }
    },
    lighting: { ambient: 0.7, diffuse: 0.5, specular: 0.3, roughness: 0.5 },
    hovertemplate: "Throughput: %{x:.0f} tok/s<br>Latency: %{y:.1f} ms<br>SCI: %{z:.0f} \u00B5gCO\u2082/tok<extra></extra>"
  });

  Plotly.newPlot("pareto3d-chart", traces, {
    ...PLOTLY_LAYOUT_BASE,
    title: { text: "Throughput \u00D7 Latency \u00D7 Carbon", font: { size: 16, weight: 700 } },
    scene: {
      xaxis: { ...PLOTLY_LOG_AXIS, title: "Throughput (tok/s \u2191)" },
      yaxis: { ...PLOTLY_LOG_AXIS, title: "Latency P50 (ms \u2193)" },
      zaxis: { ...PLOTLY_LOG_AXIS, title: "SCI (\u00B5gCO\u2082/tok \u2193)" },
      camera: { eye: { x: 1.8, y: 1.4, z: 0.9 } }
    }
  }, { responsive: true });
}

function render3DScaling() {
  const law = SCI_SCALING_LAW;

  // Measured points
  const measuredTrace = {
    type: "scatter3d",
    mode: "markers",
    name: "Measured (DGX Spark)",
    x: law.measured.map(m => m.params_b),
    y: law.measured.map(m => m.sci_per_token * 1e6),
    z: law.measured.map(m => m.sci_per_token * 1e6 / (law.coefficient * Math.pow(m.params_b, law.exponent) * 1e6) * 100),
    text: law.measured.map(m => `${m.name}<br>${m.params_b}B params<br>SCI: ${(m.sci_per_token * 1e6).toFixed(1)} \u00B5gCO\u2082/tok`),
    hoverinfo: "text",
    marker: { size: 10, color: MTA.green, opacity: 0.9, line: { width: 2, color: "#006B2B" } }
  };

  // Predicted frontier points
  const predTrace = {
    type: "scatter3d",
    mode: "markers",
    name: "Predicted (Frontier)",
    x: law.predicted.map(m => m.params_b),
    y: law.predicted.map(m => m.sci_per_token * 1e6),
    z: law.predicted.map(m => 100), // On the regression line = 100%
    text: law.predicted.map(m => `${m.name}<br>${m.params_b}B params<br>Predicted SCI: ${(m.sci_per_token * 1e6).toFixed(1)} \u00B5gCO\u2082/tok`),
    hoverinfo: "text",
    marker: { size: 7, color: MTA.orange, opacity: 0.8, line: { width: 1, color: "#D4500F" } }
  };

  // Regression surface (grid)
  const paramRange = [];
  for (let i = -0.1; i <= 2.75; i += 0.1) paramRange.push(Math.pow(10, i));
  const confRange = [80, 90, 100, 110, 120]; // % of regression
  const surfX = [], surfY = [], surfZ = [];
  for (const conf of confRange) {
    const row_x = [], row_y = [], row_z = [];
    for (const p of paramRange) {
      const sci = law.coefficient * Math.pow(p, law.exponent) * 1e6 * (conf / 100);
      row_x.push(p);
      row_y.push(sci);
      row_z.push(conf);
    }
    surfX.push(row_x);
    surfY.push(row_y);
    surfZ.push(row_z);
  }

  const surfaceTrace = {
    type: "surface",
    name: "Regression Surface",
    x: surfX, y: surfY, z: surfZ,
    colorscale: [[0, "rgba(93,184,0,0.12)"], [0.5, "rgba(212,134,10,0.12)"], [1, "rgba(224,48,48,0.12)"]],
    showscale: false,
    opacity: 0.4,
    hoverinfo: "skip"
  };

  Plotly.newPlot("scaling3d-chart", [surfaceTrace, measuredTrace, predTrace], {
    ...PLOTLY_LAYOUT_BASE,
    title: { text: "Scaling Law: Params \u00D7 SCI \u00D7 Confidence", font: { size: 16, weight: 700 } },
    scene: {
      xaxis: { ...PLOTLY_LOG_AXIS, title: "Parameters (B)" },
      yaxis: { ...PLOTLY_LOG_AXIS, title: "SCI (\u00B5gCO\u2082/tok)" },
      zaxis: { ...PLOTLY_LIN_AXIS, title: "% of Regression", range: [75, 125] },
      camera: { eye: { x: 1.6, y: 1.6, z: 1.0 } }
    }
  }, { responsive: true });
}

function render3DSensor() {
  if (typeof SENSOR_TIMESERIES === "undefined") return;

  const colors = [MTA.green, MTA.yellow, MTA.red];
  const traces = SENSOR_TIMESERIES.map((series, i) => ({
    type: "scatter3d",
    mode: "lines",
    name: series.label,
    x: series.data.map(d => d.t),
    y: series.data.map(d => d.power),
    z: series.data.map(d => d.temp),
    text: series.data.map(d => `${series.label}<br>t=${d.t}s<br>Power: ${d.power}W<br>Temp: ${d.temp}\u00B0C<br>Util: ${d.util}%`),
    hoverinfo: "text",
    line: { color: colors[i], width: 4 },
    opacity: 0.9
  }));

  // Add utilization as marker size on a sampled subset
  SENSOR_TIMESERIES.forEach((series, i) => {
    const sampled = series.data.filter((_, j) => j % 10 === 0);
    traces.push({
      type: "scatter3d",
      mode: "markers",
      name: series.label + " (util%)",
      showlegend: false,
      x: sampled.map(d => d.t),
      y: sampled.map(d => d.power),
      z: sampled.map(d => d.temp),
      text: sampled.map(d => `Util: ${d.util}%`),
      hoverinfo: "text",
      marker: {
        size: sampled.map(d => Math.max(2, d.util / 10)),
        color: colors[i],
        opacity: 0.5,
        line: { width: 0 }
      }
    });
  });

  Plotly.newPlot("sensor3d-chart", traces, {
    ...PLOTLY_LAYOUT_BASE,
    title: { text: "Sensor Timeline: Time \u00D7 Power \u00D7 Temperature", font: { size: 16, weight: 700 } },
    scene: {
      xaxis: { ...PLOTLY_LIN_AXIS, title: "Time (seconds)" },
      yaxis: { ...PLOTLY_LIN_AXIS, title: "GPU Power (W)" },
      zaxis: { ...PLOTLY_LIN_AXIS, title: "GPU Temp (\u00B0C)" },
      camera: { eye: { x: 1.5, y: 1.5, z: 0.8 } }
    }
  }, { responsive: true });
}

// 3D tab switching
const viz3dRendered = { pareto3d: false, scaling3d: false, sensor3d: false };

function init3DTabs() {
  const tabContainer = document.getElementById("viz3d-tabs");
  if (!tabContainer) return;

  tabContainer.addEventListener("click", e => {
    const tab = e.target.closest(".mta-tab");
    if (!tab) return;
    const vizId = tab.dataset.viz3d;
    if (!vizId) return;

    // Toggle tabs
    tabContainer.querySelectorAll(".mta-tab").forEach(t => {
      t.classList.remove("is-selected");
      t.setAttribute("aria-selected", "false");
    });
    tab.classList.add("is-selected");
    tab.setAttribute("aria-selected", "true");

    // Toggle panels
    document.querySelectorAll(".viz3d-panel").forEach(p => { p.style.display = "none"; p.classList.remove("is-active"); });
    const panel = document.getElementById("panel-" + vizId);
    if (panel) { panel.style.display = "block"; panel.classList.add("is-active"); }

    // Lazy render
    if (!viz3dRendered[vizId]) {
      if (vizId === "pareto3d") render3DPareto();
      else if (vizId === "scaling3d") render3DScaling();
      else if (vizId === "sensor3d") render3DSensor();
      viz3dRendered[vizId] = true;
    } else {
      // Resize on tab switch (Plotly needs this)
      Plotly.Plots.resize(document.getElementById(vizId + "-chart"));
    }
  });

  // Render first tab
  if (typeof Plotly !== "undefined") {
    render3DPareto();
    viz3dRendered.pareto3d = true;
  }
}

// ─── Initialize ─────────────────────────────────────────────────────────────────
function init() {
  renderStatsBar(); renderPerModelCharts(); renderScalingLawCharts(); renderPreviewTable();
  populateFilters(); renderLeaderboard();
  document.querySelectorAll("#leaderboard-tabs .mta-tab").forEach((t, i) => t.setAttribute("tabindex", i === 0 ? "0" : "-1"));
  pageInitialized.dashboard = true; pageInitialized.leaderboards = true;
  // 3D charts (lazy-loaded via Plotly)
  if (typeof Plotly !== "undefined") init3DTabs();
  else window.addEventListener("load", () => { if (typeof Plotly !== "undefined") init3DTabs(); });
}

if (typeof Chart !== "undefined") document.addEventListener("DOMContentLoaded", init);
else window.addEventListener("load", init);
