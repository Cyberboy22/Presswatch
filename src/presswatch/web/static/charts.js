// Small SVG line/dot chart with crosshair, tooltip and keyboard support.
// Marks: 2px lines, 8px dots with a 2px surface ring, hairline grid, one y-axis.

const NS = "http://www.w3.org/2000/svg";

export function svg(tag, attrs = {}) {
  const el = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
  return el;
}

const formatters = new Map();
export function fmt(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  if (!formatters.has(digits)) {
    formatters.set(
      digits,
      new Intl.NumberFormat("de-DE", { minimumFractionDigits: digits, maximumFractionDigits: digits }),
    );
  }
  return formatters.get(digits).format(value);
}

function niceStep(range, count) {
  const raw = range / Math.max(count, 1);
  const power = 10 ** Math.floor(Math.log10(raw));
  const unit = raw / power;
  const nice = unit < 1.5 ? 1 : unit < 3 ? 2 : unit < 7 ? 5 : 10;
  return nice * power;
}

export function niceTicks(min, max, count = 5) {
  if (min === max) {
    const pad = Math.abs(min) * 0.1 || 1;
    min -= pad;
    max += pad;
  }
  const step = niceStep(max - min, count);
  const start = Math.floor(min / step) * step;
  const ticks = [];
  for (let v = start; v <= max + step * 0.5; v += step) ticks.push(Number(v.toFixed(10)));
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
  return { ticks, step };
}

function decimalsFor(step) {
  return Math.max(0, -Math.floor(Math.log10(step) + 1e-9));
}

function key(series) {
  const k = svg("svg", { width: 16, height: 10, "aria-hidden": "true" });
  if (series.type === "dots") {
    k.append(svg("circle", { cx: 8, cy: 5, r: 4, fill: `var(${series.color})` }));
  } else {
    k.append(
      svg("line", {
        x1: 1, y1: 5, x2: 15, y2: 5,
        stroke: `var(${series.color})`, "stroke-width": 2, "stroke-linecap": "round",
      }),
    );
  }
  return k;
}

export function renderLegend(list, series) {
  list.replaceChildren(
    ...series.map((s) => {
      const li = document.createElement("li");
      li.append(key(s), document.createTextNode(s.name));
      return li;
    }),
  );
}

export class LineChart {
  constructor(container, { height = 240, onPick = null, xLabel = "Zyklus", xDigits = 0 } = {}) {
    this.el = container;
    this.height = height;
    this.onPick = onPick;
    this.xLabel = xLabel;
    this.xDigits = xDigits;
    this.data = null;
    this.index = null;
    this.tooltip = document.createElement("div");
    this.tooltip.className = "tooltip";
    this.tooltip.hidden = true;
    this.lastWidth = 0;
    new ResizeObserver(() => {
      if (this.el.clientWidth !== this.lastWidth) this.render();
    }).observe(this.el);
  }

  update(data) {
    this.data = data;
    this.render();
  }

  render() {
    const data = this.data;
    if (!data) return;
    const width = this.el.clientWidth || 600;
    this.lastWidth = width;
    if (!data.x.length || data.series.every((s) => s.values.every((v) => v == null))) {
      const p = document.createElement("p");
      p.className = "empty";
      p.textContent = data.emptyText || "Noch keine Daten.";
      this.el.replaceChildren(p);
      return;
    }

    const m = { top: 20, right: 70, bottom: 30, left: 52 };
    const h = this.height;
    const values = data.series.flatMap((s) => s.values.filter((v) => v != null));
    (data.refs || []).forEach((r) => values.push(r.y));
    let lo = Math.min(...values);
    let hi = Math.max(...values);
    if (data.yMin !== undefined) lo = Math.min(lo, data.yMin);
    const pad = (hi - lo) * 0.08 || Math.abs(hi) * 0.05 || 1;
    if (data.yMin === undefined) lo -= pad;
    hi += pad;
    let clipped = false;
    if (data.yMax !== undefined && hi > data.yMax) {
      hi = data.yMax;
      clipped = true;
    }
    const { ticks: yTicks, step: yStep } = niceTicks(lo, hi, 4);
    lo = yTicks[0];
    hi = yTicks[yTicks.length - 1];
    const yDigits = data.yDigits ?? decimalsFor(yStep);

    const x0 = data.x[0];
    const x1 = data.x[data.x.length - 1] === x0 ? x0 + 1 : data.x[data.x.length - 1];
    const sx = (v) => m.left + ((v - x0) / (x1 - x0)) * (width - m.left - m.right);
    const sy = (v) => m.top + (1 - (Math.min(Math.max(v, lo), hi) - lo) / (hi - lo)) * (h - m.top - m.bottom);
    this.sx = sx;
    this.sy = sy;

    const root = svg("svg", {
      viewBox: `0 0 ${width} ${h}`,
      height: h,
      tabindex: 0,
      role: "img",
      "aria-label": data.label || "Diagramm",
    });

    const grid = svg("g", { class: "grid" });
    const axis = svg("g", { class: "axis" });
    for (const t of yTicks) {
      grid.append(svg("line", { x1: m.left, x2: width - m.right, y1: sy(t), y2: sy(t) }));
      const label = svg("text", { x: m.left - 8, y: sy(t) + 4, "text-anchor": "end" });
      label.textContent = fmt(t, yDigits);
      const tick = svg("g", { class: "tick" });
      tick.append(label);
      axis.append(tick);
    }
    axis.append(svg("line", { x1: m.left, x2: width - m.right, y1: h - m.bottom, y2: h - m.bottom }));
    const { ticks: xTicks, step: xStep } = niceTicks(x0, x1, Math.max(2, Math.floor(width / 110)));
    const xDigits = Math.max(this.xDigits, decimalsFor(xStep));
    for (const t of xTicks) {
      if (t < x0 || t > x1) continue;
      const tick = svg("g", { class: "tick" });
      const label = svg("text", { x: sx(t), y: h - m.bottom + 18, "text-anchor": "middle" });
      label.textContent = fmt(t, xDigits);
      tick.append(label);
      axis.append(tick);
    }
    root.append(grid, axis);
    if (clipped) {
      const note = svg("text", { x: width - m.right, y: m.top - 6, "text-anchor": "end", class: "clip-note" });
      note.textContent = `Werte über ${fmt(hi, 0)} oben abgeschnitten`;
      root.append(note);
    }

    for (const ref of data.refs || []) {
      const g = svg("g", { class: "ref" });
      g.append(svg("line", { x1: m.left, x2: width - m.right, y1: sy(ref.y), y2: sy(ref.y) }));
      const t = svg("text", { x: width - m.right + 6, y: sy(ref.y) + 4 });
      t.textContent = ref.label;
      g.append(t);
      root.append(g);
    }

    for (const ev of data.events || []) {
      if (ev.x < x0 || ev.x > x1) continue;
      const g = svg("g", { class: "event" });
      g.append(svg("line", { x1: sx(ev.x), x2: sx(ev.x), y1: m.top - 4, y2: h - m.bottom }));
      const t = svg("text", { x: sx(ev.x) + 4, y: m.top - 6 });
      t.textContent = ev.label;
      g.append(t);
      root.append(g);
    }

    for (const s of data.series) {
      if (s.type === "dots") {
        const g = svg("g");
        s.values.forEach((v, i) => {
          if (v == null) return;
          g.append(
            svg("circle", { class: "dot", cx: sx(data.x[i]), cy: sy(v), r: 4, fill: `var(${s.color})` }),
          );
        });
        root.append(g);
      } else {
        let d = "";
        let pen = false;
        s.values.forEach((v, i) => {
          if (v == null) {
            pen = false;
            return;
          }
          d += `${pen ? "L" : "M"}${sx(data.x[i]).toFixed(1)},${sy(v).toFixed(1)}`;
          pen = true;
        });
        root.append(svg("path", { class: "line", d, stroke: `var(${s.color})` }));
      }
    }

    this.crosshair = svg("line", { class: "crosshair", y1: m.top, y2: h - m.bottom, visibility: "hidden" });
    this.focusDots = svg("g", { "pointer-events": "none" });
    const hit = svg("rect", {
      class: "hit",
      x: m.left - 6,
      y: m.top,
      width: width - m.left - m.right + 12,
      height: h - m.top - m.bottom,
    });
    root.append(this.crosshair, this.focusDots, hit);

    hit.addEventListener("pointermove", (event) => {
      const box = root.getBoundingClientRect();
      const px = ((event.clientX - box.left) / box.width) * width;
      this.show(this.nearest(px));
    });
    hit.addEventListener("pointerleave", () => this.hide());
    hit.addEventListener("click", () => {
      if (this.onPick && this.index !== null) this.onPick(data.x[this.index]);
    });
    root.addEventListener("keydown", (event) => {
      const last = data.x.length - 1;
      if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
        const start = this.index ?? last;
        this.show(Math.min(last, Math.max(0, start + (event.key === "ArrowRight" ? 1 : -1))));
        event.preventDefault();
      } else if (event.key === "Enter" && this.onPick && this.index !== null) {
        this.onPick(data.x[this.index]);
      } else if (event.key === "Escape") {
        this.hide();
      }
    });
    root.addEventListener("blur", () => this.hide());

    this.el.replaceChildren(root, this.tooltip);
    if (this.index !== null && this.index < data.x.length && !this.tooltip.hidden) this.show(this.index);
  }

  nearest(px) {
    const xs = this.data.x;
    let lo = 0;
    let hi = xs.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (this.sx(xs[mid]) < px) lo = mid;
      else hi = mid;
    }
    return Math.abs(this.sx(xs[lo]) - px) <= Math.abs(this.sx(xs[hi]) - px) ? lo : hi;
  }

  show(index) {
    const data = this.data;
    this.index = index;
    const x = this.sx(data.x[index]);
    this.crosshair.setAttribute("x1", x);
    this.crosshair.setAttribute("x2", x);
    this.crosshair.setAttribute("visibility", "visible");
    this.focusDots.replaceChildren();

    const head = document.createElement("div");
    head.className = "t-head";
    head.textContent = `${this.xLabel} ${fmt(data.x[index], this.xDigits)}`;
    const rows = data.series.map((s) => {
      const v = s.values[index];
      if (v != null) {
        this.focusDots.append(
          svg("circle", { class: "dot", cx: x, cy: this.sy(v), r: 5, fill: `var(${s.color})` }),
        );
      }
      const row = document.createElement("div");
      row.className = "t-row";
      const strong = document.createElement("strong");
      strong.textContent = v == null ? "–" : `${fmt(v, s.digits ?? 2)}${s.unit ? ` ${s.unit}` : ""}`;
      const name = document.createElement("span");
      name.textContent = s.name;
      row.append(key(s), strong, name);
      return row;
    });
    const extra = data.tooltipExtra ? data.tooltipExtra(index) : null;
    const nodes = [head, ...rows];
    if (extra) {
      const e = document.createElement("div");
      e.className = "t-head";
      e.style.marginTop = "0.25rem";
      e.textContent = extra;
      nodes.push(e);
    }
    this.tooltip.replaceChildren(...nodes);
    this.tooltip.hidden = false;
    const box = this.el.getBoundingClientRect();
    const scale = box.width / (this.lastWidth || box.width);
    const left = x * scale;
    const tipWidth = this.tooltip.offsetWidth;
    this.tooltip.style.left = `${left + 12 + tipWidth > box.width ? left - tipWidth - 12 : left + 12}px`;
    this.tooltip.style.top = "8px";
  }

  hide() {
    this.tooltip.hidden = true;
    if (this.crosshair) this.crosshair.setAttribute("visibility", "hidden");
    if (this.focusDots) this.focusDots.replaceChildren();
  }
}
