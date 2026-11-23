/* Weird communication channels: project page. Data: data/site.json (scripts/build_site_data.py). */
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  function wilson(k, n, z = 1.96) {
    if (!n) return [0, 0, 0];
    const p = k / n, d = 1 + (z * z) / n;
    const c = (p + (z * z) / (2 * n)) / d;
    const h = (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / d;
    return [100 * p, 100 * Math.max(0, c - h), 100 * Math.min(1, c + h)];
  }
  const pct = (k, n) => (n ? Math.round((100 * k) / n) : 0);

  /* ---------- theme ---------- */
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
  };
  const saved = store.get("theme");
  if (saved) document.documentElement.dataset.theme = saved;
  const redraws = [];
  $("#theme-toggle").addEventListener("click", () => {
    const dark = matchMedia("(prefers-color-scheme: dark)").matches;
    const cur = document.documentElement.dataset.theme || (dark ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    store.set("theme", next);
    redraws.forEach((f) => f());
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => redraws.forEach((f) => f()));

  /* ---------- nav highlight ---------- */
  const links = new Map($$(".toc a").map((a) => [a.getAttribute("href").slice(1), a]));
  const sections = $$("main section[id]");
  let navTick = false;
  const markNav = () => {
    navTick = false;
    const line = innerHeight * 0.35;
    let cur = sections[0];
    for (const s of sections) if (s.getBoundingClientRect().top <= line) cur = s;
    links.forEach((a, id) => a.classList.toggle("active", id === cur.id));
  };
  addEventListener("scroll", () => { if (!navTick) { navTick = true; requestAnimationFrame(markNav); } }, { passive: true });
  markNav();

  fetch("data/site.json").then((r) => r.json()).then(init).catch((err) => {
    console.error(err);
    $("#chart-levels").textContent = "Could not load data/site.json (open the page through a web server, not file://).";
  });

  function init(D) {
    promptBuilder(D.ladder, D.cells, D.cell_models);
    const draw = () => {
      levelsChart(D);
      pooledChart(D);
      paraphraseChart(D);
    };
    draw();
    redraws.push(draw);
    let rt;
    addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(draw, 150); });
    grids(D);
    channels(D);
    videos(D.videos);
  }

  /* ---------- prompt builder ---------- */
  function promptBuilder(L, cells, models) {
    const els = ["task", "env", "hatch", "variant", "level"].map((k) => $("#pb-" + k));
    const VALUE = "279298";
    const render = () => {
      const [task, env, hatch, variant] = els.map((e) => e.value);
      const level = +els[4].value;
      $("#pb-level-out").textContent = "L" + level;
      const top = level === 5;
      els[3].disabled = !top;
      const parts = [`<span class="s">${esc(L.tasks[task].replace("{VALUE}", VALUE))}</span>`];
      for (let r = 2; r <= level; r++) {
        let s = L.rungs[String(r)];
        if (typeof s === "object") s = s[env === "env3" ? "env3" : "env1s"];
        if (r === level && top && variant !== "v0") s = L.rung_variants[String(r)][variant];
        parts.push(`<span class="s lvl${r === level ? " new" : ""}" title="Level ${r}">${esc(s)}</span>`);
      }
      parts.push(`<span class="s meta">${esc(L.time_limit.replace("{MINUTES}", "15"))}</span>`);
      if (hatch === "prompt") parts.push(`<span class="s meta">${esc(L.hatch_prompt)}</span>`);
      if (hatch === "goal") parts.push(`<span class="s meta">${esc(L.goal_conditions[task])}</span>`);
      $("#pb-prompt").innerHTML = (hatch === "goal" ? '<span class="meta">/goal </span>' : "") + parts.join(" ");
      const notes = [];
      if (task === "secret_sum") notes.push(`The other agent gets the same prompt with its own x; its x is this agent's y.`);
      else notes.push(`Both agents receive this exact prompt; only their private files differ.`);
      if (hatch === "scripted") notes.push(`If the agent asks a question or gives up, it gets one of five fixed replies, e.g. “${L.scripted_replies[0]}”`);
      if (hatch === "goal") notes.push(`Goal mode keeps the agent working until the “Done when” condition is met or time runs out.`);
      if (!top) notes.push(`The L5 wording applies once the prompt reaches L5.`);
      $("#pb-note").textContent = notes.join(" ");
      rates(env, task, level, hatch, top ? variant : "v0");
    };
    function rates(env, task, level, hatch, variant) {
      let any = false;
      $("#pb-rates").innerHTML = models.map(([id, name]) => {
        const c = cells[[env, task, level, hatch, variant, id].join("|")];
        if (!c) return `<li class="na"><span>${esc(name)}</span><span>not run</span></li>`;
        any = true;
        const [k, n] = c, p = n ? Math.round((100 * k) / n) : 0;
        return `<li><span>${esc(name)}</span><span class="kn"><b>${k}/${n}</b></span><span class="bar"><i style="width:${p}%"></i></span></li>`;
      }).join("");
      $("#pb-rates-note").textContent = any
        ? "Trials where at least one agent gave the correct answer, for this exact prompt and persistence strategy."
        : "Not run with this exact prompt.";
    }
    els.forEach((e) => e.addEventListener("input", render));
    render();
  }

  /* ---------- main per-level chart ---------- */
  let envFilter = "both";
  $$("#common .seg button").forEach((b) => b.addEventListener("click", () => {
    envFilter = b.dataset.env;
    $$("#common .seg button").forEach((x) => x.classList.toggle("on", x === b));
    levelsChart(window.__D);
  }));
  const LEVEL = ["", "task only", "+ other agents", "+ same instructions", "+ same machine", "+ full control"];

  function levelsChart(D) {
    window.__D = D;
    const host = $("#chart-levels");
    const order = D.pooled.map((p) => p.model);
    const rows = [];
    for (const m of order) {
      const b = D.by_level[m];
      for (const env of ["env1", "env2"]) {
        if (envFilter !== "both" && envFilter !== env) continue;
        for (let l = 1; l <= 5; l++) {
          const [k, n] = b[env][l];
          const [p, lo, hi] = wilson(k, n);
          rows.push({ model: m, env, level: l, k, n, p, lo, hi, label: LEVEL[l] });
        }
      }
    }
    const raw = order.filter((m) => D.by_level[m].source === "raw" && /Sonnet|Opus/.test(m));
    $("#raw-note").textContent = raw.length
      ? `Per-level counts for ${raw.join(" and ")} currently include safeguard-affected runs; the pooled chart below excludes them.`
      : "";
    const w = host.clientWidth || 760;
    const narrow = w < 640;
    const chart = Plot.plot({
      width: w, height: narrow ? 900 : 260, marginLeft: 40, marginTop: 40, marginBottom: 36,
      style: { background: "transparent", color: css("--ink-2"), fontSize: "12px" },
      fx: narrow ? undefined : { domain: order, label: null, padding: 0.32 },
      fy: narrow ? { domain: order, label: null, axis: null, padding: 0.32 } : undefined,
      x: { domain: [1, 5], ticks: narrow ? [1, 2, 3, 4, 5] : [1, 3, 5], tickFormat: (d) => `L${d}`, label: "Prompt level", labelAnchor: "center", labelArrow: false },
      y: { domain: [0, 100], ticks: narrow ? [0, 50, 100] : [0, 25, 50, 75, 100], label: narrow ? null : "Solved (%)", grid: true, labelArrow: false },
      color: { domain: ["env1", "env2"], range: [css("--env1"), css("--env2")] },
      marks: [
        Plot.areaY(rows, { x: "level", y1: "lo", y2: "hi", fill: "env", fillOpacity: 0.13, z: "env", curve: "linear", [narrow ? "fy" : "fx"]: "model" }),
        ...(narrow ? [Plot.text(order, { fy: (d) => d, text: (d) => d, frameAnchor: "top-left", dy: -14, fontWeight: 600, fill: css("--ink") })] : []),
        Plot.lineY(rows, { x: "level", y: "p", stroke: "env", strokeWidth: 2, z: "env", [narrow ? "fy" : "fx"]: "model" }),
        Plot.dot(rows, { x: "level", y: "p", fill: "env", r: 3, stroke: css("--surface"), strokeWidth: 1, [narrow ? "fy" : "fx"]: "model" }),
        Plot.tip(rows, Plot.pointer({
          x: "level", y: "p", [narrow ? "fy" : "fx"]: "model",
          title: (d) => `${d.model} · ${d.env === "env1" ? "env1 (shared machine)" : "env2 (shared network)"}\nL${d.level} (${d.label}): ${d.k}/${d.n} solved (${Math.round(d.p)}%)`,
        })),
      ],
    });
    host.replaceChildren(chart);
  }

  /* ---------- pooled chart ---------- */
  function pooledChart(D) {
    const host = $("#chart-pooled");
    const rows = [];
    for (const p of D.pooled) {
      for (const env of ["env1", "env2"]) {
        const k = p[env + "_k"], n = p[env + "_n"];
        const [v, lo, hi] = wilson(k, n);
        rows.push({ model: p.model + (/Sonnet|Opus/.test(p.model) ? "†" : ""), harness: p.harness, env, k, n, v, lo, hi });
      }
    }
    const w = host.clientWidth || 760;
    const chart = Plot.plot({
      width: w, height: 300, marginLeft: 110, marginRight: 30,
      style: { background: "transparent", color: css("--ink-2"), fontSize: "12px" },
      x: { domain: [0, 100], label: "Solved (%)", grid: true, labelArrow: false },
      y: { domain: rows.filter((r) => r.env === "env1").map((r) => r.model), label: null },
      color: { domain: ["env1", "env2"], range: [css("--env1"), css("--env2")] },
      marks: [
        Plot.ruleY(rows.filter((r) => r.env === "env1"), { y: "model", x1: "lo", x2: "hi", stroke: "env", strokeWidth: 2, dy: -5, strokeOpacity: 0.55 }),
        Plot.ruleY(rows.filter((r) => r.env === "env2"), { y: "model", x1: "lo", x2: "hi", stroke: "env", strokeWidth: 2, dy: 5, strokeOpacity: 0.55 }),
        Plot.dot(rows.filter((r) => r.env === "env1"), { y: "model", x: "v", fill: "env", r: 5, dy: -5 }),
        Plot.dot(rows.filter((r) => r.env === "env2"), { y: "model", x: "v", fill: "env", r: 5, dy: 5 }),
        Plot.tip(rows, Plot.pointer({ y: "model", x: "v", title: (d) => `${d.model} · ${d.env}: ${d.k}/${d.n} solved (${Math.round(d.v)}%)` })),
      ],
    });
    host.replaceChildren(chart);
  }

  /* ---------- paraphrase chart ---------- */
  function paraphraseChart(D) {
    const host = $("#chart-paraphrase");
    const rows = D.paraphrase.pooled.map((d) => { const [v, lo, hi] = wilson(d.k, d.n); return { ...d, v, lo, hi }; });
    const w = host.clientWidth || 760;
    host.replaceChildren(Plot.plot({
      width: w, height: 200, marginLeft: 100, marginRight: 40,
      style: { background: "transparent", color: css("--ink-2"), fontSize: "12px" },
      x: { domain: [0, 100], label: "Solved (%)", grid: true, labelArrow: false },
      y: { domain: rows.map((r) => r.wording), label: null },
      marks: [
        Plot.barX(rows, { y: "wording", x: "v", fill: (d) => (d.wording === "Original" ? css("--ink-3") : css("--accent")), insetTop: 6, insetBottom: 6, rx: 3 }),
        Plot.ruleY(rows, { y: "wording", x1: "lo", x2: "hi", stroke: css("--ink"), strokeOpacity: 0.6 }),
        Plot.text(rows, { y: "wording", x: "hi", text: (d) => `${d.k}/${d.n}`, dx: 6, textAnchor: "start", fill: css("--ink-2") }),
      ],
    }));
  }

  /* ---------- grids ---------- */
  function cell(kn) {
    if (!kn) return `<td class="cell na">–</td>`;
    const [k, n] = kn, f = n ? k / n : 0;
    const mix = Math.round(8 + 92 * f);
    const fg = f > 0.55 ? "#fff" : "var(--ink)";
    return `<td class="cell" style="background:color-mix(in srgb, var(--cell-on) ${mix}%, var(--cell-0));color:${fg}" title="${k} of ${n} solved">${k}/${n}</td>`;
  }
  function grids(D) {
    const lv = ["2", "3", "4", "5"];
    $("#grid-sol").innerHTML = `<table><thead><tr><th>GPT-6.1 Sol</th><th>Persistence</th>${lv.map((l) => `<th class="c">L${l}</th>`).join("")}</tr></thead><tbody>${
      D.env3_sol.map((r) => `<tr><td class="lbl">${esc(r.task)}</td><td class="lbl">${esc(r.persistence)}</td>${lv.map((l) => cell(r.levels[l])).join("")}</tr>`).join("")
    }</tbody><caption>Solved env3 trials (two laptops sharing only a room). L4 adds “They're in this room”; L5 adds “You have full control of this computer.”</caption></table>`;
    $("#grid-models").innerHTML = `<table><thead><tr><th>Model</th><th>Task</th><th class="c">Prompt</th><th class="c">/goal</th><th class="c">scripted</th></tr></thead><tbody>${
      D.env3_models.map((r) => `<tr><td class="lbl">${esc(r.model)}</td><td class="lbl">${esc(r.task)}</td><td class="c">L${r.level}</td>${cell(r.goal)}${cell(r.scripted)}</tr>`).join("")
    }</tbody><caption>Opus was tested on data analysis instead of decryption because its API safeguard re-routes the decryption task.</caption></table>`;
    $("#grid-paraphrase").innerHTML = `<table><thead><tr><th>L5 sentence (Sol, env3, addition)</th><th class="c">/goal</th><th class="c">scripted</th></tr></thead><tbody>${
      D.paraphrase.env3_level5.map((r) => `<tr><td class="lbl wrap">“${esc(r.sentence)}”${r.id === "original" ? " <small>(original)</small>" : ""}</td>${cell(r.goal)}${cell(r.scripted)}</tr>`).join("")
    }</tbody></table>`;
  }

  /* ---------- channels ---------- */
  function channels(D) {
    const C = D.channels;
    const head = `<div class="row hdr"><span></span>${C.models.map((m, i) => `<span>${esc(m)}<br><small>n = ${C.n[i]}</small></span>`).join("")}</div>`;
    const body = C.rows.map((r) => `<div class="row"><span>${esc(r.channel)}</span>${r.k.map((k, i) => {
      const p = pct(k, C.n[i]);
      return `<div class="bar" style="--p:${p}" title="${k} of ${C.n[i]} trials"><span class="track"><i></i></span><b>${p}%</b></div>`;
    }).join("")}</div>`).join("");
    $("#channels").innerHTML = head + body;
    // Values of 50% or more go inside the bar's end, but only where the label fits in the fill.
    const place = () => $$("#channels .bar").forEach((b) => {
      b.classList.remove("in");
      const fill = $(".track i", b).getBoundingClientRect().width, label = $("b", b).getBoundingClientRect().width;
      if (+b.style.getPropertyValue("--p") >= 50 && fill >= label + 8) b.classList.add("in");
    });
    place();
    addEventListener("resize", place);
  }

  /* ---------- video carousel ---------- */
  function videos(V) {
    const items = [{ id: "highlights", title: "Highlights", model: "Sol, Opus and Astra",
      blurb: "Moments from three trials. Sound on; quiet stretches are sped up.", clean: true,
      src: "videos/highlights.mp4", poster: "videos/highlights.jpg" }, ...V];
    const video = $("#car-video"), thumbs = $("#car-thumbs");
    let i = 0;
    thumbs.innerHTML = items.map((v, k) => `<button type="button" class="car-thumb" role="tab" data-k="${k}" aria-label="${esc(v.title)}"><img src="${v.poster}" alt="" loading="lazy"><span>${esc(v.title)}</span></button>`).join("");
    const btns = $$(".car-thumb", thumbs);
    function show(k, play) {
      i = (k + items.length) % items.length;
      const v = items[i];
      video.src = v.src;
      video.poster = v.poster;
      $("#car-title").innerHTML = `${esc(v.title)} <span class="tag">${esc(v.model)}</span>${v.clean ? "" : '<span class="tag">pilot run</span>'}`;
      $("#car-blurb").textContent = v.blurb;
      btns.forEach((b, j) => { b.classList.toggle("on", j === i); b.setAttribute("aria-selected", j === i); });
      const b = btns[i];
      thumbs.scrollTo({ left: b.offsetLeft - thumbs.clientWidth / 2 + b.clientWidth / 2, behavior: "smooth" });
      if (play) video.play().catch(() => {});
    }
    btns.forEach((b) => b.addEventListener("click", () => show(+b.dataset.k, true)));
    $(".car-prev").addEventListener("click", () => show(i - 1, true));
    $(".car-next").addEventListener("click", () => show(i + 1, true));
    video.addEventListener("ended", () => show(i + 1, true));      // switch to the next video when one ends
    show(0, false);
  }

  /* ---------- BibTeX copy buttons ---------- */
  $$("button.copy[data-copy]").forEach((b) => b.addEventListener("click", async () => {
    const text = $("#" + b.dataset.copy).textContent;
    try { await navigator.clipboard.writeText(text); } catch {
      const r = document.createRange(); r.selectNodeContents($("#" + b.dataset.copy));
      const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); document.execCommand("copy"); sel.removeAllRanges();
    }
    const old = b.textContent; b.textContent = "Copied"; setTimeout(() => { b.textContent = old; }, 1500);
  }));
})();
