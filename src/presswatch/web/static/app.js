// PressWatch dashboard. Plain ES modules, no build step.
// All data strings are inserted with textContent (never innerHTML).

import { LineChart, fmt, renderLegend, svg } from "./charts.js";

const $ = (id) => document.getElementById(id);
// On the static (Netlify) site replay.js answers /api/* from a recording.
const REPLAY = window.PRESSWATCH_REPLAY === true;
const REPLAY_NOTE = "In der Aufzeichnung entscheidet Arif. Lokal mit „presswatch demo“ kannst du selbst klicken.";

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

// ---- status vocabulary -------------------------------------------------------

const STATUS_TEXT = { ok: "In Ordnung", warnung: "Warnung", alarm: "Alarm" };

const ICON_PATHS = {
  ok: "M3 8.5l3 3 7-7",
  warnung: "M8 2.5l6 11H2zM8 6.5v3.2M8 11.6v.3",
  alarm: "M4 4l8 8M12 4l-8 8",
  neutral: "M3 8h10",
};

function icon(kind) {
  const el = svg("svg", { viewBox: "0 0 16 16", "aria-hidden": "true" });
  el.append(
    svg("path", {
      d: ICON_PATHS[kind] || ICON_PATHS.neutral,
      fill: "none",
      stroke: "currentColor",
      "stroke-width": 2,
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
    }),
  );
  return el;
}

function chip(kind, text) {
  return h("span", { class: `status-chip chip-${kind}`, "data-status": kind }, icon(kind), text);
}

const ACTION_LABELS = {
  doe_imported: "Einfahrversuche übernommen",
  model_trained: "Modell trainiert",
  model_activated: "Modell aktiviert",
  model_rollback: "Rollback",
  model_rejected: "Vorschlag abgelehnt",
  retraining_proposed: "Retraining vorgeschlagen",
  monitoring_reset: "Überwachung zurückgesetzt",
  sim_tool_wear: "Simulator: Werkzeugverschleiß",
  sim_sensor_fault: "Simulator: Thermoelement-Fehler",
  sim_maintenance: "Simulator: Werkzeug gewechselt",
  sim_reset_faults: "Simulator: Fehler abgeschaltet",
};

const SENSOR_NAMES = {
  dev_tool_upper_c: ["Thermoelement Oberwerkzeug", "°C", 2],
  dev_tool_lower_c: ["Thermoelement Unterwerkzeug", "°C", 2],
  dev_force_n: ["Kraftsensor", "N", 1],
  dev_force_duration_s: ["Pressensteuerung", "s", 2],
  dev_furnace_c: ["Ofenthermoelement", "°C", 2],
  gob_mass_mg: ["Waage", "mg", 2],
};

// ---- API ---------------------------------------------------------------------

async function getJSON(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${response.status} bei ${path}`);
  return response.json();
}

async function postJSON(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : `Fehler ${response.status}`;
    throw new Error(detail);
  }
  return data;
}

function toast(text) {
  const el = h("div", { class: "toast", role: "status" }, text);
  document.body.append(el);
  setTimeout(() => el.remove(), 4000);
}

function storage(keyName, value) {
  try {
    if (value === undefined) return localStorage.getItem(keyName);
    localStorage.setItem(keyName, value);
  } catch (e) {
    return null;
  }
  return null;
}

// ---- decisions need a person and a reason ----------------------------------------

function askReason(title, text, confirmLabel) {
  const dialog = $("action-dialog");
  const form = $("action-form");
  $("action-title").textContent = title;
  $("action-text").textContent = text;
  $("action-confirm").textContent = confirmLabel;
  form.actor.value = storage("presswatch-actor") || "";
  form.reason.value = "";
  dialog.showModal();
  return new Promise((resolve) => {
    dialog.addEventListener(
      "close",
      () => {
        if (dialog.returnValue !== "confirm") return resolve(null);
        const actor = form.actor.value.trim();
        storage("presswatch-actor", actor);
        resolve({ actor, reason: form.reason.value.trim() });
      },
      { once: true },
    );
  });
}

async function decide({ title, text, confirm, path, done }) {
  const answer = await askReason(title, text, confirm);
  if (!answer) return;
  try {
    const result = await postJSON(path, answer);
    toast(done(result));
    await Promise.all([refresh(), refreshSlow()]);
  } catch (error) {
    toast(error.message);
  }
}

const ACTIONS = {
  propose_retraining: {
    label: "Retraining-Vorschlag erstellen",
    run: () =>
      decide({
        title: "Retraining-Vorschlag erstellen",
        text:
          "Ein Kandidat wird auf den Einfahrversuchen und den zuletzt gemessenen Linsen trainiert " +
          "und auf den neuesten Linsen mit dem aktiven Modell verglichen. Aktiv wird er erst nach deiner Freigabe.",
        confirm: "Vorschlag erstellen",
        path: "/api/models/proposals",
        done: (r) => `Vorschlag ${r.version} erstellt – unter „Modelle“ prüfen`,
      }),
  },
  rollback: {
    label: "Rollback auf vorherige Version",
    run: () =>
      decide({
        title: "Rollback",
        text: "Die zuletzt aktive frühere Version wird wieder aktiv.",
        confirm: "Rollback ausführen",
        path: "/api/models/rollback",
        done: (r) => `${r.version} ist wieder aktiv`,
      }),
  },
  reset_monitoring: {
    label: "Überwachung zurücksetzen",
    run: () =>
      decide({
        title: "Überwachung zurücksetzen",
        text:
          "Die Überwachung sammelt eine neue Referenz. Mach das nach einer Reparatur, " +
          "nicht um einen Alarm loszuwerden.",
        confirm: "Zurücksetzen",
        path: "/api/monitoring/reset",
        done: () => "Überwachung sammelt eine neue Referenz",
      }),
  },
};

// ---- state & refresh ---------------------------------------------------------

const state = { status: null, cycles: [], models: [], audit: [], sim: null, simError: null };
let lastVerdict = null;

const charts = {
  pv: new LineChart($("chart-pv"), { height: 260, onPick: openCycle }),
  ct: new LineChart($("chart-ct"), { height: 260, onPick: openCycle }),
  cusumPv: new LineChart($("chart-cusum-pv"), { height: 200, onPick: openCycle }),
  cusumCt: new LineChart($("chart-cusum-ct"), { height: 200, onPick: openCycle }),
};

function setLive(ok, error) {
  const live = $("live");
  live.dataset.state = ok ? "online" : "offline";
  $("live-text").textContent = REPLAY && ok
    ? "Aufzeichnung"
    : ok
    ? `aktualisiert ${new Date().toLocaleTimeString("de-DE")}`
    : `keine Verbindung (${error?.message || "Fehler"})`;
}

async function refresh() {
  try {
    const [status, cycles] = await Promise.all([
      getJSON("/api/status"),
      getJSON("/api/cycles?limit=300"),
    ]);
    state.status = status;
    state.cycles = cycles;
    setLive(true);
    renderFast();
  } catch (error) {
    setLive(false, error);
  }
}

async function refreshSlow() {
  const [models, audit] = await Promise.all([
    getJSON("/api/models").catch(() => state.models),
    getJSON("/api/audit?limit=60").catch(() => state.audit),
  ]);
  state.models = models;
  state.audit = audit;
  try {
    state.sim = await getJSON("/api/sim");
    state.simError = null;
  } catch (error) {
    state.sim = null;
    state.simError = error.message;
  }
  renderModels();
  renderAudit();
  renderSim();
}

function renderFast() {
  renderVerdict();
  renderLens();
  renderKpis();
  renderHistory();
  renderMonitoring();
  renderQuality();
}

// ---- verdict -----------------------------------------------------------------

function renderVerdict() {
  const { recommendation: rec, data_drift: drift, performance } = state.status;
  const verdict = $("verdict");
  verdict.dataset.status = rec.status;
  if (lastVerdict && lastVerdict !== `${rec.status}/${rec.cause}`) {
    verdict.dataset.changed = "true";
    setTimeout(() => (verdict.dataset.changed = "false"), 1300);
  }
  lastVerdict = `${rec.status}/${rec.cause}`;
  $("verdict-chip").replaceChildren(chip(rec.status, STATUS_TEXT[rec.status]));
  $("verdict-title").textContent = rec.title;
  $("verdict-explanation").textContent = rec.explanation;
  $("verdict-actions").replaceChildren(...rec.actions.map((a) => h("li", {}, a)));
  $("verdict-evidence").replaceChildren(...rec.evidence.map((e) => h("li", {}, e)));
  const actionButtons = rec.allowed_actions
    .filter((a) => ACTIONS[a])
    .map((a, i) =>
      h("button", { class: i === 0 ? "primary" : "secondary", type: "button", onclick: ACTIONS[a].run,
        disabled: REPLAY, title: REPLAY ? REPLAY_NOTE : null }, ACTIONS[a].label),
    );
  $("verdict-buttons").replaceChildren(
    ...actionButtons,
    ...(REPLAY && actionButtons.length ? [h("p", { class: "replay-note" }, REPLAY_NOTE)] : []),
  );

  const meters = [];
  if (drift.reference_collected < drift.reference_target) {
    meters.push(["Referenz der Sensordaten", drift.reference_collected, drift.reference_target, "Zyklen"]);
  }
  const pv = performance.pv_um;
  if (pv && pv.state === "einlaufen") {
    meters.push(["Referenz des Modellfehlers", pv.n, pv.baseline_n, "gemessenen Linsen"]);
  }
  const warmup = $("warmup");
  warmup.hidden = meters.length === 0;
  warmup.replaceChildren(
    ...meters.map(([label, n, total, unit]) =>
      h("div", {},
        h("div", {}, `${label}: ${n} von ${total} ${unit}`),
        h("div", { class: "meter", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": total, "aria-valuenow": n },
          h("span", { style: `width:${Math.min(100, (100 * n) / total)}%` })),
      ),
    ),
  );
}

// ---- lens drawing --------------------------------------------------------------

function renderLens() {
  const { kpis, spec } = state.status;
  const last = kpis.last_cycle;
  const box = $("lens-drawing");
  if (!last) {
    box.replaceChildren(h("p", { class: "empty" }, "Noch keine Linse gepresst. Die Zelle startet gleich."));
    $("lens-values").replaceChildren();
    $("lens-chip").replaceChildren();
    return;
  }
  const p = last.prediction;
  const ct = p ? p.center_thickness_mm : spec.ct_nominal_mm;
  const pv = p ? p.pv_um : 0;

  const W = 470, H = 214, cx = 168, cy = 112, half = 128, edge = 24;
  const tc = Math.max(52, Math.min(140, 92 + (ct - spec.ct_nominal_mm) * 420));
  const top = cy - tc / 2, bottom = cy + tc / 2;
  const yEdgeTop = cy - edge / 2, yEdgeBottom = cy + edge / 2;
  const ctrlTop = 2 * top - yEdgeTop, ctrlBottom = 2 * bottom - yEdgeBottom;
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}` });

  root.append(
    svg("path", {
      class: "glass",
      d: `M${cx - half},${yEdgeTop} Q${cx},${ctrlTop} ${cx + half},${yEdgeTop} L${cx + half},${yEdgeBottom} ` +
        `Q${cx},${ctrlBottom} ${cx - half},${yEdgeBottom} Z`,
    }),
    svg("path", { class: "ideal", d: `M${cx - half},${yEdgeTop} Q${cx},${ctrlTop} ${cx + half},${yEdgeTop}` }),
  );

  // real surface: form deviation drawn strongly exaggerated
  const amp = Math.min(14, pv * 2.4);
  let d = "";
  for (let i = 0; i <= 60; i++) {
    const t = i / 60;
    const x = cx - half + 2 * half * t;
    const yIdeal = (1 - t) ** 2 * yEdgeTop + 2 * (1 - t) * t * ctrlTop + t ** 2 * yEdgeTop;
    const wobble = amp * Math.sin(Math.PI * t) * Math.sin(3 * Math.PI * t);
    d += `${i ? "L" : "M"}${x.toFixed(1)},${(yIdeal - wobble).toFixed(1)}`;
  }
  root.append(svg("path", { class: "real", d }));

  // dimension line for the center thickness
  const dimX = cx + half + 40;
  root.append(
    svg("line", { class: "dim", x1: cx + 4, y1: top, x2: dimX + 8, y2: top }),
    svg("line", { class: "dim", x1: cx + 4, y1: bottom, x2: dimX + 8, y2: bottom }),
    svg("line", { class: "dim", x1: dimX, y1: top, x2: dimX, y2: bottom }),
    svg("path", { class: "dim", d: `M${dimX - 4},${top + 8} L${dimX},${top} L${dimX + 4},${top + 8}` }),
    svg("path", { class: "dim", d: `M${dimX - 4},${bottom - 8} L${dimX},${bottom} L${dimX + 4},${bottom - 8}` }),
  );
  const ctText = svg("text", { class: "dim-text", x: dimX + 12, y: cy + 2 });
  ctText.textContent = `${fmt(ct, 3)} mm`;
  const ctSub = svg("text", { class: "dim-sub", x: dimX + 12, y: cy + 18 });
  ctSub.textContent = `Soll ${fmt(spec.ct_nominal_mm, 2)} ± ${fmt(spec.ct_tolerance_mm, 2)}`;

  // leader for the form deviation
  const leaderX = cx - half * 0.5;
  const leaderY = yEdgeTop - (yEdgeTop - top) * 0.75;
  root.append(svg("line", { class: "dim", x1: leaderX, y1: leaderY - 6, x2: leaderX - 30, y2: 42 }));
  const pvText = svg("text", { class: "dim-text", x: 8, y: 18 });
  pvText.textContent = `PV ${fmt(pv, 2)} µm`;
  const pvSub = svg("text", { class: "dim-sub", x: 8, y: 34 });
  pvSub.textContent = `max. ${fmt(spec.pv_max_um, 1)} µm, überhöht gezeichnet`;
  root.append(ctText, ctSub, pvText, pvSub);
  box.replaceChildren(root);
  box.setAttribute(
    "aria-label",
    `Querschnitt der Linse aus Zyklus ${last.cycle_id}: Mittendicke ${fmt(ct, 3)} mm, Formabweichung ${fmt(pv, 2)} µm`,
  );

  const rec = state.status.recommendation;
  const unreliable = rec.status === "alarm" && ["prozess", "sensor", "datenfehler"].includes(rec.cause);
  const lensChip = !p
    ? chip("warnung", "keine Vorhersage (Datenfehler)")
    : unreliable
      ? chip("warnung", "Vorhersage unsicher")
      : p.in_spec
      ? chip("ok", "i. O. erwartet")
      : chip("alarm", "n. i. O. erwartet");
  $("lens-chip").replaceChildren(lensChip);

  const m = last.measurement;
  $("lens-values").replaceChildren(
    h("div", {}, h("dt", {}, "Zyklus und Rezept"), h("dd", {}, `${last.cycle_id}, ${last.recipe}`)),
    h("div", {}, h("dt", {}, "Modell"), h("dd", {}, p ? p.model_version : "–")),
    h("div", {}, h("dt", {}, "Vorhersage Mittendicke"), h("dd", {}, p ? `${fmt(p.center_thickness_mm, 3)} mm` : "–")),
    h("div", {}, h("dt", {}, "Vorhersage Formabweichung"), h("dd", {}, p ? `${fmt(p.pv_um, 2)} µm` : "–")),
    ...(m ? [h("div", {}, h("dt", {}, "Gemessen"), h("dd", {}, `${fmt(m.center_thickness_mm, 3)} mm, ${fmt(m.pv_um, 2)} µm`))] : []),
  );
}

// ---- KPIs ------------------------------------------------------------------------

function pct(share) {
  return share === null || share === undefined ? "–" : `${fmt(share * 100, 0)} %`;
}

function renderKpis() {
  const k = state.status.kpis;
  const kpi = (label, value, note) =>
    h("div", { class: "kpi" }, h("span", { class: "label" }, label), h("span", { class: "value" }, value),
      note ? h("span", { class: "note" }, note) : null);
  const share = k.cycles ? k.measured / k.cycles : null;
  $("kpis").replaceChildren(
    kpi("Gepresst", fmt(k.cycles, 0), k.quarantined ? `${k.quarantined} ohne Vorhersage` : "alle bewertet"),
    kpi("Gemessen", fmt(k.measured, 0), share === null ? null : `${pct(share)} der Linsen`),
    kpi("Ausschuss erwartet", pct(k.predicted_scrap_share), "letzte 50 Linsen"),
    kpi("Ausschuss real", pct(k.measured_scrap_share), "letzte 50 gemessen"),
    kpi("Modellfehler", k.mae.pv_um == null ? "–" : `${fmt(k.mae.pv_um, 2)} µm`, "Formabweichung"),
  );
}

// ---- history charts -------------------------------------------------------------------

function historySeries(target, unit, digits) {
  const cycles = state.cycles;
  return [
    { name: "Vorhersage", type: "line", color: "--series-1", unit, digits,
      values: cycles.map((c) => c.prediction?.[target] ?? null) },
    { name: "Messung", type: "dots", color: "--series-2", unit, digits,
      values: cycles.map((c) => c.measurement?.[target] ?? null) },
  ];
}

function historyTable(el, target, unit, digits) {
  const rows = state.cycles.slice(-30).reverse();
  el.replaceChildren(
    h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Zyklus"), h("th", {}, "Rezept"),
        h("th", { class: "num" }, `Vorhersage [${unit}]`), h("th", { class: "num" }, `Messung [${unit}]`),
        h("th", {}, "Status"))),
      h("tbody", {}, ...rows.map((c) =>
        h("tr", {},
          h("td", { class: "num" }, c.cycle_id),
          h("td", {}, c.recipe),
          h("td", { class: "num" }, fmt(c.prediction?.[target], digits)),
          h("td", { class: "num" }, fmt(c.measurement?.[target], digits)),
          h("td", {}, c.status === "ok" ? "ok" : c.status === "warning" ? "Warnung" : "Quarantäne"),
        ))),
    ),
  );
}

function events(target) {
  const perf = state.status.performance[target];
  return perf?.alarm_cycle_id ? [{ x: perf.alarm_cycle_id, label: "Alarm" }] : [];
}

function recipeAt(index) {
  const c = state.cycles[index];
  return c ? `Rezept ${c.recipe}${c.status !== "ok" ? `, ${c.status === "warning" ? "Warnung" : "Quarantäne"}` : ""}` : null;
}

function renderHistory() {
  const { spec } = state.status;
  const x = state.cycles.map((c) => c.cycle_id);
  const pvSeries = historySeries("pv_um", "µm", 2);
  renderLegend($("legend-pv"), pvSeries);
  charts.pv.update({
    x, series: pvSeries, yMin: 0, label: "Formabweichung je Zyklus",
    refs: [{ y: spec.pv_max_um, label: `Grenze ${fmt(spec.pv_max_um, 1)}` }],
    events: events("pv_um"), tooltipExtra: recipeAt,
    emptyText: "Noch keine Zyklen. Die Zelle startet gleich.",
  });
  const ctSeries = historySeries("center_thickness_mm", "mm", 3);
  renderLegend($("legend-ct"), ctSeries);
  charts.ct.update({
    x, series: ctSeries, yDigits: 2, label: "Mittendicke je Zyklus",
    refs: [
      { y: spec.ct_nominal_mm + spec.ct_tolerance_mm, label: `+${fmt(spec.ct_tolerance_mm, 2)}` },
      { y: spec.ct_nominal_mm - spec.ct_tolerance_mm, label: `−${fmt(spec.ct_tolerance_mm, 2)}` },
    ],
    events: events("center_thickness_mm"), tooltipExtra: recipeAt,
    emptyText: "Noch keine Zyklen.",
  });
  historyTable($("table-pv"), "pv_um", "µm", 2);
  historyTable($("table-ct"), "center_thickness_mm", "mm", 3);
}

// ---- monitoring --------------------------------------------------------------------

function renderCusum(chart, legendEl, target) {
  const perf = state.status.performance[target];
  const series = [
    { name: "Teile schlechter als vorhergesagt", type: "line", color: "--series-1", digits: 1,
      values: perf?.cusum_upper ?? [] },
    { name: "Teile besser als vorhergesagt", type: "line", color: "--series-2", digits: 1,
      values: perf?.cusum_lower ?? [] },
  ];
  renderLegend(legendEl, series);
  chart.update({
    x: perf?.cusum_cycle_ids ?? [],
    series, yMin: 0, yDigits: 0, label: "CUSUM-Verlauf",
    yMax: perf ? perf.threshold * 3 : undefined,
    refs: perf ? [{ y: perf.threshold, label: `Schwelle ${fmt(perf.threshold, 0)}` }] : [],
    events: perf?.alarm_cycle_id ? [{ x: perf.alarm_cycle_id, label: "Alarm" }] : [],
    emptyText: perf
      ? `Sammelt die Referenz: ${perf.n} von ${perf.baseline_n} gemessenen Linsen.`
      : "Kein aktives Modell.",
  });
}

function renderMonitoring() {
  const { data_drift: drift, monitoring_since: since } = state.status;
  $("monitoring-since").textContent = since
    ? `Fehlerüberwachung seit ${new Date(since).toLocaleString("de-DE")} (aktives Modell oder letzter Reset)`
    : "";
  renderCusum(charts.cusumPv, $("legend-cusum-pv"), "pv_um");
  renderCusum(charts.cusumCt, $("legend-cusum-ct"), "center_thickness_mm");

  const table = $("drift-table");
  if (!drift.enough_data) {
    table.replaceChildren(
      h("p", { class: "empty" },
        `Sammelt die Referenz: ${drift.reference_collected} von ${drift.reference_target} Zyklen, danach mindestens 30 neue Zyklen.`),
    );
    return;
  }
  const level = { ok: ["ok", "unauffällig"], moderat: ["warnung", "leicht verändert"], signifikant: ["alarm", "Drift"] };
  table.replaceChildren(
    h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Sensor"), h("th", {}, "Merkmal"),
        h("th", { class: "num" }, "Verschiebung"), h("th", { class: "num" }, "PSI"),
        h("th", { class: "num" }, "KS p-Wert"), h("th", {}, "Bewertung"))),
      h("tbody", {}, ...drift.features.map((f) => {
        const [sensor, unit, digits] = SENSOR_NAMES[f.feature] || [f.feature, "", 2];
        const [kind, text] = level[f.level];
        const value = Math.abs(f.shift) < 0.5 * 10 ** -digits ? 0 : f.shift;
        const shift = `${value > 0 ? "+" : ""}${fmt(value, digits)} ${unit}`;
        return h("tr", {},
          h("td", {}, sensor), h("td", {}, f.label),
          h("td", { class: "num" }, shift),
          h("td", { class: "num" }, fmt(f.psi, 2)),
          h("td", { class: "num" }, f.ks_pvalue < 0.001 ? "< 0,001" : fmt(f.ks_pvalue, 3)),
          h("td", {}, chip(kind, text)));
      })),
    ),
    h("p", { class: "muted", style: "font-size: var(--step--1); margin-top: .5rem" },
      `Referenz: Zyklus ${drift.reference_cycles[0]}–${drift.reference_cycles[1]}, verglichen mit den letzten ${drift.window_size} Zyklen. ` +
      "Drift heißt: PSI über 0,25 und KS-Test unter 0,001."),
  );
}

function renderQuality() {
  const rows = [];
  for (const c of [...state.cycles].reverse()) {
    for (const issue of c.issues || []) rows.push([c, issue]);
    if (rows.length >= 15) break;
  }
  const el = $("quality-table");
  if (!rows.length) {
    el.replaceChildren(h("p", { class: "empty" }, "Keine Meldungen in den letzten Zyklen."));
    return;
  }
  el.replaceChildren(
    h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Zyklus"), h("th", {}, "Schwere"), h("th", {}, "Meldung"))),
      h("tbody", {}, ...rows.map(([c, issue]) =>
        h("tr", {},
          h("td", { class: "num" }, h("a", { href: "#", onclick: (e) => { e.preventDefault(); openCycle(c.cycle_id); } }, c.cycle_id)),
          h("td", {}, issue.severity === "error" ? chip("alarm", "Fehler") : chip("warnung", "Warnung")),
          h("td", {}, issue.message)))),
    ),
  );
}

// ---- models -------------------------------------------------------------------------

const MODEL_STATUS = {
  active: ["ok", "aktiv"],
  proposed: ["warnung", "Vorschlag"],
  archived: ["neutral", "archiviert"],
  rejected: ["neutral", "abgelehnt"],
};

function compareBlock(cmp) {
  const rows = [];
  for (const [target, label, unit, scale, digits] of [
    ["pv_um", "Formabweichung", "µm", 1, 2],
    ["center_thickness_mm", "Mittendicke", "µm", 1000, 1],
  ]) {
    const values = [cmp.candidate[target].rmse * scale, cmp.active[target].rmse * scale];
    const max = Math.max(...values) || 1;
    rows.push(h("div", { style: "grid-column: 1 / -1; font-weight: 600; margin-top: .4rem" }, `${label}, Fehler (RMSE)`));
    [["Kandidat", values[0], "var(--series-1)"], [`aktiv (${cmp.active_version})`, values[1], "var(--axis)"]].forEach(
      ([name, v, color]) => {
        rows.push(h("span", {}, name));
        rows.push(h("div", { class: "bar-track" },
          h("div", { class: "bar-fill", style: `width:${(100 * v) / max}%; background:${color}` })));
        rows.push(h("span", { class: "num" }, `${fmt(v, digits)} ${unit}`));
      });
  }
  const candidatePv = cmp.candidate.pv_um.rmse;
  const gain = 1 - candidatePv / (cmp.active.pv_um.rmse || 1);
  let hint = null;
  if (candidatePv > 1.0) {
    hint = "Auch der Kandidat liegt deutlich daneben: Der Prozess verändert sich weiter. " +
      "Erst die Ursache beheben (z. B. das Werkzeug wechseln), dann neu trainieren.";
  } else if (gain < 0.1) {
    hint = "Der Kandidat ist kaum besser als das aktive Modell. Eine Freigabe lohnt sich kaum.";
  }
  return h("div", {},
    hint ? h("p", { style: "font-size: var(--step--1); font-weight: 600; margin-bottom: .4rem" }, hint) : null,
    h("p", { class: "muted", style: "font-size: var(--step--1)" },
      `Verglichen auf den ${cmp.n} neuesten gemessenen Linsen (Zyklus ${cmp.holdout_cycles[0]}–${cmp.holdout_cycles[1]}), die keines der Modelle beim Training gesehen hat.`),
    h("div", { class: "compare" }, ...rows));
}

function modelCard(card) {
  const weights = Object.entries(card.weights || {}).map(([target, w]) =>
    h("div", {}, h("strong", {}, card.targets[target]),
      h("ul", {}, ...Object.entries(w)
        .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
        .slice(0, 6)
        .map(([name, value]) => h("li", {}, `${name}: ${fmt(value, 3)}`)))));
  const ranges = Object.entries(card.validity_range).map(([name, [lo, hi]]) =>
    h("li", {}, `${name}: ${fmt(lo, 1)} bis ${fmt(hi, 1)}`));
  const sources = Object.entries(card.training.sources || {})
    .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join("–") : v}`).join(", ");
  return h("details", {},
    h("summary", {}, "Modellkarte"),
    h("div", { class: "card-grid" },
      h("div", {}, h("strong", {}, "Was es tut"), h("p", {}, card.description), h("p", { class: "muted" }, card.intended_use)),
      h("div", {}, h("strong", {}, "Training"),
        h("p", {}, `${card.training.n_samples} Beispiele, trainiert ${new Date(card.training.trained_at).toLocaleString("de-DE")}`),
        h("p", { class: "muted" }, sources),
        h("p", { class: "hash" }, `Datensatz-Hash ${card.training.data_hash}`)),
      h("div", {}, h("strong", {}, "Gewichte bzw. Wichtigkeit"), ...weights),
      h("div", {}, h("strong", {}, "Gültigkeitsbereich"), h("ul", {}, ...ranges)),
      h("div", { style: "grid-column: 1 / -1" }, h("strong", {}, "Grenzen"),
        h("ul", {}, ...card.limitations.map((l) => h("li", {}, l)))),
    ));
}

function renderModels() {
  const container = $("models");
  if (!state.models.length) {
    container.replaceChildren(h("p", { class: "empty" }, "Noch keine Modelle."));
    return;
  }
  const ordered = [...state.models].sort((a, b) => {
    const rank = { proposed: 0, active: 1, archived: 2, rejected: 3 };
    return rank[a.status] - rank[b.status] || b.version.localeCompare(a.version);
  });
  container.replaceChildren(...ordered.map((m) => {
    const [kind, text] = MODEL_STATUS[m.status];
    const holdout = m.metrics;
    const buttons = [];
    if (m.status === "proposed") {
      buttons.push(
        h("button", { class: "primary", type: "button", disabled: REPLAY, onclick: () => decide({
          title: `${m.version} freigeben`,
          text: "Das Modell wird sofort für alle neuen Linsen verwendet. Die bisherige Version bleibt für einen Rollback erhalten.",
          confirm: "Freigeben", path: `/api/models/${m.version}/activate`, done: (r) => `${r.version} ist aktiv`,
        }) }, "Freigeben"),
        h("button", { class: "secondary", type: "button", disabled: REPLAY, onclick: () => decide({
          title: `${m.version} ablehnen`, text: "Der Vorschlag wird verworfen und kann nicht mehr aktiviert werden.",
          confirm: "Ablehnen", path: `/api/models/${m.version}/reject`, done: (r) => `${r.version} abgelehnt`,
        }) }, "Ablehnen"),
      );
    } else if (m.status === "archived") {
      buttons.push(h("button", { class: "secondary", type: "button", disabled: REPLAY, onclick: () => decide({
        title: `${m.version} aktivieren`, text: "Diese ältere Version wird wieder aktiv.",
        confirm: "Aktivieren", path: `/api/models/${m.version}/activate`, done: (r) => `${r.version} ist aktiv`,
      }) }, "Aktivieren"));
    }
    return h("article", { class: "model", "data-status": m.status },
      h("div", { class: "model-head" }, h("h3", {}, m.version), chip(kind, text),
        h("span", { class: "model-meta" }, m.card.title)),
      h("p", { class: "model-meta" },
        `Fehler auf zurückgehaltenen Trainingsdaten: Formabweichung ${fmt(holdout.pv_um.rmse, 2)} µm, ` +
        `Mittendicke ${fmt(holdout.center_thickness_mm.rmse * 1000, 1)} µm` +
        (m.parent_version ? `. Nachfolger von ${m.parent_version}` : "") +
        (m.activated_at ? `. Zuletzt aktiviert ${new Date(m.activated_at).toLocaleString("de-DE")}` : "")),
      m.metrics.comparison ? compareBlock(m.metrics.comparison) : null,
      buttons.length ? h("div", { class: "button-row" }, ...buttons) : null,
      modelCard(m.card),
    );
  }));
}

// ---- audit ----------------------------------------------------------------------------

function details(obj) {
  return Object.entries(obj || {})
    .filter(([, v]) => v !== null && v !== undefined)
    .map(([k, v]) => {
      if (typeof v !== "object") return `${k}: ${typeof v === "number" ? fmt(v, Number.isInteger(v) ? 0 : 3) : v}`;
      const inner = Object.entries(v).map(([ik, iv]) => `${ik} ${typeof iv === "number" ? fmt(iv, 3) : iv}`);
      return `${k}: ${inner.join(", ")}`;
    })
    .join(", ");
}

function renderAudit() {
  const el = $("audit-table");
  if (!state.audit.length) {
    el.replaceChildren(h("p", { class: "empty" }, "Noch keine Einträge."));
    return;
  }
  el.replaceChildren(
    h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Zeit"), h("th", {}, "Wer"), h("th", {}, "Was"),
        h("th", {}, "Begründung"), h("th", {}, "Details"))),
      h("tbody", {}, ...state.audit.map((e) =>
        h("tr", {},
          h("td", { class: "num" }, new Date(e.ts).toLocaleString("de-DE")),
          h("td", {}, e.actor),
          h("td", {}, ACTION_LABELS[e.action] || e.action),
          h("td", {}, e.reason || "–"),
          h("td", { class: "muted" }, details(e.details))))),
    ),
  );
}

// ---- simulator ---------------------------------------------------------------------------

function renderSim() {
  const el = $("sim-state");
  const buttons = document.querySelectorAll("#sim-buttons button");
  if (!state.sim) {
    el.replaceChildren(h("p", { class: "empty" },
      `Simulator nicht erreichbar (${state.simError || "unbekannt"}). In einer echten Anlage fehlt dieser Teil.`));
    buttons.forEach((b) => (b.disabled = true));
    return;
  }
  buttons.forEach((b) => {
    b.disabled = REPLAY;
    b.title = REPLAY ? REPLAY_NOTE : "";
  });
  const s = state.sim;
  const item = (label, value, note) =>
    h("div", {}, h("span", { class: "muted" }, label), h("strong", {}, value), note ? h("span", { class: "muted" }, note) : null);
  el.replaceChildren(
    item("Zyklus", fmt(s.cycle, 0), `Rezept ${s.recipe}`),
    item("Werkzeugverschleiß", `${fmt(s.tool_wear_um, 2)} µm`,
      s.tool_wear_rate_um_per_cycle ? `wächst um ${fmt(s.tool_wear_rate_um_per_cycle, 3)} µm je Zyklus` : "stabil"),
    item("Versatz Thermoelement oben", `${fmt(s.sensor_offset_upper_c, 1)} °C`,
      `Ziel ${fmt(s.sensor_offset_target_c, 0)} °C`),
  );
}

document.querySelectorAll("#sim-buttons button").forEach((button) => {
  button.addEventListener("click", async () => {
    const value = button.dataset.value ? Number(button.dataset.value) : null;
    try {
      await postJSON("/api/sim", { action: button.dataset.sim, value });
      toast(`${button.textContent}: an die Zelle gesendet`);
      await refreshSlow();
    } catch (error) {
      toast(error.message);
    }
  });
});

// ---- cycle detail ---------------------------------------------------------------------------

async function openCycle(cycleId) {
  const dialog = $("cycle-dialog");
  $("cycle-title").textContent = `Zyklus ${cycleId}`;
  $("cycle-body").replaceChildren(h("p", { class: "empty" }, "Lade …"));
  if (!dialog.open) dialog.showModal();
  let c;
  try {
    c = await getJSON(`/api/cycles/${cycleId}`);
  } catch (error) {
    $("cycle-body").replaceChildren(h("p", { class: "empty" }, error.message));
    return;
  }
  const p = c.prediction;
  const m = c.measurement;
  const curve = (values, rate) => values.map((_, i) => i / rate);
  const furnace = h("div", { class: "chart" });
  const tools = h("div", { class: "chart" });
  const force = h("div", { class: "chart" });
  const toolLegend = h("ul", { class: "legend" });
  const params = c.params;
  $("cycle-body").replaceChildren(...[
    h("dl", { class: "lens-values", style: "grid-template-columns: repeat(4, minmax(0,1fr))" },
      h("div", {}, h("dt", {}, "Rezept"), h("dd", {}, c.recipe)),
      h("div", {}, h("dt", {}, "Vorhersage"), h("dd", {}, p ? `${fmt(p.center_thickness_mm, 3)} mm, ${fmt(p.pv_um, 2)} µm` : "–")),
      h("div", {}, h("dt", {}, "Messung"), h("dd", {}, m ? `${fmt(m.center_thickness_mm, 3)} mm, ${fmt(m.pv_um, 2)} µm` : "nicht gemessen")),
      h("div", {}, h("dt", {}, "Modell"), h("dd", {}, p ? p.model_version : "–"))),
    p ? h("p", { class: "hash", style: "margin-top:.5rem" }, `Eingangsdaten-Hash ${p.input_hash}`) : null,
    c.issues.length ? h("ul", {}, ...c.issues.map((i) => h("li", {}, i.message))) : null,
    h("p", { class: "muted", style: "margin: .75rem 0" },
      `Sollwerte: Heizzeit ${fmt(params.heating_time_s, 0)} s, Presszeit ${fmt(params.press_time_s, 1)} s, ` +
      `Presskraft ${fmt(params.press_force_n, 0)} N, Werkzeuge ${fmt(params.temp_upper_c, 0)} / ${fmt(params.temp_lower_c, 0)} °C, ` +
      `Glasposten ${fmt(c.gob_mass_mg, 1)} mg`),
    h("div", { class: "grid-2" },
      h("div", {}, h("h3", {}, "Werkzeugtemperaturen beim Pressen"), toolLegend, tools),
      h("div", {}, h("h3", {}, "Presskraft"), force)),
    h("div", { style: "margin-top: 1.25rem" }, h("h3", {}, "Ofentemperatur beim Aufheizen"), furnace),
  ].filter(Boolean));
  const cur = c.curves;
  const toolSeries = [
    { name: "oben", type: "line", color: "--series-1", unit: "°C", digits: 1, values: cur.tool_temp_upper_c },
    { name: "unten", type: "line", color: "--series-2", unit: "°C", digits: 1, values: cur.tool_temp_lower_c },
  ];
  renderLegend(toolLegend, toolSeries);
  new LineChart(tools, { height: 200, xLabel: "Sekunde", xDigits: 1 }).update({
    x: curve(cur.tool_temp_upper_c, cur.pressing_rate_hz), series: toolSeries, label: "Werkzeugtemperaturen",
  });
  new LineChart(force, { height: 200, xLabel: "Sekunde", xDigits: 1 }).update({
    x: curve(cur.force_n, cur.pressing_rate_hz), yMin: 0, label: "Presskraft",
    series: [{ name: "Kraft", type: "line", color: "--series-1", unit: "N", digits: 0, values: cur.force_n }],
  });
  new LineChart(furnace, { height: 180, xLabel: "Sekunde", xDigits: 0 }).update({
    x: curve(cur.furnace_temp_c, cur.heating_rate_hz), label: "Ofentemperatur",
    series: [{ name: "Ofen", type: "line", color: "--series-1", unit: "°C", digits: 1, values: cur.furnace_temp_c }],
  });
}

// ---- theme -------------------------------------------------------------------------------

function currentTheme() {
  const set = document.documentElement.dataset.theme;
  if (set) return set;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function labelThemeButton() {
  $("theme-toggle").textContent = currentTheme() === "dark" ? "Hell" : "Dunkel";
}

$("theme-toggle").addEventListener("click", () => {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  storage("presswatch-theme", next);
  labelThemeButton();
  if (state.status) renderFast();
});
labelThemeButton();

// ---- start --------------------------------------------------------------------------------

window.presswatchRefresh = () => Promise.all([refresh(), refreshSlow()]);

refresh();
refreshSlow();
setInterval(refresh, 2000);
setInterval(refreshSlow, 5000);
