/*
 * GEO & MTA Sandbox: interactive UI.
 * Security posture: no HTML-string DOM sinks, no inline handlers, no dynamic code, no network access.
 * All untrusted text goes through textContent; the page ships with a hash-based CSP.
 */
(function () {
  "use strict";

  const E = window.GeoMtaEngine;
  const DATA = JSON.parse(document.getElementById("seed-data").textContent);
  const $ = (id) => document.getElementById(id);

  // ------------------------------------------------------------------ DOM helpers
  function h(tag, attrs, ...kids) {
    const node = document.createElement(tag);
    applyAttrs(node, attrs);
    append(node, kids);
    return node;
  }
  function s(tag, attrs, ...kids) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    applyAttrs(node, attrs);
    append(node, kids);
    return node;
  }
  function applyAttrs(node, attrs) {
    if (!attrs) return;
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "text") node.textContent = v;
      else if (k === "on") for (const [ev, fn] of Object.entries(v)) node.addEventListener(ev, fn);
      else if (k === "tip") bindTip(node, v);
      else if (k.startsWith("on") || k === "style") throw new Error("inline handlers/styles are not allowed");
      else node.setAttribute(k, v === true ? "" : String(v));
    }
  }
  function append(node, kids) {
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      node.appendChild(typeof kid === "string" || typeof kid === "number" ? document.createTextNode(String(kid)) : kid);
    }
  }
  const clear = (node) => node.replaceChildren();

  // ------------------------------------------------------------------ formatting
  const pct = (x, d = 1) => `${(100 * x).toFixed(d)}%`;
  const num = (x, d = 3) => (Number.isFinite(x) ? x.toFixed(d) : "∞");
  const usd = (x) => `$${Math.round(x).toLocaleString("en-US")}`;
  const usdCompact = (x) => (Math.abs(x) >= 1000 ? `$${(x / 1000).toFixed(Math.abs(x) >= 10000 ? 0 : 1)}K` : `$${Math.round(x)}`);
  const signed = (x, f) => (x >= 0 ? "+" : "−") + f(Math.abs(x));
  const sci = (x) => (x === 0 ? "0" : x.toExponential(1));

  function readThemeIsDark() {
    const forced = document.documentElement.getAttribute("data-theme");
    if (forced) return forced === "dark";
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }
  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  // ------------------------------------------------------------------ tooltip
  const tooltip = $("tooltip");
  function bindTip(node, rows) {
    const show = (x, y) => {
      clear(tooltip);
      rows().forEach(([label, value], i) => {
        if (i === 0) tooltip.appendChild(h("strong", { text: label }));
        else tooltip.appendChild(h("div", null, `${label}: `, h("b", { text: value })));
      });
      tooltip.hidden = false;
      const r = tooltip.getBoundingClientRect();
      const left = Math.min(window.innerWidth - r.width - 8, Math.max(8, x + 14));
      const top = y - r.height - 12 < 8 ? y + 16 : y - r.height - 12;
      tooltip.style.left = `${left}px`;
      tooltip.style.top = `${top}px`;
    };
    node.addEventListener("pointermove", (e) => show(e.clientX, e.clientY));
    node.addEventListener("pointerleave", () => { tooltip.hidden = true; });
    node.addEventListener("focus", () => { const b = node.getBoundingClientRect(); show(b.left + b.width / 2, b.top); });
    node.addEventListener("blur", () => { tooltip.hidden = true; });
    if (!node.hasAttribute("tabindex")) node.setAttribute("tabindex", "0");
  }

  // ------------------------------------------------------------------ chart primitives
  const RAMP = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"];
  function luminance(hex) {
    const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  }
  function rampColor(t) {
    const ramp = readThemeIsDark() ? [...RAMP].reverse() : RAMP;
    const idx = Math.max(0, Math.min(ramp.length - 1, Math.round(t * (ramp.length - 1))));
    const bg = ramp[idx];
    return { bg, fg: luminance(bg) < 0.33 ? "#ffffff" : "#0b0b0b" };
  }

  function roundedBar(x0, y, w, hgt, dir = 1, r = 4) {
    const width = Math.abs(w);
    if (width < 0.5) return "";
    r = Math.min(r, width, hgt / 2);
    if (dir > 0) {
      return `M${x0},${y}H${x0 + width - r}Q${x0 + width},${y} ${x0 + width},${y + r}V${y + hgt - r}Q${x0 + width},${y + hgt} ${x0 + width - r},${y + hgt}H${x0}Z`;
    }
    return `M${x0},${y}H${x0 - width + r}Q${x0 - width},${y} ${x0 - width},${y + r}V${y + hgt - r}Q${x0 - width},${y + hgt} ${x0 - width + r},${y + hgt}H${x0}Z`;
  }
  function niceTicks(max, count = 4) {
    if (max <= 0) return [0];
    const raw = max / count;
    const mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((st) => st >= raw) || raw;
    const ticks = [];
    for (let v = 0; v <= max + 1e-9; v += step) ticks.push(+v.toFixed(10));
    return ticks;
  }
  function chartWidth(container) { return Math.max(300, Math.floor(container.clientWidth || 600)); }
  function measureLabelWidth(labels, minW = 90, maxW = 170) {
    const longest = Math.max(...labels.map((l) => String(l).length), 4);
    return Math.max(minW, Math.min(maxW, longest * 7.2 + 12));
  }

  /** Horizontal bars from a zero baseline. rows: {label, value, color, valueText, tip} */
  function hbarChart(container, rows, opts = {}) {
    clear(container);
    const W = chartWidth(container);
    const rowH = 34, barH = 18, top = 6;
    const labelW = measureLabelWidth(rows.map((r) => r.label));
    const valueW = opts.valueW || 64;
    const plotW = W - labelW - valueW;
    const max = opts.max || Math.max(...rows.map((r) => r.value), 1e-9);
    const H = top + rows.length * rowH + 4;
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": opts.aria || "Bar chart" });
    for (const t of niceTicks(max, 4)) {
      const x = labelW + (t / max) * plotW;
      if (x > labelW + plotW + 0.5) continue;
      svg.appendChild(s("line", { class: "gridline", x1: x, x2: x, y1: top - 4, y2: H - 2 }));
    }
    rows.forEach((r, i) => {
      const y = top + i * rowH + (rowH - barH) / 2;
      const w = (Math.max(0, r.value) / max) * plotW;
      const g = s("g", { class: "mark", tip: r.tip });
      g.appendChild(s("rect", { class: "hit", x: 0, y: top + i * rowH, width: W, height: rowH }));
      g.appendChild(s("text", { x: labelW - 10, y: y + barH / 2 + 4, "text-anchor": "end", class: "label-text", text: r.label }));
      g.appendChild(s("path", { d: roundedBar(labelW, y, w, barH), fill: r.color }));
      g.appendChild(s("text", { x: labelW + w + 8, y: y + barH / 2 + 4, class: "value-text", text: r.valueText }));
      svg.appendChild(g);
    });
    svg.appendChild(s("line", { class: "baseline", x1: labelW, x2: labelW, y1: top - 4, y2: H - 2 }));
    container.appendChild(svg);
  }

  /** Diverging horizontal bars around zero. rows: {label, value, valueText, tip} */
  function divergingChart(container, rows, opts = {}) {
    clear(container);
    const W = chartWidth(container);
    const rowH = 34, barH = 18, top = 6;
    const labelW = measureLabelWidth(rows.map((r) => r.label));
    const pad = 60;
    const plotW = W - labelW - 2 * pad;
    const maxAbs = Math.max(...rows.map((r) => Math.abs(r.value)), 1e-9);
    const zero = labelW + pad + plotW / 2;
    const H = top + rows.length * rowH + 4;
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": opts.aria || "Diverging bar chart" });
    rows.forEach((r, i) => {
      const y = top + i * rowH + (rowH - barH) / 2;
      const w = (Math.abs(r.value) / maxAbs) * (plotW / 2);
      const dir = r.value >= 0 ? 1 : -1;
      const g = s("g", { class: "mark", tip: r.tip });
      g.appendChild(s("rect", { class: "hit", x: 0, y: top + i * rowH, width: W, height: rowH }));
      g.appendChild(s("text", { x: labelW - 10, y: y + barH / 2 + 4, "text-anchor": "end", class: "label-text", text: r.label }));
      g.appendChild(s("path", { d: roundedBar(zero, y, w * dir, barH, dir), fill: dir > 0 ? cssVar("--pos") : cssVar("--neg") }));
      g.appendChild(s("text", {
        x: zero + dir * (w + 6), y: y + barH / 2 + 4, "text-anchor": dir > 0 ? "start" : "end", class: "value-text", text: r.valueText,
      }));
      svg.appendChild(g);
    });
    svg.appendChild(s("line", { class: "baseline", x1: zero, x2: zero, y1: top - 4, y2: H - 2 }));
    container.appendChild(svg);
  }

  /** Bars with whiskers for an interval. rows: {label, value, lo, hi, tip} */
  function intervalChart(container, rows, opts = {}) {
    clear(container);
    const W = chartWidth(container);
    const rowH = 34, barH = 16, top = 6;
    const labelW = measureLabelWidth(rows.map((r) => r.label));
    const valueW = 120;
    const plotW = W - labelW - valueW;
    const max = Math.max(...rows.map((r) => Math.max(r.value, r.hi ?? r.value)), 1e-9) * 1.05;
    const H = top + rows.length * rowH + 4;
    const sx = (v) => labelW + (v / max) * plotW;
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": opts.aria || "Interval chart" });
    for (const t of niceTicks(max, 4)) svg.appendChild(s("line", { class: "gridline", x1: sx(t), x2: sx(t), y1: top - 4, y2: H - 2 }));
    const ink = cssVar("--ink");
    rows.forEach((r, i) => {
      const y = top + i * rowH + (rowH - barH) / 2;
      const cy = y + barH / 2;
      const g = s("g", { class: "mark", tip: r.tip });
      g.appendChild(s("rect", { class: "hit", x: 0, y: top + i * rowH, width: W, height: rowH }));
      g.appendChild(s("text", { x: labelW - 10, y: cy + 4, "text-anchor": "end", class: "label-text", text: r.label }));
      g.appendChild(s("path", { d: roundedBar(labelW, y, sx(r.value) - labelW, barH), fill: cssVar("--s1") }));
      let textX = sx(r.value) + 8;
      if (r.lo !== undefined && r.hi !== undefined) {
        g.appendChild(s("line", { x1: sx(r.lo), x2: sx(r.hi), y1: cy, y2: cy, stroke: ink, "stroke-width": 1.5 }));
        for (const v of [r.lo, r.hi]) g.appendChild(s("line", { x1: sx(v), x2: sx(v), y1: cy - 6, y2: cy + 6, stroke: ink, "stroke-width": 1.5 }));
        textX = sx(r.hi) + 8;
      }
      g.appendChild(s("text", { x: textX, y: cy + 4, class: "value-text", text: r.valueText }));
      svg.appendChild(g);
    });
    svg.appendChild(s("line", { class: "baseline", x1: labelW, x2: labelW, y1: top - 4, y2: H - 2 }));
    container.appendChild(svg);
  }

  /** Dumbbell: rows {label, from, to, tip}. */
  function dumbbellChart(container, rows, opts = {}) {
    clear(container);
    const W = chartWidth(container);
    const rowH = 36, top = 8, bottom = 26;
    const labelW = measureLabelWidth(rows.map((r) => r.label));
    const valueW = 70;
    const plotW = W - labelW - valueW - 10;
    const max = Math.max(...rows.flatMap((r) => [r.from, r.to]), 1) * 1.05;
    const sx = (v) => labelW + 6 + (v / max) * plotW;
    const H = top + rows.length * rowH + bottom;
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": opts.aria || "Dumbbell chart" });
    const surface = cssVar("--surface");
    for (const t of niceTicks(max, 4)) {
      svg.appendChild(s("line", { class: "gridline", x1: sx(t), x2: sx(t), y1: top - 4, y2: H - bottom + 4 }));
      svg.appendChild(s("text", { class: "axis-text", x: sx(t), y: H - 8, "text-anchor": "middle", text: usdCompact(t) }));
    }
    rows.forEach((r, i) => {
      const cy = top + i * rowH + rowH / 2;
      const g = s("g", { class: "mark", tip: r.tip });
      g.appendChild(s("rect", { class: "hit", x: 0, y: top + i * rowH, width: W, height: rowH }));
      g.appendChild(s("text", { x: labelW - 4, y: cy + 4, "text-anchor": "end", class: "label-text", text: r.label }));
      g.appendChild(s("line", { x1: sx(r.from), x2: sx(r.to), y1: cy, y2: cy, stroke: cssVar("--axis"), "stroke-width": 2, "stroke-linecap": "round" }));
      g.appendChild(s("circle", { cx: sx(r.from), cy, r: 5, fill: cssVar("--deemph"), stroke: surface, "stroke-width": 2 }));
      g.appendChild(s("circle", { cx: sx(r.to), cy, r: 6, fill: cssVar("--s1"), stroke: surface, "stroke-width": 2 }));
      g.appendChild(s("text", { x: Math.max(sx(r.from), sx(r.to)) + 12, y: cy + 4, class: "value-text", text: usdCompact(r.to) }));
      svg.appendChild(g);
    });
    container.appendChild(svg);
  }

  /** Bars measured from a reference value (lambda*). rows {label, value, tip} */
  function referenceChart(container, rows, ref, opts = {}) {
    clear(container);
    const W = chartWidth(container);
    const rowH = 32, barH = 16, top = 22, bottom = 22;
    const labelW = measureLabelWidth(rows.map((r) => r.label));
    const valueW = 56;
    const plotW = W - labelW - valueW;
    const finite = rows.map((r) => r.value).filter(Number.isFinite);
    const max = Math.max(ref, ...finite, 1e-9) * 1.1;
    const sx = (v) => labelW + (Math.min(v, max) / max) * plotW;
    const H = top + rows.length * rowH + bottom;
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": opts.aria || "Reference chart" });
    for (const t of niceTicks(max, 4)) {
      svg.appendChild(s("line", { class: "gridline", x1: sx(t), x2: sx(t), y1: top - 4, y2: H - bottom + 2 }));
      svg.appendChild(s("text", { class: "axis-text", x: sx(t), y: H - 6, "text-anchor": "middle", text: t.toFixed(t < 1 ? 2 : 1) }));
    }
    const x0 = sx(ref);
    rows.forEach((r, i) => {
      const y = top + i * rowH + (rowH - barH) / 2;
      const val = Number.isFinite(r.value) ? r.value : max;
      const dir = val >= ref ? 1 : -1;
      const g = s("g", { class: "mark", tip: r.tip });
      g.appendChild(s("rect", { class: "hit", x: 0, y: top + i * rowH, width: W, height: rowH }));
      g.appendChild(s("text", { x: labelW - 10, y: y + barH / 2 + 4, "text-anchor": "end", class: "label-text", text: r.label }));
      g.appendChild(s("path", { d: roundedBar(x0, y, sx(val) - x0, barH, dir), fill: dir > 0 ? cssVar("--pos") : cssVar("--neg") }));
      g.appendChild(s("text", { x: dir > 0 ? sx(val) + 6 : x0 + 6, y: y + barH / 2 + 4, class: "value-text", text: num(r.value, 2) }));
      svg.appendChild(g);
    });
    svg.appendChild(s("line", { x1: x0, x2: x0, y1: top - 10, y2: H - bottom + 2, stroke: cssVar("--ink"), "stroke-width": 1.5 }));
    svg.appendChild(s("text", { x: x0, y: top - 12, "text-anchor": "middle", class: "value-text", text: `λ* = ${ref.toFixed(3)}` }));
    container.appendChild(svg);
  }

  /** Line chart of y = f(x) over integer x with a highlighted point. */
  function decayChart(container, selected) {
    clear(container);
    const W = chartWidth(container), H = 190;
    const left = 40, right = 16, top = 14, bottom = 30;
    const xs = Array.from({ length: 10 }, (_, i) => i + 1);
    const sx = (x) => left + ((x - 1) / 9) * (W - left - right);
    const sy = (y) => top + (1 - y) * (H - top - bottom);
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": "Position decay by citation rank" });
    for (const t of [0, 0.25, 0.5, 0.75, 1]) {
      svg.appendChild(s("line", { class: "gridline", x1: left, x2: W - right, y1: sy(t), y2: sy(t) }));
      svg.appendChild(s("text", { class: "axis-text", x: left - 8, y: sy(t) + 4, "text-anchor": "end", text: t.toFixed(2) }));
    }
    xs.forEach((x) => svg.appendChild(s("text", { class: "axis-text", x: sx(x), y: H - 10, "text-anchor": "middle", text: `#${x}` })));
    const s1 = cssVar("--s1");
    const d = xs.map((x, i) => `${i ? "L" : "M"}${sx(x)},${sy(E.positionDecay(x))}`).join("");
    svg.appendChild(s("path", { d: `${d}L${sx(10)},${sy(0)}L${sx(1)},${sy(0)}Z`, fill: s1, "fill-opacity": 0.1 }));
    svg.appendChild(s("path", { d, fill: "none", stroke: s1, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    svg.appendChild(s("line", { class: "baseline", x1: left, x2: W - right, y1: sy(0), y2: sy(0) }));
    xs.forEach((x) => {
      const y = E.positionDecay(x);
      const g = s("g", { class: "mark", tip: () => [[`Rank #${x}`], ["Decay", y.toFixed(4)], ["Visibility vs #1", pct(y)]] });
      g.appendChild(s("rect", { class: "hit", x: sx(x) - 14, y: top, width: 28, height: H - top - bottom }));
      g.appendChild(s("circle", { cx: sx(x), cy: sy(y), r: x === selected ? 6 : 3.5, fill: s1, stroke: cssVar("--surface"), "stroke-width": 2 }));
      if (x === selected) g.appendChild(s("text", { x: sx(x) + 10, y: sy(y) - 8, class: "value-text", text: y.toFixed(3) }));
      svg.appendChild(g);
    });
    container.appendChild(svg);
  }

  function dataTable(container, headers, rows) {
    clear(container);
    const table = h("table", { class: "table" },
      h("thead", null, h("tr", null, headers.map((x) => h("th", { scope: "col", text: x })))),
      h("tbody", null, rows.map((r) => h("tr", null, r.map((c, i) => h("td", { class: i === 0 ? "label-cell" : null, text: c }))))));
    container.appendChild(h("div", { class: "table-wrap" }, table));
  }

  function kpi(label, value, sub, subClass) {
    return h("div", { class: "kpi" },
      h("div", { class: "kpi-label", text: label }),
      h("div", { class: "kpi-value", text: value }),
      sub ? h("div", { class: "kpi-sub" }, subClass ? h("span", { class: subClass, text: sub }) : sub) : null);
  }

  // ------------------------------------------------------------------ state
  const SEED = DATA.records;
  const CHANNEL_ORDER = DATA.channels;
  const DEFAULT_CUSTOM = [
    { p: ["AI Search", "Organic Tech Blog", "Direct"], c: 1, n: 12, v: 12 * 200 },
    { p: ["AI Search", "Paid Social"], c: 1, n: 8, v: 8 * 150 },
    { p: ["Paid Social", "Direct"], c: 1, n: 10, v: 10 * 180 },
    { p: ["Virtual Influencer", "AI Search", "Direct"], c: 1, n: 6, v: 6 * 300 },
    { p: ["Organic Tech Blog", "Paid Social"], c: 0, n: 9, v: 0 },
    { p: ["Paid Social"], c: 0, n: 20, v: 0 },
    { p: ["Virtual Influencer"], c: 0, n: 7, v: 0 },
    { p: ["AI Search"], c: 0, n: 8, v: 0 },
  ];
  const state = {
    dataset: "seed", order: 1, halfLife: 2, bootstrap: true, compare: "Last-Click",
    custom: DEFAULT_CUSTOM.map((r) => ({ ...r, p: [...r.p] })), customVersion: 0, draft: ["AI Search", "Direct"],
    schema: "Product",
  };
  const MODEL_ORDER = ["Last-Click", "First-Touch", "Linear", "Position-Based", "Time-Decay", "Markov", "Shapley"];
  const pipelineCache = new Map();
  const bootstrapCache = new Map();

  function currentRecords() { return state.dataset === "seed" ? SEED : state.custom; }
  function pipeline(records, order, halfLife, key) {
    const cacheKey = `${key}|${order}|${halfLife}`;
    if (!pipelineCache.has(cacheKey)) {
      const t0 = performance.now();
      const res = E.runPipeline(records, { order, halfLife });
      res.ms = performance.now() - t0;
      pipelineCache.set(cacheKey, res);
    }
    return pipelineCache.get(cacheKey);
  }
  const datasetKey = () => (state.dataset === "seed" ? "seed" : `custom-${state.customVersion}`);

  // ------------------------------------------------------------------ hero
  function renderHero() {
    const t0 = performance.now();
    const res = E.runPipeline(SEED, { order: 1, halfLife: 2 });
    const opt = budgetFor(res, DATA.defaults.budget, 0.5, 0.5);
    const ms = performance.now() - t0;
    const lc = res.divergence["Last-Click"];
    clear($("hero-kpis"));
    $("hero-kpis").append(
      kpi("Customer journeys", res.totals.journeys.toLocaleString("en-US"), `${res.channels.length} channels · ${res.totals.conversions} conversions`),
      kpi("Conversion probability", pct(res.base), "Markov chain = observed rate (flow conservation)"),
      kpi("Credit Last-Click misplaces", pct(lc.tv), `JSD ${lc.jsd.toFixed(3)} bits vs Markov`),
      kpi("Projected revenue lift", signed(opt.liftPct / 100, (x) => pct(x)), "optimal reallocation, β 0.5, δ 0.5"),
      kpi("Full recompute", `${ms.toFixed(ms < 10 ? 1 : 0)} ms`, "7 models + optimizer, in your browser"),
    );
  }

  // ------------------------------------------------------------------ GEO
  const COMPETITORS = [
    { name: "VortexAI", rate: 0.55, rank: 2, sent: 0.35 },
    { name: "OmniAgent", rate: 0.48, rank: 3, sent: 0.2 },
    { name: "HyperDev", rate: 0.3, rank: 3, sent: -0.1 },
  ];
  function renderGeo() {
    const brandRaw = $("geo-brand").value;
    const brand = E.sanitizeText(brandRaw).slice(0, 64) || "Target brand";
    const M = +$("geo-prompts").value, rate = +$("geo-rate").value, rank = +$("geo-rank").value, sent = +$("geo-sent").value;
    $("geo-prompts-out").textContent = String(M);
    $("geo-rate-out").textContent = pct(rate, 0);
    $("geo-rank-out").textContent = `#${rank}`;
    $("geo-sent-out").textContent = `${sent >= 0 ? "+" : ""}${sent.toFixed(2)}`;

    const decay = E.positionDecay(rank), mult = E.sentimentMultiplier(sent);
    const brands = [{ name: brand, rate, rank, sent, target: true }, ...COMPETITORS];
    brands.forEach((b) => { b.som = E.expectedSom(b.rate, b.rank, b.sent); });
    const total = brands.reduce((a, b) => a + b.som, 0);
    brands.forEach((b) => { b.sov = total > 0 ? b.som / total : 0; });
    const target = brands[0];
    const k = Math.round(rate * M);
    const [lo, hi] = E.wilson(k, M);
    const hhiVal = E.hhi(brands.map((b) => b.sov));

    clear($("geo-kpis"));
    $("geo-kpis").append(
      kpi("Share of Model", target.som.toFixed(3), `normalized ${pct(target.som / E.SOM_MAX)}`),
      kpi("Share of Voice", pct(target.sov), `rank ${[...brands].sort((a, b) => b.som - a.som).indexOf(target) + 1} of ${brands.length}`),
      kpi("Citation rate 95% CI", `${pct(lo, 0)} – ${pct(hi, 0)}`, `Wilson, ${k}/${M} cited`),
      kpi("Market HHI", Math.round(hhiVal).toLocaleString("en-US"), hhiVal > 2500 ? "highly concentrated" : hhiVal > 1500 ? "moderately concentrated" : "unconcentrated"),
    );

    const f = $("geo-formula");
    clear(f);
    f.append(
      h("div", null, "SoM = rate × 1/log₂(rank+1) × (1 + 0.5·s)"),
      h("div", null, "    = ", h("b", { text: rate.toFixed(2) }), " × ", h("b", { text: decay.toFixed(3) }), " × ", h("b", { text: mult.toFixed(2) }), " = ", h("b", { text: target.som.toFixed(4) })),
      h("div", null, "SoV = SoM / ΣSoM = ", h("b", { text: pct(target.sov) }), "   HHI = 10000·ΣSoV² = ", h("b", { text: Math.round(hhiVal).toString() })),
    );

    const sorted = [...brands].sort((a, b) => b.som - a.som);
    hbarChart($("geo-chart"), sorted.map((b) => ({
      label: b.target ? `${b.name} (you)` : b.name,
      value: b.som,
      color: b.target ? cssVar("--s1") : cssVar("--deemph"),
      valueText: b.som.toFixed(3),
      tip: () => [[b.name], ["SoM", b.som.toFixed(4)], ["Share of voice", pct(b.sov)], ["Citation rate", pct(b.rate, 0)], ["Rank", `#${b.rank}`], ["Sentiment", b.sent.toFixed(2)]],
    })), { aria: "Share of Model by brand" });
    dataTable($("geo-table"), ["Brand", "Citation rate", "Rank", "Sentiment", "SoM", "Share of voice"],
      sorted.map((b) => [b.name, pct(b.rate, 0), `#${b.rank}`, b.sent.toFixed(2), b.som.toFixed(4), pct(b.sov)]));
    decayChart($("decay-chart"), rank);
  }

  // ------------------------------------------------------------------ Attribution
  function orderedChannels(res) {
    return [...res.channels].sort((a, b) => res.models.Markov[b] - res.models.Markov[a]);
  }

  function renderBuilder() {
    $("builder").hidden = state.dataset !== "custom";
    if (state.dataset !== "custom") return;
    const palette = $("builder-palette");
    if (!palette.childElementCount) {
      CHANNEL_ORDER.forEach((ch) => palette.appendChild(h("button", {
        type: "button", class: "chip", text: `+ ${ch}`,
        on: { click: () => { if (state.draft.length < 25) { state.draft.push(ch); renderBuilder(); } } },
      })));
    }
    const path = $("builder-path");
    clear(path);
    state.draft.forEach((ch, i) => {
      path.appendChild(h("span", { class: "arrow", "aria-hidden": "true", text: "→" }));
      path.appendChild(h("button", { type: "button", class: "step", text: ch, "aria-label": `Remove step ${i + 1}: ${ch}`, on: { click: () => { state.draft.splice(i, 1); renderBuilder(); } } }));
    });
    path.appendChild(h("span", { class: "arrow", "aria-hidden": "true", text: "→" }));
    const converted = $("builder-converted").checked;
    $("builder-end").textContent = converted ? "Conversion" : "No conversion";
    $("builder-end").className = `node ${converted ? "node-end-conv" : "node-end-null"}`;

    const table = $("builder-table");
    clear(table);
    table.append(h("caption", { class: "sr-only", text: "Custom journeys" }),
      h("thead", null, h("tr", null, ["Path", "Outcome", "Count", "Revenue", ""].map((x) => h("th", { scope: "col", text: x })))),
      h("tbody", null, state.custom.map((r, i) => h("tr", null,
        h("td", { class: "label-cell", text: r.p.join(" → ") }),
        h("td", null, h("span", { class: `status ${r.c ? "status-up" : "status-down"}`, text: r.c ? "Converted" : "Dropped" })),
        h("td", { text: String(r.n) }),
        h("td", { text: r.c ? usd(r.v) : "–" }),
        h("td", null, h("button", { type: "button", class: "remove-btn", text: "Remove", "aria-label": `Remove journey ${i + 1}`, on: { click: () => { state.custom.splice(i, 1); state.customVersion++; renderAttributionAndBudget(); } } })),
      ))));
  }

  function renderAttribution() {
    renderBuilder();
    const records = currentRecords();
    const kpis = $("attr-kpis");
    clear(kpis);
    const totals = E.totals(records);
    if (!records.length || totals.conversions === 0 || E.channelsOf(records).length < 1) {
      kpis.append(kpi("Not enough data", "Add a converting journey", "Attribution needs at least one conversion"));
      ["model-heatmap", "divergence-table", "transition-matrix"].forEach((id) => clear($(id)));
      ["shift-chart", "ci-chart"].forEach((id) => clear($(id)));
      return;
    }
    const res = pipeline(records, state.order, state.halfLife, datasetKey());
    const channels = orderedChannels(res);
    const lc = res.divergence["Last-Click"];
    kpis.append(
      kpi("P(conversion) from chain", pct(res.base), `observed ${pct(res.empirical)} · exact match`),
      kpi("Expected path length", `${res.pathLength.toFixed(2)} touches`, `order k = ${res.order} · ${res.model.states.length - 1} states`),
      kpi("Last-Click vs Markov", pct(lc.tv), `of credit moves · JSD ${lc.jsd.toFixed(3)} bits`),
      kpi("Compute time", `${res.ms.toFixed(res.ms < 10 ? 1 : 0)} ms`, `${totals.journeys.toLocaleString("en-US")} journeys, 7 models`),
    );

    // Heatmap
    const hm = $("model-heatmap");
    clear(hm);
    const maxShare = Math.max(...MODEL_ORDER.flatMap((m) => channels.map((c) => res.models[m][c])));
    hm.append(h("caption", { class: "sr-only", text: "Attribution share per channel and model" }),
      h("thead", null, h("tr", null, h("th", { scope: "col", text: "Channel" }), MODEL_ORDER.map((m) => h("th", { scope: "col", text: m })))),
      h("tbody", null, channels.map((ch) => h("tr", null,
        h("th", { scope: "row", class: "label-cell", text: ch }),
        MODEL_ORDER.map((m) => {
          const v = res.models[m][ch];
          const { bg, fg } = rampColor(v / maxShare);
          const td = h("td", { class: "cell", text: pct(v), tabindex: "-1", tip: () => [[`${ch} · ${m}`], ["Share", pct(v, 2)], ["Revenue credited", usd(v * res.revenue)]] });
          td.style.background = bg;
          td.style.color = fg;
          return td;
        })))));

    // Credit shift
    const sel = $("compare-model");
    if (!sel.childElementCount) {
      MODEL_ORDER.filter((m) => m !== "Markov").forEach((m) => sel.appendChild(h("option", { value: m, text: m })));
      sel.value = state.compare;
    }
    const shiftRows = channels.map((ch) => {
      const d = res.models.Markov[ch] - res.models[state.compare][ch];
      return {
        label: ch, value: d, valueText: `${d >= 0 ? "+" : "−"}${(Math.abs(d) * 100).toFixed(1)} pp`,
        tip: () => [[ch], ["Markov", pct(res.models.Markov[ch])], [state.compare, pct(res.models[state.compare][ch])], ["Revenue shift", signed(d * res.revenue, usd)]],
      };
    }).sort((a, b) => b.value - a.value);
    divergingChart($("shift-chart"), shiftRows, { aria: `Credit shift Markov minus ${state.compare}` });
    dataTable($("shift-table"), ["Channel", "Markov", state.compare, "Difference"],
      shiftRows.map((r) => [r.label, pct(res.models.Markov[r.label]), pct(res.models[state.compare][r.label]), r.valueText]));

    // CI chart
    const samples = { 1: 200, 2: 100, 3: 40 }[state.order];
    let ci = {};
    if (state.bootstrap) {
      const key = `${datasetKey()}|${state.order}`;
      if (!bootstrapCache.has(key)) bootstrapCache.set(key, E.bootstrapMarkov(records, res.channels, state.order, samples));
      ci = bootstrapCache.get(key);
      $("ci-note").textContent = `Percentile interval from ${samples} multinomial resamples of the journeys (order k = ${state.order}).`;
    } else {
      $("ci-note").textContent = "Bootstrap disabled: showing point estimates only.";
    }
    const ciRows = channels.map((ch) => ({
      label: ch, value: res.models.Markov[ch], lo: ci[ch] ? ci[ch][0] : undefined, hi: ci[ch] ? ci[ch][1] : undefined,
      valueText: ci[ch] ? `${pct(res.models.Markov[ch])} [${pct(ci[ch][0], 0)}–${pct(ci[ch][1], 0)}]` : pct(res.models.Markov[ch]),
      tip: () => [[ch], ["Markov share", pct(res.models.Markov[ch], 2)], ["Removal effect R_k", res.effects[ch].toFixed(4)], ["95% CI", ci[ch] ? `${pct(ci[ch][0], 1)} – ${pct(ci[ch][1], 1)}` : "n/a"], ["E[visits]/journey", res.visits[ch].toFixed(3)]],
    }));
    intervalChart($("ci-chart"), ciRows, { aria: "Markov share with bootstrap interval" });
    dataTable($("ci-table"), ["Channel", "Removal effect", "Markov share", "CI low", "CI high"],
      ciRows.map((r) => [r.label, res.effects[r.label].toFixed(4), pct(r.value, 2), r.lo !== undefined ? pct(r.lo, 2) : "–", r.hi !== undefined ? pct(r.hi, 2) : "–"]));

    // Divergence
    const dv = $("divergence-table");
    clear(dv);
    const divRows = Object.entries(res.divergence).sort((a, b) => b[1].tv - a[1].tv);
    dv.append(h("caption", { class: "sr-only", text: "Divergence from Markov" }),
      h("thead", null, h("tr", null, ["Model", "TV", "JSD (bits)"].map((x) => h("th", { scope: "col", text: x })))),
      h("tbody", null, divRows.map(([m, d]) => h("tr", null, h("td", { class: "label-cell", text: m }), h("td", { text: pct(d.tv) }), h("td", { text: d.jsd.toFixed(4) })))));

    renderMatrix(res, channels);
  }

  function renderMatrix(res, channels) {
    const table = $("transition-matrix");
    clear(table);
    const T = res.model.states.length, W = T + 2;
    if (res.order === 1) {
      $("matrix-title").textContent = "Transition matrix (order 1)";
      $("matrix-hint").textContent = "Row-stochastic probabilities P(next | current). Rows sum to 1.";
      const idx = new Map(res.model.states.map((st, i) => [st.join("|"), i]));
      const rowsStates = ["(start)", ...channels];
      const cols = [...channels, "conv", "null"];
      const colIdx = (c) => (c === "conv" ? T : c === "null" ? T + 1 : idx.get(c));
      table.append(h("caption", { class: "sr-only", text: "Transition probabilities" }),
        h("thead", null, h("tr", null, h("th", { scope: "col", text: "From \\ To" }), cols.map((c) => h("th", { scope: "col", text: c === "conv" ? "Conv" : c === "null" ? "Null" : abbreviate(c) })))),
        h("tbody", null, rowsStates.filter((r) => idx.has(r)).map((r) => {
          const i = idx.get(r);
          return h("tr", null, h("th", { scope: "row", class: "label-cell", text: r === "(start)" ? "Start" : r }),
            cols.map((c) => {
              const j = colIdx(c);
              const p = j === undefined ? 0 : res.P[i * W + j];
              const { bg, fg } = rampColor(p);
              const td = h("td", { class: "cell", text: p > 0 ? p.toFixed(2) : "·", tabindex: "-1", tip: () => [[`${r === "(start)" ? "Start" : r} → ${c}`], ["Probability", p.toFixed(4)]] });
              if (p > 0) { td.style.background = bg; td.style.color = fg; }
              return td;
            }));
        })));
    } else {
      $("matrix-title").textContent = `Top transitions (order ${res.order})`;
      $("matrix-hint").textContent = "Higher-order states remember the last k touches; showing the transitions that carry the most journeys.";
      const flows = new Map();
      for (const [ri, a, b] of res.model.trans) {
        const key = `${a}>${b}`;
        flows.set(key, (flows.get(key) || 0) + res.model.weights[ri]);
      }
      const name = (i) => (i === -1 ? "Conversion" : i === -2 ? "Null" : res.model.states[i].map(abbreviate).join(" › "));
      const top = [...flows.entries()].sort((x, y) => y[1] - x[1]).slice(0, 14);
      table.append(h("caption", { class: "sr-only", text: "Top transitions" }),
        h("thead", null, h("tr", null, ["From state", "To", "Journeys", "P(to | from)"].map((x) => h("th", { scope: "col", text: x })))),
        h("tbody", null, top.map(([key, w]) => {
          const [a, b] = key.split(">").map(Number);
          const col = b === -1 ? T : b === -2 ? T + 1 : b;
          return h("tr", null, h("td", { class: "label-cell", text: name(a) }), h("td", { text: name(b) }), h("td", { text: String(Math.round(w)) }), h("td", { text: res.P[a * W + col].toFixed(3) }));
        })));
    }
  }
  function abbreviate(c) {
    const map = { "AI Search": "AI", "Virtual Influencer": "Infl.", "Organic Tech Blog": "Blog", "Paid Social": "Social", "Email Newsletter": "Email", "Organic Search": "SEO", Direct: "Direct", "(start)": "Start" };
    return map[c] || c;
  }

  // ------------------------------------------------------------------ Budget
  function budgetFor(res, budget, beta, trust) {
    const attributed = Object.fromEntries(res.channels.map((c) => [c, res.models.Markov[c] * res.revenue]));
    const spend = Object.fromEntries(res.channels.map((c) => [c, DATA.spends[c] ?? 5000]));
    return { ...E.optimizeBudget(attributed, spend, budget, beta, trust), attributed, spend };
  }
  function renderBudget() {
    const budget = +$("budget-total").value, beta = +$("budget-beta").value, trust = +$("budget-trust").value;
    $("budget-total-out").textContent = usd(budget);
    $("budget-beta-out").textContent = beta.toFixed(2);
    $("budget-trust-out").textContent = `±${Math.round(trust * 100)}%`;
    const res = pipeline(SEED, state.order, state.halfLife, "seed");
    const opt = budgetFor(res, budget, beta, trust);
    const channels = orderedChannels(res);
    clear($("budget-kpis"));
    $("budget-kpis").append(
      kpi("Revenue at current mix", usd(opt.revenueNow), budget === DATA.defaults.budget ? "calibrated to Markov credit" : "current mix, scaled to budget"),
      kpi("Revenue at optimum", usd(opt.revenueOpt), signed(opt.liftPct / 100, pct), opt.liftPct >= 0 ? "up" : "down"),
      kpi("Equilibrium mROAS λ*", opt.lambda.toFixed(3), "marginal $ return at the optimum"),
    );
    const scale = budget / Object.values(opt.spend).reduce((a, b) => a + b, 0);
    dumbbellChart($("dumbbell-chart"), channels.map((c) => ({
      label: c, from: opt.spend[c] * scale, to: opt.allocation[c],
      tip: () => [[c], ["Current (scaled)", usd(opt.spend[c] * scale)], ["Optimal", usd(opt.allocation[c])], ["Change", signed(opt.allocation[c] - opt.spend[c] * scale, usd)], ["Bounds", `${usd(opt.lo[c])} – ${usd(opt.hi[c])}`]],
    })), { aria: "Current versus optimal spend" });
    referenceChart($("mroas-chart"), channels.map((c) => ({
      label: c, value: opt.mroasNow[c],
      tip: () => [[c], ["mROAS today", num(opt.mroasNow[c])], ["mROAS at optimum", num(opt.mroasOpt[c])], ["λ*", opt.lambda.toFixed(3)]],
    })), opt.lambda, { aria: "Marginal ROAS compared with equilibrium" });

    const table = $("budget-table");
    clear(table);
    table.append(h("caption", { class: "sr-only", text: "Budget recommendation per channel" }),
      h("thead", null, h("tr", null, ["Channel", "Spend now", "Optimal", "Change", "Markov revenue", "ROAS", "mROAS now", "mROAS opt.", "CPA", "Status"].map((x) => h("th", { scope: "col", text: x })))),
      h("tbody", null, channels.map((c) => {
        const now = opt.spend[c] * scale;
        const delta = opt.allocation[c] - now;
        const conv = res.models.Markov[c] * res.totals.conversions;
        const m = opt.mroasNow[c];
        const status = m > opt.lambda * 1.15 ? ["Under-invested", "status-up"] : m < opt.lambda * 0.85 ? ["Over-invested", "status-down"] : ["Near equilibrium", "status-eq"];
        return h("tr", null,
          h("td", { class: "label-cell", text: c }), h("td", { text: usd(now) }), h("td", { text: usd(opt.allocation[c]) }),
          h("td", { text: signed(delta, usd) }), h("td", { text: usd(opt.attributed[c]) }),
          h("td", { text: `${(opt.attributed[c] / opt.spend[c]).toFixed(2)}×` }), h("td", { text: num(m) }), h("td", { text: num(opt.mroasOpt[c]) }),
          h("td", { text: conv > 0 ? usd(opt.spend[c] / conv) : "–" }),
          h("td", null, h("span", { class: `status ${status[1]}`, text: status[0] })));
      })));
  }

  function renderAttributionAndBudget() { renderAttribution(); renderBudget(); }

  // ------------------------------------------------------------------ JSON-LD
  const SCHEMA_FIELDS = {
    Product: [
      ["name", "Product name", "TesserPulse AI Attribution Engine"], ["brand", "Brand", "TesserPulse"],
      ["sku", "SKU", "TP-ENT-2026"], ["price", "Price", "499"], ["currency", "Currency (ISO 4217)", "USD"],
      ["rating", "Rating (1–5)", "4.8"], ["url", "Product URL (https)", "https://github.com/samuelhtampubolon/geo-multitouch-attribution-sandbox", "full"],
      ["description", "Description", "Enterprise multi-touch attribution and generative engine optimization platform.", "full", "textarea"],
    ],
    FAQPage: [
      ["url", "Page URL (https)", "https://samuelhtampubolon.github.io/geo-multitouch-attribution-sandbox/", "full"],
      ["q1", "Question 1", "What is Share of Model in Generative Engine Optimization?", "full"],
      ["a1", "Answer 1", "Share of Model measures how often and how prominently AI engines cite a brand, discounted by citation rank and scaled by sentiment.", "full", "textarea"],
      ["q2", "Question 2", "Why is Last-Click attribution flawed?", "full"],
      ["a2", "Answer 2", "It gives 100% of the credit to the final touch, ignoring upper-funnel AI answers, content and influencers that created the demand.", "full", "textarea"],
    ],
    Organization: [
      ["name", "Organization name", "TesserPulse Technologies"], ["logo", "Logo URL (optional)", ""],
      ["url", "Website (https)", "https://samuelhtampubolon.github.io/geo-multitouch-attribution-sandbox/", "full"],
      ["description", "Description", "Research lab for Generative Engine Optimization and algorithmic marketing attribution.", "full", "textarea"],
      ["sameAs", "sameAs links (one per line)", "https://github.com/samuelhtampubolon", "full", "textarea"],
    ],
  };
  const SCHEMA_TIPS = {
    Product: ["Include aggregateRating and a stable SKU so answer engines can quote a concrete, verifiable entity.", "Match brand and product names exactly to the entity names users ask AI engines about.", "Describe features plainly; retrieval-augmented engines extract short factual sentences."],
    FAQPage: ["Phrase questions the way users ask AI assistants; direct Q&A pairs map onto answer snippets.", "Keep answers to roughly 40–90 words with a clear conclusion in the first sentence.", "Only mark up FAQs that are visible on the page."],
    Organization: ["List authoritative profiles (Wikidata, GitHub, LinkedIn) in sameAs to consolidate entity identity.", "Keep the organization name identical across registries and your own site.", "Add a logo URL that is publicly reachable over HTTPS."],
  };
  const formValues = {};

  function renderSchemaForm() {
    document.querySelectorAll("[data-schema]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.schema === state.schema)));
    const form = $("jsonld-form");
    clear(form);
    for (const [key, label, def, full, kind] of SCHEMA_FIELDS[state.schema]) {
      const id = `ld-${state.schema}-${key}`;
      const stored = formValues[id] ?? def;
      const input = kind === "textarea"
        ? h("textarea", { id, rows: key === "sameAs" ? 3 : 3, maxlength: 2000, spellcheck: "false" })
        : h("input", { id, type: "text", maxlength: 2048, spellcheck: "false", autocomplete: "off" });
      input.value = stored;
      input.addEventListener("input", () => { formValues[id] = input.value; renderJsonLd(); });
      form.appendChild(h("div", { class: `field${full ? " full" : ""}` }, h("label", { for: id, text: label }), input));
    }
    renderJsonLd();
  }

  function renderJsonLd() {
    const val = (key) => {
      const field = SCHEMA_FIELDS[state.schema].find((f) => f[0] === key);
      const id = `ld-${state.schema}-${key}`;
      return formValues[id] ?? (field ? field[2] : "");
    };
    const checks = [];
    const clean = (key, label) => {
      const raw = val(key);
      const out = E.sanitizeText(raw);
      if (out !== raw.trim()) checks.push(["warn", `${label}: markup or invisible characters neutralized`]);
      return out;
    };
    const urlOk = (key, label, optional) => {
      const raw = val(key).trim();
      if (!raw && optional) return null;
      const r = E.ssrfCheck(raw);
      const failed = r.steps.find((st) => st.ok === false);
      if (r.verdict === "blocked") { checks.push(["bad", `${label} rejected: ${failed.rule} (${failed.detail})`]); return undefined; }
      checks.push([r.verdict === "allowed" ? "ok" : "info", `${label} passed SSRF rules${r.verdict === "needs-dns" ? "; DNS answers are verified server-side" : ""}`]);
      return new URL(raw).href;
    };

    let obj = null;
    if (state.schema === "Product") {
      const url = urlOk("url", "Product URL");
      const price = Number(val("price"));
      const rating = Number(val("rating"));
      const currency = val("currency").trim().toUpperCase();
      if (!(price > 0)) checks.push(["bad", "Price must be a positive number"]);
      if (!/^[A-Z]{3}$/.test(currency)) checks.push(["bad", "Currency must be a 3-letter ISO 4217 code"]);
      if (!(rating >= 1 && rating <= 5)) checks.push(["bad", "Rating must lie between 1 and 5"]);
      obj = {
        "@context": "https://schema.org", "@type": "Product",
        name: clean("name", "Name"), description: clean("description", "Description"), sku: clean("sku", "SKU"),
        brand: { "@type": "Brand", name: clean("brand", "Brand") },
        offers: { "@type": "Offer", url, priceCurrency: currency, price: Math.round(price * 100) / 100, availability: "https://schema.org/InStock", itemCondition: "https://schema.org/NewCondition" },
        aggregateRating: { "@type": "AggregateRating", ratingValue: Math.round(rating * 10) / 10, reviewCount: 120 },
      };
      if (url === undefined) obj = null;
    } else if (state.schema === "FAQPage") {
      const url = urlOk("url", "Page URL");
      obj = url === undefined ? null : {
        "@context": "https://schema.org", "@type": "FAQPage", url,
        mainEntity: [["q1", "a1"], ["q2", "a2"]].map(([q, a], i) => ({
          "@type": "Question", name: clean(q, `Question ${i + 1}`),
          acceptedAnswer: { "@type": "Answer", text: clean(a, `Answer ${i + 1}`) },
        })),
      };
    } else {
      const url = urlOk("url", "Website");
      const logo = urlOk("logo", "Logo URL", true);
      const links = val("sameAs").split(/\n+/).map((x) => x.trim()).filter(Boolean).slice(0, 10);
      const sameAs = [];
      for (const [i, l] of links.entries()) {
        const r = E.ssrfCheck(l);
        if (r.verdict === "blocked") checks.push(["bad", `sameAs #${i + 1} rejected: ${r.steps.find((st) => st.ok === false).detail}`]);
        else sameAs.push(new URL(l).href);
      }
      obj = url === undefined || logo === undefined ? null : {
        "@context": "https://schema.org", "@type": "Organization",
        name: clean("name", "Name"), url, description: clean("description", "Description"), sameAs,
      };
      if (obj && logo) obj.logo = logo;
    }

    const hasError = checks.some((c) => c[0] === "bad") || !obj;
    const out = $("jsonld-output");
    out.textContent = hasError
      ? "// Generation blocked: fix the failing checks above.\n// The API returns HTTP 400 for the same input."
      : `<script type="application/ld+json">\n${E.safeJsonForScript(obj)}\n<\/script>`;
    if (!hasError) checks.unshift(["ok", "Valid JSON; <, > and & escaped for safe <script> embedding"]);

    const list = $("jsonld-checks");
    clear(list);
    const icons = { ok: "✓", bad: "✕", warn: "!", info: "i" };
    checks.forEach(([kind, text]) => list.appendChild(h("li", { class: kind === "ok" ? "ok" : kind === "bad" ? "bad" : "info" }, h("span", { class: "ico", "aria-hidden": "true", text: icons[kind] }), h("span", { text }))));
    const tips = $("jsonld-tips");
    clear(tips);
    SCHEMA_TIPS[state.schema].forEach((t) => tips.appendChild(h("li", { text: t })));
  }

  // ------------------------------------------------------------------ SSRF
  const SSRF_PRESETS = [
    ["Cloud metadata", "https://169.254.169.254/latest/meta-data/"],
    ["Decimal loopback", "https://2130706433/admin"],
    ["Hex loopback", "https://0x7f.0.0.1/"],
    ["IPv4-mapped IPv6", "https://[::ffff:169.254.169.254]/"],
    ["NAT64 smuggling", "https://[64:ff9b::a9fe:a9fe]/"],
    ["Userinfo trick", "https://trusted.example@127.0.0.1/"],
    ["GCP metadata host", "https://metadata.google.internal/computeMetadata/v1/"],
    ["CGNAT range", "https://100.64.0.1/"],
    ["Plain HTTP", "http://example.com/"],
    ["Odd port", "https://example.com:8443/"],
    ["Public IP literal", "https://140.82.112.3/"],
    ["Public hostname", "https://github.com/samuelhtampubolon"],
  ];
  function renderSsrf() {
    const r = E.ssrfCheck($("ssrf-input").value);
    const verdict = $("ssrf-verdict");
    clear(verdict);
    const failed = r.steps.find((st) => st.ok === false);
    if (r.verdict === "blocked") {
      verdict.className = "verdict verdict-blocked";
      verdict.append(h("span", { "aria-hidden": "true", text: "⛔" }), h("span", { text: `Blocked · HTTP 400 · ${failed.rule}: ${failed.detail}` }));
    } else if (r.verdict === "allowed") {
      verdict.className = "verdict verdict-allowed";
      verdict.append(h("span", { "aria-hidden": "true", text: "✅" }), h("span", { text: "Allowed · public HTTPS destination" }));
    } else {
      verdict.className = "verdict verdict-dns";
      verdict.append(h("span", { "aria-hidden": "true", text: "🔎" }), h("span", { text: "Passes static rules · final decision after server-side DNS check" }));
    }
    const trace = $("ssrf-trace");
    clear(trace);
    r.steps.forEach((st) => trace.appendChild(h("li", { class: st.ok === true ? "ok" : st.ok === false ? "bad" : "info" },
      h("span", { class: "ico", "aria-hidden": "true", text: st.ok === true ? "✓" : st.ok === false ? "✕" : "i" }),
      h("span", null, h("span", { class: "rule", text: st.rule }), h("span", { class: "detail", text: st.detail })))));
  }

  const CONTROLS = [
    ["SSRF prevention", "https only, port 443, no userinfo, internal hostnames denied, every DNS answer must be globally routable (IPv4-in-IPv6 unwrapped), IPs returned for pinning.", "test_ssrf_and_security.py"],
    ["Broken authentication", "Tenant client secrets checked in constant time; JWT pinned to HS256 with iss, aud, exp, nbf, jti; tokens last 60 minutes.", "test_api_integration.py"],
    ["BOLA / tenant isolation", "Every route compares the token tenant to the requested tenant; denial messages never echo the probed tenant.", "test_api_integration.py"],
    ["Privilege escalation", "Only analyst and viewer roles can be minted; tokens carrying other roles are rejected; viewers cannot persist.", "test_ssrf_and_security.py"],
    ["Mass assignment", "All request schemas use extra='forbid', tenant IDs follow a strict pattern, enums are Literal types.", "test_ssrf_and_security.py"],
    ["Resource exhaustion", "1 MB body limit, global + per-route rate limits, path ≤ 25, channels ≤ 20, order ≤ 3, bootstrap ≤ 500, Shapley closed form above 12 channels.", "test_api_integration.py"],
    ["XSS & injection", "Untrusted text is reduced to inert plain text; JSON-LD escapes <, >, & so <\/script> cannot break out.", "test_geo_and_jsonld.py"],
    ["Headers & secrets", "CSP, HSTS, nosniff, COOP, no-store on API; production refuses weak or missing secrets; no secrets in git.", "test_api_integration.py"],
  ];
  function renderControls() {
    const grid = $("controls-grid");
    clear(grid);
    CONTROLS.forEach(([title, body, test]) => grid.appendChild(h("article", { class: "control" }, h("h4", { text: title }), h("p", { text: body }), h("span", { class: "tag", text: test }))));
  }

  // ------------------------------------------------------------------ Verification
  function renderVerification() {
    const ref = DATA.reference;
    const rows = [];
    let compared = 0, maxDelta = 0, allPass = true;
    const cmp = (label, pairs, tol) => {
      let worst = 0;
      for (const [a, b] of pairs) worst = Math.max(worst, Math.abs(a - b));
      const ok = worst <= tol;
      compared += pairs.length;
      maxDelta = Math.max(maxDelta, worst);
      allPass = allPass && ok;
      rows.push([label, pairs.length, worst, ok]);
    };
    const t0 = performance.now();
    for (const order of ["1", "2", "3"]) {
      const r = E.markovAttribution(SEED, DATA.channels, +order);
      const py = ref.markov[order];
      cmp(`Markov order ${order}: P(conv)`, [[r.base, py.base]], 1e-10);
      cmp(`Markov order ${order}: removal effects`, DATA.channels.map((c) => [r.effects[c], py.effects[c]]), 1e-10);
      cmp(`Markov order ${order}: shares`, DATA.channels.map((c) => [r.shares[c], py.shares[c]]), 1e-10);
    }
    const full = E.runPipeline(SEED, { order: 1, halfLife: 2 });
    cmp("Expected visits per channel", DATA.channels.map((c) => [full.visits[c], ref.visits[c]]), 1e-10);
    cmp("Expected path length", [[full.pathLength, ref.path_length]], 1e-10);
    const phi = E.shapleyExact(SEED, DATA.channels);
    cmp("Shapley φ (USD, exact 2^N)", DATA.channels.map((c) => [phi[c], ref.shapley_phi[c]]), 1e-6);
    for (const m of ["Last-Click", "First-Touch", "Linear", "Position-Based", "Time-Decay"]) {
      cmp(`${m} shares`, DATA.channels.map((c) => [full.models[m][c], ref.rules[m][c]]), 2e-6);
    }
    cmp("Divergence TV / JSD", Object.keys(ref.divergence).flatMap((m) => [[full.divergence[m].tv, ref.divergence[m].tv], [full.divergence[m].jsd, ref.divergence[m].jsd]]), 2e-5);
    const opt = budgetFor(full, ref.optimizer.budget, ref.optimizer.beta, ref.optimizer.trust);
    cmp("Optimizer allocation (USD)", DATA.channels.map((c) => [opt.allocation[c], ref.optimizer.allocation[c]]), 1e-4);
    cmp("Optimizer λ* and revenues", [[opt.lambda, ref.optimizer.lambda], [opt.revenueNow, ref.optimizer.revenue_now], [opt.revenueOpt, ref.optimizer.revenue_opt]], 1e-5);
    const ssrfPairs = ref.ssrf.map(([url, blocked]) => [E.ssrfCheck(url).verdict === "blocked" ? 1 : 0, blocked ? 1 : 0]);
    cmp(`SSRF verdicts (${ref.ssrf.length} URLs)`, ssrfPairs, 0);
    const sanPairs = ref.sanitize.map(([input, out]) => [E.sanitizeText(input) === out ? 0 : 1, 0]);
    cmp(`Sanitizer outputs (${ref.sanitize.length} payloads)`, sanPairs, 0);
    const ms = performance.now() - t0;

    clear($("verify-kpis"));
    $("verify-kpis").append(
      kpi("Parity status", allPass ? "All match" : "Mismatch", allPass ? "JavaScript reproduces Python" : "see table", allPass ? "up" : "down"),
      kpi("Values compared", compared.toLocaleString("en-US"), `${rows.length} checks in ${ms.toFixed(0)} ms`),
      kpi("Largest |Δ|", sci(maxDelta), "floating-point agreement"),
      kpi("Automated tests", DATA.tests.total.toLocaleString("en-US"), "pytest, incl. Node parity run"),
    );
    const table = $("parity-table");
    clear(table);
    table.append(h("caption", { class: "sr-only", text: "Python versus JavaScript" }),
      h("thead", null, h("tr", null, ["Quantity", "n", "max |Δ|", "Result"].map((x) => h("th", { scope: "col", text: x })))),
      h("tbody", null, rows.map(([label, n, worst, ok]) => h("tr", null,
        h("td", { class: "label-cell", text: label }), h("td", { text: String(n) }), h("td", { text: sci(worst) }),
        h("td", null, h("span", { class: ok ? "pass" : "fail", text: ok ? "✓ match" : "✕ differs" }))))));

    const tt = $("tests-table");
    clear(tt);
    tt.append(h("caption", { class: "sr-only", text: "Tests by file" }),
      h("thead", null, h("tr", null, ["Test file", "Tests", "Covers"].map((x) => h("th", { scope: "col", text: x })))),
      h("tbody", null, DATA.tests.files.map((f) => h("tr", null, h("td", { class: "label-cell", text: f.file }), h("td", { text: String(f.count) }), h("td", { text: f.covers })))));
  }

  // ------------------------------------------------------------------ wiring
  function setSeg(attr, value) {
    document.querySelectorAll(`[data-${attr}]`).forEach((b) => b.setAttribute("aria-checked", String(b.dataset[attr] === String(value))));
  }

  function initTheme() {
    try {
      const saved = localStorage.getItem("geo-mta-theme");
      if (saved === "light" || saved === "dark") document.documentElement.setAttribute("data-theme", saved);
    } catch (e) { /* storage unavailable */ }
    $("theme-toggle").addEventListener("click", () => {
      const next = readThemeIsDark() ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      try { localStorage.setItem("geo-mta-theme", next); } catch (e) { /* ignore */ }
      renderVisuals();
    });
    if (window.matchMedia) window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", renderVisuals);
  }

  function renderVisuals() { renderGeo(); renderAttribution(); renderBudget(); }

  let frame = 0;
  const schedule = (fn) => { cancelAnimationFrame(frame); frame = requestAnimationFrame(fn); };

  function init() {
    initTheme();
    // GEO
    ["geo-brand", "geo-prompts", "geo-rate", "geo-rank", "geo-sent"].forEach((id) => $(id).addEventListener("input", () => schedule(renderGeo)));
    // Attribution
    document.querySelectorAll("[data-dataset]").forEach((b) => b.addEventListener("click", () => { state.dataset = b.dataset.dataset; setSeg("dataset", state.dataset); renderAttribution(); }));
    document.querySelectorAll("[data-order]").forEach((b) => b.addEventListener("click", () => { state.order = +b.dataset.order; setSeg("order", state.order); renderAttributionAndBudget(); }));
    $("half-life").addEventListener("input", () => { state.halfLife = +$("half-life").value; $("half-life-out").textContent = `${state.halfLife} touches`; schedule(renderAttributionAndBudget); });
    $("half-life-out").textContent = `${state.halfLife} touches`;
    $("bootstrap-toggle").addEventListener("change", () => { state.bootstrap = $("bootstrap-toggle").checked; renderAttribution(); });
    $("compare-model").addEventListener("change", () => { state.compare = $("compare-model").value; renderAttribution(); });
    $("builder-converted").addEventListener("change", renderBuilder);
    $("builder-add").addEventListener("click", () => {
      if (!state.draft.length || state.custom.length >= 200) return;
      const n = Math.max(1, Math.min(500, Math.round(+$("builder-count").value || 1)));
      const value = Math.max(0, Math.min(100000, +$("builder-value").value || 0));
      const converted = $("builder-converted").checked;
      state.custom.push({ p: [...state.draft], c: converted ? 1 : 0, n, v: converted ? n * value : 0 });
      state.customVersion++;
      renderAttributionAndBudget();
    });
    $("builder-clear").addEventListener("click", () => { state.draft = []; renderBuilder(); });
    $("builder-reset").addEventListener("click", () => { state.custom = DEFAULT_CUSTOM.map((r) => ({ ...r, p: [...r.p] })); state.customVersion++; renderAttributionAndBudget(); });
    // Budget
    ["budget-total", "budget-beta", "budget-trust"].forEach((id) => $(id).addEventListener("input", () => schedule(renderBudget)));
    $("budget-total").value = DATA.defaults.budget;
    // JSON-LD
    document.querySelectorAll("[data-schema]").forEach((b) => b.addEventListener("click", () => { state.schema = b.dataset.schema; renderSchemaForm(); }));
    $("jsonld-copy").addEventListener("click", async () => {
      try { await navigator.clipboard.writeText($("jsonld-output").textContent); $("jsonld-copy").textContent = "Copied"; }
      catch (e) { $("jsonld-copy").textContent = "Copy failed"; }
      setTimeout(() => { $("jsonld-copy").textContent = "Copy"; }, 1800);
    });
    $("jsonld-attack").addEventListener("click", () => {
      const key = state.schema === "FAQPage" ? "a1" : "description";
      formValues[`ld-${state.schema}-${key}`] = 'Great tool<\/script><script>alert(document.domain)<\/script><img src=x onerror=alert(1)> &lt;svg onload=alert(2)&gt;';
      renderSchemaForm();
    });
    $("jsonld-reset").addEventListener("click", () => { Object.keys(formValues).forEach((k) => { if (k.startsWith(`ld-${state.schema}-`)) delete formValues[k]; }); renderSchemaForm(); });
    // SSRF
    const presets = $("ssrf-presets");
    SSRF_PRESETS.forEach(([label, url]) => presets.appendChild(h("button", { type: "button", class: "chip", text: label, title: url, on: { click: () => { $("ssrf-input").value = url; renderSsrf(); } } })));
    $("ssrf-run").addEventListener("click", renderSsrf);
    $("ssrf-input").addEventListener("keydown", (e) => { if (e.key === "Enter") renderSsrf(); });
    $("ssrf-input").addEventListener("input", () => schedule(renderSsrf));

    setSeg("dataset", state.dataset);
    setSeg("order", state.order);
    renderHero();
    renderVisuals();
    renderSchemaForm();
    renderSsrf();
    renderControls();
    renderVerification();

    // Re-render charts when their containers resize
    let lastWidth = document.documentElement.clientWidth;
    if (window.ResizeObserver) {
      new ResizeObserver(() => {
        const w = document.documentElement.clientWidth;
        if (Math.abs(w - lastWidth) > 4) { lastWidth = w; schedule(renderVisuals); }
      }).observe(document.body);
    }

    // Active section highlighting
    const links = [...document.querySelectorAll(".nav a")];
    if (window.IntersectionObserver) {
      const io = new IntersectionObserver((entries) => {
        entries.forEach((en) => {
          if (en.isIntersecting) links.forEach((a) => a.classList.toggle("active", a.getAttribute("href") === `#${en.target.id}`));
        });
      }, { rootMargin: "-45% 0px -50% 0px" });
      document.querySelectorAll("main section[id]").forEach((sec) => io.observe(sec));
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
