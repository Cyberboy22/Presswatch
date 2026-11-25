// PressWatch replay for the static (Netlify) site.
// Answers the dashboard's /api/* requests from a recording of a real run and adds a
// player with chapters. Loaded as a classic script before app.js.
(function () {
  "use strict";

  const script = document.currentScript;
  const mode = (script && script.dataset.mode) || "player";
  const realFetch = window.fetch.bind(window);
  const BASE_MS = 450; // one frame (5 cycles) at 1x speed
  const HOLD_MS = 1800; // pause on each new chapter so it can be read
  const state = { rec: null, frame: 0, playing: false, speed: 1, timer: null, chapter: -1 };

  window.PRESSWATCH_REPLAY = true;

  const ready = realFetch("/data/recording.json")
    .then((response) => {
      if (!response.ok) throw new Error(`Aufzeichnung fehlt (${response.status})`);
      return response.json();
    })
    .then((rec) => {
      state.rec = rec;
      return rec;
    });

  function respond(body, status = 200) {
    return new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  }

  function cyclesUpTo(limit) {
    const upto = state.rec.frames[state.frame].cycle;
    const delay = state.rec.meta.measurement_delay;
    const list = [];
    for (const c of state.rec.cycles) {
      if (c.cycle_id > upto) break;
      list.push(c);
    }
    return list
      .slice(-limit)
      .map((c) => (c.measurement && c.cycle_id > upto - delay ? { ...c, measurement: null } : c));
  }

  window.fetch = async function (input, init) {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    if (url.origin !== location.origin || !url.pathname.startsWith("/api/")) {
      return realFetch(input, init);
    }
    await ready;
    const method = ((init && init.method) || "GET").toUpperCase();
    if (method !== "GET") {
      return respond(
        { detail: "Das ist eine Aufzeichnung. Lokal mit „presswatch demo“ kannst du selbst entscheiden." },
        403,
      );
    }
    const frame = state.rec.frames[state.frame];
    const path = url.pathname;
    const limit = Number(url.searchParams.get("limit")) || undefined;
    if (path === "/api/status") return respond(frame.status);
    if (path === "/api/cycles") return respond(cyclesUpTo(limit || 300));
    const detail = path.match(/^\/api\/cycles\/(\d+)$/);
    if (detail) {
      if (Number(detail[1]) > frame.cycle) return respond({ detail: "Zyklus noch nicht gepresst" }, 404);
      return realFetch(`/data/zyklus/${detail[1]}.json`);
    }
    if (path === "/api/models") return respond(state.rec.models[frame.models]);
    if (path === "/api/audit") return respond(state.rec.audits[frame.audit].slice(0, limit || 200));
    if (path === "/api/sim") return respond(frame.sim);
    if (path === "/api/health") return respond({ status: "ok", replay: true });
    return respond({ detail: "Nicht Teil der Aufzeichnung" }, 404);
  };

  // ---- shared helpers --------------------------------------------------------------

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  let refreshing = false;
  let refreshAgain = false;
  async function refreshDashboard() {
    if (typeof window.presswatchRefresh !== "function") return;
    if (refreshing) {
      refreshAgain = true;
      return;
    }
    refreshing = true;
    try {
      await window.presswatchRefresh();
    } finally {
      refreshing = false;
      if (refreshAgain) {
        refreshAgain = false;
        refreshDashboard();
      }
    }
  }

  function chapterIndexAt(frameIndex) {
    const cycle = state.rec.frames[frameIndex].cycle;
    let index = -1;
    state.rec.chapters.forEach((c, i) => {
      if (c.cycle <= cycle) index = i;
    });
    return index;
  }

  // ---- operator view: loop the recording -------------------------------------------

  function startAutoLoop() {
    const badge = el("span", { class: "replay-badge" }, "Aufzeichnung");
    badge.style.cssText =
      "border:1px solid var(--line);border-radius:999px;padding:.1rem .6rem;font-size:.9rem";
    const header = document.querySelector("header");
    if (header) header.append(badge);
    const button = document.getElementById("tool-changed");
    if (button) button.remove();
    const tick = () => {
      const last = state.frame >= state.rec.frames.length - 1;
      state.frame = last ? 0 : state.frame + 1;
      setTimeout(tick, last ? 4000 : BASE_MS);
    };
    setTimeout(tick, BASE_MS);
  }

  // ---- dashboard: player with chapters ------------------------------------------------

  const ui = {};

  function buildPlayer() {
    const rec = state.rec;
    const config = window.PRESSWATCH_SITE || {};
    const total = rec.meta.n_cycles;

    ui.play = el("button", { class: "primary", type: "button", onclick: togglePlay }, "Abspielen");
    ui.speedButtons = [1, 2, 4].map((speed) =>
      el("button", {
        class: "secondary",
        type: "button",
        "aria-pressed": String(speed === state.speed),
        onclick: () => setSpeed(speed),
      }, `${speed}×`),
    );
    ui.range = el("input", {
      type: "range",
      min: 0,
      max: rec.frames.length - 1,
      value: 0,
      "aria-label": "Zeitpunkt in der Aufzeichnung",
      oninput: (event) => {
        pause();
        goTo(Number(event.target.value));
      },
    });
    ui.ticks = el("div", { class: "replay-ticks", "aria-hidden": "true" },
      rec.chapters.map((c) =>
        el("span", {
          class: c.kind === "detected" ? "tick tick-alarm" : "tick",
          style: `left:${(100 * (c.cycle - 1)) / (total - 1)}%`,
        }),
      ),
    );
    ui.position = el("span", { class: "replay-pos" }, "");
    ui.chapterButtons = rec.chapters.map((c, i) =>
      el("li", {},
        el("button", {
          type: "button",
          class: c.kind === "detected" ? "chapter chapter-alarm" : "chapter",
          onclick: () => {
            pause();
            goTo(rec.frames.findIndex((f) => f.cycle >= c.cycle));
          },
        }, el("span", { class: "chapter-no" }, String(i + 1)), c.title),
      ),
    );
    ui.nowTitle = el("strong", {}, "");
    ui.nowText = el("p", {}, "");

    const intro = el("p", { class: "replay-intro" },
      "Aufzeichnung eines echten Laufs: PressWatch erlebt 960 simulierte Linsen mit Verschleiß, " +
      "Retraining, Werkzeugwechsel und einem Sensorfehler. Lokal ist alles live und klickbar.",
      config.repoUrl ? el("a", { href: config.repoUrl, rel: "noopener" }, " Code auf GitHub") : null,
    );

    const section = el("section", { class: "replay", "aria-label": "Aufzeichnung abspielen" },
      intro,
      el("div", { class: "replay-controls" },
        ui.play,
        el("div", { class: "replay-speed", role: "group", "aria-label": "Tempo" }, ui.speedButtons),
        el("div", { class: "replay-scrub" }, ui.ticks, ui.range),
        ui.position,
      ),
      el("ol", { class: "replay-chapters", "aria-label": "Kapitel" }, ui.chapterButtons),
      el("div", { class: "replay-now", "aria-live": "polite" }, ui.nowTitle, ui.nowText),
    );
    document.querySelector("main").prepend(section);
    const docsLink = document.querySelector('a[href="/docs"]');
    if (docsLink) docsLink.remove();
  }

  function renderPlayer() {
    const rec = state.rec;
    const frame = rec.frames[state.frame];
    ui.range.value = String(state.frame);
    ui.position.textContent = `Zyklus ${frame.cycle} von ${rec.meta.n_cycles}`;
    ui.play.textContent = state.playing ? "Pause" : state.frame >= rec.frames.length - 1 ? "Nochmal" : "Abspielen";
    const index = chapterIndexAt(state.frame);
    ui.chapterButtons.forEach((li, i) => {
      const button = li.firstChild;
      if (i === index) button.setAttribute("aria-current", "step");
      else button.removeAttribute("aria-current");
    });
    if (index >= 0) {
      ui.nowTitle.textContent = rec.chapters[index].title;
      ui.nowText.textContent = rec.chapters[index].text;
    }
  }

  function goTo(frameIndex) {
    state.frame = Math.max(0, Math.min(state.rec.frames.length - 1, frameIndex));
    state.chapter = chapterIndexAt(state.frame);
    renderPlayer();
    refreshDashboard();
  }

  function step() {
    if (!state.playing) return;
    if (state.frame >= state.rec.frames.length - 1) {
      pause();
      return;
    }
    const before = state.chapter;
    goTo(state.frame + 1);
    const hold = state.chapter !== before;
    state.timer = setTimeout(step, (hold ? HOLD_MS : BASE_MS) / state.speed);
  }

  function play() {
    if (state.frame >= state.rec.frames.length - 1) goTo(0);
    state.playing = true;
    renderPlayer();
    clearTimeout(state.timer);
    state.timer = setTimeout(step, BASE_MS / state.speed);
  }

  function pause() {
    state.playing = false;
    clearTimeout(state.timer);
    renderPlayer();
  }

  function togglePlay() {
    if (state.playing) pause();
    else play();
  }

  function setSpeed(speed) {
    state.speed = speed;
    ui.speedButtons.forEach((b) => b.setAttribute("aria-pressed", String(b.textContent === `${speed}×`)));
  }

  function whenDomReady() {
    return document.readyState === "loading"
      ? new Promise((resolve) => document.addEventListener("DOMContentLoaded", resolve, { once: true }))
      : Promise.resolve();
  }

  Promise.all([ready, whenDomReady()])
    .then(() => {
      if (mode === "auto") {
        startAutoLoop();
        return;
      }
      buildPlayer();
      goTo(0);
      setTimeout(play, 900);
    })
    .catch((error) => {
      const note = document.createElement("p");
      note.textContent = `Die Aufzeichnung konnte nicht geladen werden: ${error.message}`;
      note.style.cssText = "padding:1rem;color:#dc3c3d;font-weight:600";
      document.body.prepend(note);
    });
})();
