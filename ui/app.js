/* TARS – fereastra */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
let api = null;
let S = null;          // ultima stare
let ver = -1;
let openProject = null;
const openDetails = new Set();

const ZILE = ["duminică", "luni", "marți", "miercuri", "joi", "vineri", "sâmbătă"];
const LUNI = ["ianuarie", "februarie", "martie", "aprilie", "mai", "iunie", "iulie", "august", "septembrie", "octombrie", "noiembrie", "decembrie"];
const STATUS_TXT = { asteapta: "în așteptare", asculta: "te ascult", gandeste: "procesez", vorbeste: "vorbesc", pornire: "pornesc", setari: "configurare", eroare: "eroare" };
const STAGE_COLORS = {
  "Idee": "#7896ad", "Cercetare": "#a78bfa", "Proiectare": "#a78bfa", "Acte în pregătire": "#ffb547",
  "Prototip": "#2b7fff", "În dezvoltare": "#2b7fff", "Prospectare": "#ffb547", "Test": "#40e0ff",
  "Lansat": "#3ef0a3", "Creștere": "#3ef0a3", "Pe pauză": "#ff5d6c", "Încheiat": "#45627a",
};

/* ---------------------------------------------------------------- utilitare */
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const hhmm = (iso) => (iso || "").slice(11, 16);
function dayLabel(d) {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const x = new Date(d + "T00:00:00");
  const diff = Math.round((x - today) / 86400000);
  if (diff === 0) return "azi";
  if (diff === 1) return "mâine";
  if (diff === -1) return "ieri";
  return `${ZILE[x.getDay()].slice(0, 3)} ${x.getDate()} ${LUNI[x.getMonth()].slice(0, 3)}`;
}
function toast(t) {
  const el = $("#toast"); el.textContent = t; el.classList.remove("hidden");
  clearTimeout(toast._t); toast._t = setTimeout(() => el.classList.add("hidden"), 2600);
}
function ring(pct, color, size = 62) {
  const r = 26, c = 2 * Math.PI * r;
  return `<svg class="pring" viewBox="0 0 62 62" style="--sc:${color}">
    <circle class="bg" cx="31" cy="31" r="${r}"/>
    <circle class="fg" cx="31" cy="31" r="${r}" stroke-dasharray="${c}" stroke-dashoffset="${c * (1 - pct / 100)}"/>
    <text x="31" y="31">${pct}%</text></svg>`;
}

/* ---------------------------------------------------------------- markdown minimal */
function inline(s) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a data-url="$2">$1</a>');
}
function md(src) {
  const lines = esc(src || "").split(/\r?\n/);
  let html = "", i = 0;
  while (i < lines.length) {
    let l = lines[i];
    if (/^```/.test(l)) {
      let code = []; i++;
      while (i < lines.length && !/^```/.test(lines[i])) code.push(lines[i++]);
      i++; html += `<pre><code>${code.join("\n")}</code></pre>`; continue;
    }
    const h = l.match(/^(#{1,4})\s+(.*)/);
    if (h) { html += `<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`; i++; continue; }
    if (/^\s*\|.*\|\s*$/.test(l) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      const row = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
      const head = row(l); i += 2;
      let body = "";
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) body += "<tr>" + row(lines[i++]).map((c) => `<td>${c}</td>`).join("") + "</tr>";
      html += `<table><thead><tr>${head.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table>`;
      continue;
    }
    if (/^\s*[-*•]\s+/.test(l)) {
      html += "<ul>";
      while (i < lines.length && /^\s*[-*•]\s+/.test(lines[i])) html += `<li>${inline(lines[i++].replace(/^\s*[-*•]\s+/, ""))}</li>`;
      html += "</ul>"; continue;
    }
    if (/^\s*\d+[.)]\s+/.test(l)) {
      html += "<ol>";
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) html += `<li>${inline(lines[i++].replace(/^\s*\d+[.)]\s+/, ""))}</li>`;
      html += "</ol>"; continue;
    }
    if (/^&gt;\s?/.test(l)) { html += `<blockquote>${inline(l.replace(/^&gt;\s?/, ""))}</blockquote>`; i++; continue; }
    if (/^\s*(---|\*\*\*)\s*$/.test(l)) { html += "<hr>"; i++; continue; }
    if (!l.trim()) { i++; continue; }
    let para = [l]; i++;
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|\s*[-*•]\s|\s*\d+[.)]\s|\s*\|)/.test(lines[i])) para.push(lines[i++]);
    html += `<p>${inline(para.join(" "))}</p>`;
  }
  return html;
}
function sourcesHtml(list) {
  if (!list || !list.length) return "";
  return `<div class="sources"><div class="sources-title">Surse</div>${list.slice(0, 25).map((s) => `<a data-url="${esc(s.url)}" title="${esc(s.url)}">↗ ${esc(s.title || s.url)}</a>`).join("")}</div>`;
}

/* ---------------------------------------------------------------- grafice */
const PALETTE = ["#40e0ff", "#a78bfa", "#ffb547", "#ff5d6c", "#2b7fff", "#f472b6", "#94a3b8", "#fde047"];
function scatterChart(points) {
  if (!points || !points.length) return "";
  const W = 420, H = 300, P = { l: 40, r: 16, t: 14, b: 38 };
  const maxX = Math.max(10, ...points.map((p) => +p.cota_piata || 0)) * 1.1;
  const x = (v) => P.l + (v / maxX) * (W - P.l - P.r);
  const y = (v) => H - P.b - (v / 10) * (H - P.t - P.b);
  let g = "";
  for (let k = 0; k <= 10; k += 2) g += `<line class="grid" x1="${P.l}" x2="${W - P.r}" y1="${y(k)}" y2="${y(k)}"/><text class="lbl" x="${P.l - 8}" y="${y(k) + 3}" text-anchor="end">${k}</text>`;
  for (let k = 0; k <= 4; k++) { const v = (maxX / 4) * k; g += `<text class="lbl" x="${x(v)}" y="${H - P.b + 14}" text-anchor="middle">${Math.round(v)}%</text>`; }
  // cadrane
  const midX = x(maxX / 2), midY = y(5);
  g += `<line class="ax" x1="${midX}" x2="${midX}" y1="${P.t}" y2="${H - P.b}" stroke-dasharray="3 4"/><line class="ax" x1="${P.l}" x2="${W - P.r}" y1="${midY}" y2="${midY}" stroke-dasharray="3 4"/>`;
  g += `<text class="lbl" x="${P.l + 6}" y="${P.t + 10}">Nișă diferențiată</text><text class="lbl" x="${W - P.r - 4}" y="${P.t + 10}" text-anchor="end">Lideri</text>`;
  g += `<text class="lbl" x="${P.l + 6}" y="${H - P.b - 6}">Marfă</text><text class="lbl" x="${W - P.r - 4}" y="${H - P.b - 6}" text-anchor="end">Volum</text>`;
  points.forEach((p, i) => {
    const us = /^noi$/i.test((p.nume || "").trim());
    const c = us ? "#3ef0a3" : PALETTE[i % PALETTE.length];
    const cx = x(+p.cota_piata || 0), cy = y(Math.max(0, Math.min(10, +p.diferentiere || 0)));
    g += `<circle cx="${cx}" cy="${cy}" r="${us ? 8 : 6}" fill="${c}" fill-opacity=".85" style="filter:drop-shadow(0 0 6px ${c})"/>`;
    g += `<text class="${us ? "lbl-strong" : "lbl"}" x="${cx + 10}" y="${cy + 4}">${esc(p.nume)}</text>`;
  });
  g += `<text class="lbl" x="${(W + P.l) / 2}" y="${H - 6}" text-anchor="middle">Cotă de piață →</text>`;
  g += `<text class="lbl" transform="translate(11 ${(H - P.b + P.t) / 2}) rotate(-90)" text-anchor="middle">Diferențiere →</text>`;
  return `<div class="chart"><div class="chart-title">Cotă de piață vs. diferențiere</div><svg viewBox="0 0 ${W} ${H}">${g}</svg></div>`;
}
function barChart(rows, title, estimates) {
  if (!rows || !rows.length) return "";
  const W = 420, H = 240, P = { l: 46, r: 12, t: 22, b: 30 };
  const max = Math.max(...rows.map((r) => +r.valoare || 0)) * 1.15 || 1;
  const bw = (W - P.l - P.r) / rows.length;
  const unit = rows[0].unitate || "";
  let g = "";
  for (let k = 0; k <= 4; k++) {
    const v = (max / 4) * k, yy = H - P.b - (v / max) * (H - P.t - P.b);
    g += `<line class="grid" x1="${P.l}" x2="${W - P.r}" y1="${yy}" y2="${yy}"/><text class="lbl" x="${P.l - 6}" y="${yy + 3}" text-anchor="end">${fmt(v)}</text>`;
  }
  g += `<defs><linearGradient id="bg1" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#40e0ff"/><stop offset="1" stop-color="#2b7fff" stop-opacity=".35"/></linearGradient></defs>`;
  rows.forEach((r, i) => {
    const v = +r.valoare || 0, h = (v / max) * (H - P.t - P.b);
    const bx = P.l + i * bw + bw * 0.18, by = H - P.b - h;
    g += `<rect x="${bx}" y="${by}" width="${bw * 0.64}" height="${h}" rx="3" fill="url(#bg1)" style="filter:drop-shadow(0 0 6px rgba(64,224,255,.4))"/>`;
    g += `<text class="lbl-strong" x="${bx + bw * 0.32}" y="${by - 5}" text-anchor="middle">${fmt(v)}</text>`;
    g += `<text class="lbl" x="${bx + bw * 0.32}" y="${H - P.b + 14}" text-anchor="middle">${esc(r.an)}</text>`;
  });
  g += `<text class="lbl" x="${P.l}" y="12">${esc(unit)}</text>`;
  return `<div class="chart"><div class="chart-title">${esc(title || "Proiecție")}</div><svg viewBox="0 0 ${W} ${H}">${g}</svg>
    ${estimates ? `<div class="note">Valorile marcate ca estimări în analiză nu sunt date oficiale.</div>` : ""}</div>`;
}
function fmt(v) { return v >= 1000 ? (v / 1000).toFixed(v >= 10000 ? 0 : 1) + "k" : (Math.round(v * 10) / 10).toString(); }

/* ---------------------------------------------------------------- randare */
function renderAll() {
  if (!S) return;
  $("#brandName").textContent = (S.name || "TARS").toUpperCase();
  renderHeaderWeather(); renderBrief(); renderStats(); renderTasks(); renderUpcoming(); renderActivity();
  renderProjects(); renderResearch(); renderChat(); renderUpdate();
  if (openProject) renderProjectDetail(false);
}
function renderUpdate() {
  const show = !!S.update_available && !S.auto_update;
  $("#updateCard").classList.toggle("hidden", !show);
  if (show) $("#updateText").textContent = `Versiunea ${S.update_available} e disponibilă (ai ${S.version}).`;
}
function renderHeaderWeather() {
  const w = S.weather;
  $("#weather").innerHTML = w ? `<b>${Math.round(w.temp)}°</b> ${esc(w.cer)} · ${Math.round(w.min)}° / ${Math.round(w.max)}° · ${esc(S.city)}` : esc(S.city || "");
}
function renderBrief() {
  const b = S.briefing || {};
  if (b.text) {
    $("#briefText").textContent = b.text;
    $("#briefMeta").textContent = `${dayLabel(b.date).toUpperCase()} · ${hhmm(b.ts)}`;
  }
}
function renderStats() {
  const tasks = S.tasks || [];
  const done = tasks.filter((t) => t.done).length;
  const active = (S.projects || []).filter((p) => !/pauză|Încheiat/.test(p.stage)).length;
  $("#stats").innerHTML = `
    <div class="stat" style="--c:var(--green)"><div class="stat-n">${done}/${tasks.length}</div><div class="stat-l">Taskuri azi</div></div>
    <div class="stat" style="--c:var(--amber)"><div class="stat-n">${(S.overdue || []).length}</div><div class="stat-l">Restante</div></div>
    <div class="stat" style="--c:var(--violet)"><div class="stat-n">${active}</div><div class="stat-l">Proiecte active</div></div>`;
}
function taskLi(t, overdue) {
  return `<li class="${t.done ? "done" : ""}" data-id="${t.id}">
    <span class="check" data-act="toggle"></span>
    <span class="t-text">${esc(t.text)}</span>
    ${overdue ? `<span class="t-time">${dayLabel(t.date)}</span>` : t.time ? `<span class="t-time">${esc(t.time)}</span>` : ""}
    <button class="t-del" data-act="del" title="Șterge">✕</button></li>`;
}
function renderTasks() {
  const tasks = S.tasks || [];
  $("#taskCount").textContent = tasks.length ? `${tasks.filter((t) => t.done).length}/${tasks.length}` : "";
  $("#taskList").innerHTML = tasks.length ? tasks.map((t) => taskLi(t)).join("") : `<li class="empty">Nimic pe azi. Spune „adaugă un task…”.</li>`;
  const od = S.overdue || [];
  $("#overdueTitle").classList.toggle("hidden", !od.length);
  $("#overdueList").innerHTML = od.map((t) => taskLi(t, true)).join("");
}
function renderUpcoming() {
  const up = S.upcoming || [];
  $("#upcoming").innerHTML = up.length ? up.map((u) => `<li class="${u.kind}">
      <div class="tl-when">${dayLabel(u.date).toUpperCase()}${u.time ? " · " + esc(u.time) + (u.end ? "–" + esc(u.end) : "") : ""}${u.kind === "task" ? " · TASK" : ""}</div>
      <div class="tl-title">${esc(u.title)}</div>${u.where ? `<div class="tl-where">${esc(u.where)}</div>` : ""}</li>`).join("")
    : `<li class="empty" style="list-style:none">Nimic programat în zilele următoare.</li>`;
}
function renderActivity() {
  const log = (S.log || []).slice().reverse();
  $("#activity").innerHTML = log.length ? log.map((e) => `<li class="k-${e.kind}"><span class="a-ts">${hhmm(e.ts)}</span><span class="a-dot"></span><span>${esc(e.text)}</span></li>`).join("")
    : `<li class="empty">Încă nimic azi.</li>`;
}
function projStatusBadge(p) {
  const a = (p.analyses || []).slice(-1)[0];
  if (!a) return "";
  if (a.status === "lucrez") return `<span class="badge busy">analiză în lucru…</span>`;
  if (a.status === "gata") return `<span class="badge">${a.kind === "strategie" ? "strategie" : "propuneri"} ${dayLabel(a.ts.slice(0, 10))}</span>`;
  return "";
}
function renderProjects() {
  const ps = S.projects || [];
  const avg = ps.length ? Math.round(ps.reduce((s, p) => s + (+p.progress || 0), 0) / ps.length) : 0;
  const launched = ps.filter((p) => /Lansat|Creștere/.test(p.stage)).length;
  $("#portfolio").innerHTML = `
    <div class="stat" style="--c:var(--cyan)"><div class="stat-n">${avg}%</div><div class="stat-l">Progres mediu</div></div>
    <div class="stat" style="--c:var(--green)"><div class="stat-n">${launched}/${ps.length}</div><div class="stat-l">Lansate</div></div>`;
  $("#projects").innerHTML = ps.map((p) => {
    const c = STAGE_COLORS[p.stage] || "#40e0ff";
    return `<div class="proj" data-pid="${p.id}" style="--sc:${c}">
      ${ring(+p.progress || 0, c)}
      <div><div class="proj-name">${esc(p.name)}</div>
        <div class="proj-meta"><span class="stage" style="--sc:${c}">${esc(p.stage)}</span>${projStatusBadge(p)}</div>
        ${p.next ? `<div class="proj-next"><b>Următorul pas:</b> ${esc(p.next)}</div>` : ""}</div></div>`;
  }).join("");
}
function renderResearch() {
  const list = S.research || [];
  $("#researchList").innerHTML = list.map((r) => {
    const open = openDetails.has("r" + r.id) ? "open" : "";
    return `<details class="report" data-did="r${r.id}" ${open}>
      <summary><div style="flex:1"><div class="r-q">${esc(r.query)}</div><div class="r-ts">${dayLabel(r.ts.slice(0, 10)).toUpperCase()} · ${hhmm(r.ts)}</div></div>
        ${r.status === "lucrez" ? `<span class="badge busy">caut…</span>` : r.status === "eroare" ? `<span class="badge" style="color:var(--red)">eroare</span>` : ""}</summary>
      <div class="r-body">${r.status === "lucrez" ? `<div class="working"><span class="spinner"></span> Caut pe internet și citesc sursele…</div>` : `<div class="md">${md(r.text)}</div>${sourcesHtml(r.sources)}`}
        <div class="r-actions"><button class="chip-btn" data-rdel="${r.id}">Șterge</button></div></div></details>`;
  }).join("") || `<div class="hint">Nicio cercetare încă.</div>`;
}
function renderChat() {
  const c = S.chat || [];
  const el = $("#chat");
  const atBottom = $("#views").scrollHeight - $("#views").scrollTop - $("#views").clientHeight < 60;
  el.innerHTML = c.map((m) => `<div class="msg ${m.role}">${esc(m.text)}<div class="msg-ts">${hhmm(m.ts)}</div></div>`).join("")
    || `<div class="hint">Spune „TARS” sau scrie mai jos.</div>`;
  if ($("#view-jurnal").classList.contains("active") && atBottom) $("#views").scrollTop = 1e9;
}

/* ---------------------------------------------------------------- proiect deschis */
function currentProject() { return (S.projects || []).find((p) => p.id === openProject); }
function renderProjectDetail(fillInputs = true) {
  const p = currentProject();
  if (!p) { closeOverlay("#projOverlay"); return; }
  const c = STAGE_COLORS[p.stage] || "#40e0ff";
  $("#pdRing").innerHTML = ring(+p.progress || 0, c);
  if (fillInputs) {
    $("#pdName").value = p.name; $("#pdStage").value = p.stage; $("#pdProgress").value = p.progress;
    $("#pdNext").value = p.next || ""; $("#pdDomain").value = p.domain || ""; $("#pdObjective").value = p.objective || "";
    $("#pdAudience").value = p.audience || ""; $("#pdResources").value = p.resources || "";
    if (![...$("#pdStage").options].some((o) => o.value === p.stage)) $("#pdStage").add(new Option(p.stage, p.stage, true, true));
  }
  $("#pdProgVal").textContent = p.progress + "%";
  $("#pdNotes").innerHTML = (p.notes || []).slice().reverse().map((n) => `<li><span class="a-ts">${dayLabel(n.ts.slice(0, 10))}</span><span>${esc(n.text)}</span></li>`).join("");
  const an = (p.analyses || []).slice().reverse();
  $("#pdAnalyses").innerHTML = an.map((a, idx) => {
    const key = "a" + a.id;
    const open = openDetails.has(key) || (idx === 0 && !openDetails.has("closed-" + key)) ? "open" : "";
    const body = a.status === "lucrez"
      ? `<div class="working"><span class="spinner"></span> ${a.kind === "strategie" ? "Analizez piața, concurența și tendințele…" : "Pregătesc propunerile…"}</div>`
      : `${a.charts ? scatterChart(a.charts.pozitionare) + barChart(a.charts.proiectie, a.charts.titlu_proiectie, a.charts.estimari) : ""}<div class="md">${md(a.text)}</div>${sourcesHtml(a.sources)}`;
    return `<details class="analysis" data-did="${key}" ${open}>
      <summary><span class="an-kind">${a.kind === "strategie" ? "⚡ Analiză strategică" : "💡 Propuneri"}${a.focus ? ` · <span style="color:var(--muted)">${esc(a.focus)}</span>` : ""}</span>
        <span class="r-ts">${dayLabel(a.ts.slice(0, 10)).toUpperCase()} ${hhmm(a.ts)}</span></summary>
      <div class="an-body">${body}</div></details>`;
  }).join("");
}
function openOverlay(sel) { $(sel).classList.remove("hidden"); }
function closeOverlay(sel) { $(sel).classList.add("hidden"); if (sel === "#projOverlay") openProject = null; }

/* ---------------------------------------------------------------- live */
let lastStatus = "";
async function pollLive() {
  try {
    const L = await api.get_live();
    if (L.status !== lastStatus) {
      document.body.dataset.status = L.status; lastStatus = L.status;
      $("#btnStop").classList.toggle("hidden", L.status !== "vorbeste");
    }
    $("#statusText").textContent = (STATUS_TXT[L.status] || L.status) + (L.detail ? " · " + L.detail : "");
    $("#heard").textContent = L.heard ? `„${L.heard}”` : "";
    const lvl = L.status === "asculta" ? Math.min(1, (L.level || 0) / 3000) : L.status === "vorbeste" ? 0.4 + Math.random() * 0.5 : 0;
    $("#core").setAttribute("r", 40 + lvl * 18);
    if (L.ver !== ver) await pollState();
  } catch (e) { /* fereastra se închide */ }
  setTimeout(pollLive, 160);
}
async function pollState() {
  const st = await api.get_state(ver);
  if (st && !st.same) { S = st; ver = st.ver; renderAll(); }
}
function tickClock() {
  const d = new Date();
  $("#clock").textContent = d.toLocaleTimeString("ro-RO", { hour: "2-digit", minute: "2-digit" });
  $("#date").textContent = `${ZILE[d.getDay()]} · ${d.getDate()} ${LUNI[d.getMonth()]} ${d.getFullYear()}`;
}

/* ---------------------------------------------------------------- evenimente */
function bind() {
  // orb ticks
  let t = "";
  for (let i = 0; i < 60; i++) { const a = (i * 6) * Math.PI / 180, r1 = 91, r2 = i % 5 ? 93 : 96; t += `<line x1="${100 + r1 * Math.cos(a)}" y1="${100 + r1 * Math.sin(a)}" x2="${100 + r2 * Math.cos(a)}" y2="${100 + r2 * Math.sin(a)}"/>`; }
  $("#ticks").innerHTML = t;

  $$(".nav-btn").forEach((b) => b.onclick = () => {
    $$(".nav-btn").forEach((x) => x.classList.toggle("active", x === b));
    $$(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + b.dataset.view));
    $("#views").scrollTop = b.dataset.view === "jurnal" ? 1e9 : 0;
  });
  $("#orbWrap").onclick = () => api.mic();
  $("#btnMic").onclick = () => api.mic();
  $("#btnStop").onclick = () => api.stop();
  $("#btnBrief").onclick = () => { api.briefing(); toast("Pregătesc briefing-ul…"); };
  $("#btnReset").onclick = () => { api.reset_chat(); toast("Conversație nouă"); };
  $("#cmdForm").onsubmit = (e) => { e.preventDefault(); const v = $("#cmdInput").value.trim(); if (v) { api.send_text(v); $("#cmdInput").value = ""; } };
  $("#taskForm").onsubmit = (e) => { e.preventDefault(); const v = $("#taskInput").value.trim(); if (v) { api.add_task(v, null, $("#taskTime").value); $("#taskInput").value = ""; $("#taskTime").value = ""; } };
  document.addEventListener("click", (e) => {
    const li = e.target.closest(".tasks li[data-id]");
    const act = e.target.dataset.act;
    if (li && act === "toggle") api.toggle_task(li.dataset.id);
    if (li && act === "del") api.delete_task(li.dataset.id);
    const url = e.target.closest("[data-url]");
    if (url) { e.preventDefault(); api.open_url(url.dataset.url); }
    const rdel = e.target.dataset.rdel;
    if (rdel) api.research_delete(rdel);
    const proj = e.target.closest(".proj[data-pid]");
    if (proj) { openProject = proj.dataset.pid; renderProjectDetail(true); openOverlay("#projOverlay"); $("#projOverlay .sheet-body").scrollTop = 0; }
    if (e.target.closest("[data-close]")) closeOverlay("#" + e.target.closest(".overlay").id);
  });
  document.addEventListener("toggle", (e) => {
    const d = e.target; if (!d.dataset || !d.dataset.did) return;
    if (d.open) { openDetails.add(d.dataset.did); openDetails.delete("closed-" + d.dataset.did); }
    else { openDetails.delete(d.dataset.did); openDetails.add("closed-" + d.dataset.did); }
  }, true);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") $$(".overlay").forEach((o) => closeOverlay("#" + o.id)); });

  // proiecte
  $("#projForm").onsubmit = (e) => { e.preventDefault(); const v = $("#projInput").value.trim(); if (v) { api.project_add(v); $("#projInput").value = ""; } };
  const save = (field, el, transform = (x) => x) => el.addEventListener("change", () => openProject && api.project_update(openProject, { [field]: transform(el.value) }));
  save("name", $("#pdName")); save("stage", $("#pdStage")); save("progress", $("#pdProgress"), Number);
  save("next", $("#pdNext")); save("domain", $("#pdDomain")); save("objective", $("#pdObjective"));
  save("audience", $("#pdAudience")); save("resources", $("#pdResources"));
  $("#pdProgress").addEventListener("input", () => { $("#pdProgVal").textContent = $("#pdProgress").value + "%"; });
  $("#noteForm").onsubmit = (e) => { e.preventDefault(); const v = $("#noteInput").value.trim(); if (v && openProject) { api.project_update(openProject, { note: v }); $("#noteInput").value = ""; } };
  $("#pdStrategy").onclick = async () => { const r = await api.project_analyze(openProject, "strategie", $("#pdFocus").value); if (typeof r === "string") toast(r); else toast("Analiza a pornit. Te anunț când e gata."); };
  $("#pdProposals").onclick = async () => { const r = await api.project_analyze(openProject, "propuneri", ""); if (typeof r === "string") toast(r); else toast("Pregătesc propunerile…"); };
  $("#pdDelete").onclick = () => { const p = currentProject(); if (p && confirm(`Scot proiectul „${p.name}” din listă?`)) { api.project_delete(p.id); closeOverlay("#projOverlay"); } };

  // cercetare
  $("#researchForm").onsubmit = async (e) => { e.preventDefault(); const v = $("#researchInput").value.trim(); if (!v) return; const r = await api.research(v); if (typeof r === "string") toast(r); else { $("#researchInput").value = ""; toast("Cercetarea a pornit"); } };

  // actualizări
  const doUpdate = async () => { const r = await api.update_now(); toast(typeof r === "string" ? r : "Verific actualizările…"); };
  $("#btnUpdate").onclick = doUpdate;
  $("#btnDesign").onclick = async () => {
    const list = $("#designList");
    list.innerHTML = `<div class="working"><span class="spinner"></span> Creez variantele (~20 secunde)…</div>`;
    const r = await api.design_voice();
    if (r.error) { list.innerHTML = `<div class="hint" style="color:var(--red)">${esc(r.error)}</div>`; return; }
    list.innerHTML = Array.from({ length: r.count }, (_, i) => `<div class="add-row" style="margin-top:8px">
      <button type="button" class="secondary" style="flex:1" data-play="${i}">▶ Varianta ${i + 1}</button>
      <button type="button" class="primary" data-pick="${i}">✓ Aleg</button></div>`).join("");
  };
  $("#designList").addEventListener("click", async (e) => {
    const p = e.target.dataset.play, k = e.target.dataset.pick;
    if (p !== undefined) api.play_preview(+p);
    if (k !== undefined) {
      e.target.textContent = "Salvez…";
      const r = await api.choose_preview(+k);
      toast(r.error ? r.error : "Vocea e salvată. TARS vorbește de acum cu ea.");
      if (!r.error) { $("#setForm").elements.tts_engine.value = "elevenlabs"; $("#setForm").elements.elevenlabs_voice_id.value = r.voice_id; }
    }
  });
  $("#btnCompareGemini").onclick = async () => {
    const r = await api.compare_gemini_voices();
    if (typeof r === "string") { toast(r); return; }
    $("#compareHint").textContent = "Ascultați: " + r.voices.map((v, i) => `${i + 1} = ${v}`).join(", ") + ". Alegeți apoi din listă și apăsați Salvează.";
  };
  $("#btnTestVoice").onclick = async () => { const r = await api.test_voice(); toast(typeof r === "string" ? r : "Ascultați…"); };
  $("#btnUpdateNow").onclick = doUpdate;

  // setări
  $("#btnSettings").onclick = async () => {
    const s = await api.get_settings();
    const f = $("#setForm");
    try {
      const dev = await api.audio_devices();
      for (const [sel, list] of [["#selMic", dev.input || []], ["#selSpk", dev.output || []]]) {
        const el = $(sel); el.length = 1;
        list.forEach((n) => el.add(new Option(n, n)));
      }
    } catch (e) {}
    for (const [k, v] of Object.entries(s)) { const el = f.elements[k]; if (!el) continue; if (el.type === "checkbox") el.checked = !!v; else el.value = v ?? ""; }
    $("#setVersion").textContent = s.version || "—";
    $("#setMsg").textContent = ""; openOverlay("#setOverlay");
  };
  $("#setForm").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target, out = {};
    [...f.elements].forEach((el) => { if (!el.name) return; out[el.name] = el.type === "checkbox" ? el.checked : el.value; });
    const r = await api.save_settings(out);
    $("#setMsg").textContent = r && r.restart ? "Salvat. Unele schimbări de voce se aplică după repornirea lui TARS." : "Salvat.";
    toast("Setări salvate");
  };
}

/* ---------------------------------------------------------------- pornire */
function start(realApi) {
  api = realApi;
  bind(); tickClock(); setInterval(tickClock, 1000);
  pollState().then(() => { if (S && !S.ready) $("#btnSettings").click(); });
  pollLive();
}
window.addEventListener("pywebviewready", () => start(window.pywebview.api));
// Previzualizare în browser (fără Python): date de test
window.addEventListener("load", () => setTimeout(() => { if (!api && window.JARVIS_MOCK) start(window.JARVIS_MOCK); }, 300));
