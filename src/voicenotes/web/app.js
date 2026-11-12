"use strict";

/* Voicenotes UI. Talks to Python through QWebChannel object `vn`. */

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let vn = null;
const state = {
  meetings: [],
  currentId: null,
  current: null,
  tab: "summary",
  recordingId: null,
  live: [],
  elapsed: 0,
  me: "Ik",
  progress: {},       // meeting id -> latest progress line
  settings: null,
  folders: [],
  people: [],
  folder: null,       // selected folder id, null = all meetings
};

const FOLDER_ICON = `<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M2.75 5.5c0-.97.78-1.75 1.75-1.75h3.3l1.7 1.8h6c.97 0 1.75.78 1.75 1.75v7.2c0 .97-.78 1.75-1.75 1.75h-11c-.97 0-1.75-.78-1.75-1.75Z" fill="none" stroke="currentColor" stroke-width="1.3"/></svg>`;
const ALL_ICON = `<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="3" y="4" width="14" height="12" rx="2" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M3 8h14" stroke="currentColor" stroke-width="1.3"/></svg>`;

/* ---------- formatting ---------- */

const MONTHS = ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september", "oktober", "november", "december"];
const DAYS = ["zondag", "maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag"];

function fmtTime(sec) {
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const mm = String(m).padStart(2, "0"), ss = String(s).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}
function fmtDuration(sec) {
  if (sec < 60) return `${Math.round(sec)} sec`;
  const m = Math.round(sec / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} uur ${m % 60} min`;
}
function parseDate(iso) { return new Date(iso); }
function dayKey(d) { return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`; }
function groupLabel(d) {
  const now = new Date();
  const yest = new Date(now); yest.setDate(now.getDate() - 1);
  if (dayKey(d) === dayKey(now)) return "Vandaag";
  if (dayKey(d) === dayKey(yest)) return "Gisteren";
  const diff = (now - d) / 864e5;
  if (diff < 7) return "Afgelopen week";
  return `${MONTHS[d.getMonth()][0].toUpperCase()}${MONTHS[d.getMonth()].slice(1)} ${d.getFullYear()}`;
}
function hhmm(d) { return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; }
function longDate(d) { return `${DAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}, ${hhmm(d)}`; }
function sourceLabel(src) {
  if (src === "recording") return "Opname";
  if (src && src.startsWith("recording:")) {
    const app = src.slice(10);
    return ["Chrome", "Edge", "Arc", "Brave", "Firefox", "Safari"].includes(app) ? `Opname van call in ${app}` : `Opname van ${app}-call`;
  }
  if (src && src.startsWith("import:")) return `Geïmporteerd uit ${src.slice(7)}`;
  return src || "";
}

/* ---------- speakers & turns ---------- */

function mergeTurns(segments, maxGap = 1.5) {
  const turns = [];
  for (const s of [...segments].sort((a, b) => a.start - b.start)) {
    const last = turns[turns.length - 1];
    if (last && last.speaker === s.speaker && s.start - last.end <= maxGap) {
      last.end = Math.max(last.end, s.end);
      last.text += " " + s.text;
    } else {
      turns.push({ ...s });
    }
  }
  return turns;
}
function colorMap(names, me) {
  const map = {};
  let i = 0;
  for (const n of names) {
    if (n in map) continue;
    map[n] = n === me ? "var(--me)" : `var(--s${(i++ % 6) + 1})`;
  }
  return map;
}

/* ---------- sidebar: folders with their meetings, then meetings without a folder ---------- */

const CHEVRON = `<svg class="chev" viewBox="0 0 20 20" aria-hidden="true"><path d="m7.5 5 5 5-5 5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
state.expanded = new Set(JSON.parse(localStorage.getItem("vn.expanded") || "[]"));

function saveExpanded() { localStorage.setItem("vn.expanded", JSON.stringify([...state.expanded])); }
function folderName(id) { return state.folders.find((f) => f.id === id)?.name; }
function query() { return $("#search").value.trim().toLowerCase(); }
function matches(m, q) {
  return !q || m.title.toLowerCase().includes(q) || (folderName(m.folder) || "").toLowerCase().includes(q)
    || (m.summaryText || "").toLowerCase().includes(q);
}

/* A short piece of the summary around the search term, with the term highlighted. */
function snippet(m, q) {
  const text = (m.summaryText || "").replace(/\s+/g, " ");
  const i = text.toLowerCase().indexOf(q);
  if (!q || i < 0 || m.title.toLowerCase().includes(q)) return "";
  const start = Math.max(0, i - 40), end = Math.min(text.length, i + q.length + 60);
  const before = (start > 0 ? "…" : "") + text.slice(start, i);
  const after = text.slice(i + q.length, end) + (end < text.length ? "…" : "");
  return `<div class="snip">${esc(before)}<mark>${esc(text.slice(i, i + q.length))}</mark>${esc(after)}</div>`;
}

function itemHtml(m, withFolder = false) {
  const d = parseDate(m.created);
  const badge = m.recording ? '<i class="badge rec" title="Opname loopt"></i>'
    : m.busy ? '<i class="badge busy" title="Wordt verwerkt"></i>' : "";
  let sub = m.recording ? "Opname loopt" : m.busy ? (state.progress[m.id] || "Wordt verwerkt")
    : `${d.getDate()} ${MONTHS[d.getMonth()].slice(0, 3)}, ${hhmm(d)}`;
  if (withFolder && m.folder !== null && folderName(m.folder)) sub += `  ${folderName(m.folder)}`;
  return `<button class="item${m.id === state.currentId ? " active" : ""}" data-id="${m.id}" draggable="true">
    <div class="t">${badge}<span>${esc(m.title)}</span></div><div class="s">${esc(sub)}</div>${withFolder ? snippet(m, query()) : ""}</button>`;
}

function renderSidebar() {
  const q = query();
  const folders = $("#folders"), list = $("#meetings");
  if (q) {   // search: one flat list over everything
    const hits = state.meetings.filter((m) => matches(m, q));
    folders.innerHTML = "";
    list.innerHTML = hits.length ? `<div class="group">Zoekresultaten</div>` + hits.map((m) => itemHtml(m, true)).join("")
      : `<p class="no-results">Niets gevonden voor "${esc(q)}".</p>`;
    return;
  }
  folders.innerHTML = state.folders.map((f) => {
    const inside = state.meetings.filter((m) => m.folder === f.id);
    const open = state.expanded.has(f.id);
    const live = inside.some((m) => m.recording || m.busy);
    return `<div class="folder-block${open ? " open" : ""}">
      <div class="folder${state.folder === f.id ? " target" : ""}" data-folder="${f.id}" role="button" tabindex="0" aria-expanded="${open}" title="Dubbelklik om de naam te wijzigen">
        ${CHEVRON}<span class="fname">${esc(f.name)}</span>${live ? '<i class="badge busy"></i>' : ""}
        <span class="count">${inside.length}</span>
        <span class="del" role="button" data-del="${f.id}" title="Map verwijderen" aria-label="Map ${esc(f.name)} verwijderen">×</span>
      </div>
      ${open ? `<div class="folder-items">${inside.length ? inside.map((m) => itemHtml(m)).join("")
        : `<p class="folder-empty">Sleep een meeting hierheen</p>`}</div>` : ""}
    </div>`;
  }).join("");
  // Below the folders: only meetings that are not in a folder.
  const loose = state.meetings.filter((m) => m.folder === null);
  let html = "", lastGroup = null;
  for (const m of loose) {
    const g = groupLabel(parseDate(m.created));
    if (g !== lastGroup) { html += `<div class="group">${esc(g)}</div>`; lastGroup = g; }
    html += itemHtml(m);
  }
  if (!loose.length && state.folders.length && state.meetings.length) html = `<p class="folder-empty loose">Alle meetings staan in een map.</p>`;
  list.innerHTML = html;
}
const renderFolders = renderSidebar, renderList = renderSidebar;

function loadMeetings() {
  vn.listFolders((fjson) => {
    state.folders = JSON.parse(fjson);
    if (state.folder !== null && !state.folders.some((f) => f.id === state.folder)) setTarget(null);
    vn.listMeetings((json) => {
      state.meetings = JSON.parse(json);
      if (state.currentId && !state.meetings.some((m) => m.id === state.currentId)) state.currentId = null;
      if (state.currentId === null && state.meetings.length) {
        state.currentId = state.meetings[0].id;
        loadCurrent(true);
      }
      // Keep the folder of the open meeting expanded so it stays visible.
      const cur = state.meetings.find((m) => m.id === state.currentId);
      if (cur && cur.folder !== null && !state.expanded.has(cur.folder)) { state.expanded.add(cur.folder); saveExpanded(); }
      renderSidebar();
      updateAskScope();
      showEmpty(!state.meetings.length);
    });
  });
}

/* New recordings and imports go into the "target" folder: the folder you last opened or the
   folder of the meeting you are looking at. */
function setTarget(fid) {
  state.folder = fid;
  vn.setActiveFolder(fid === null ? -1 : fid);
}

function toggleFolder(fid) {
  state.expanded.has(fid) ? state.expanded.delete(fid) : state.expanded.add(fid);
  saveExpanded();
  setTarget(state.expanded.has(fid) ? fid : null);
  renderSidebar();
}

function newFolder(onCreated) {
  $("#search").value = "";
  renderSidebar();
  const el = $("#folders");
  const row = document.createElement("div");
  row.className = "folder new";
  row.innerHTML = `${CHEVRON}<input type="text" placeholder="Naam van de map" aria-label="Naam van de nieuwe map">`;
  el.prepend(row);
  const input = row.querySelector("input");
  input.focus();
  let done = false;
  const finish = (save) => {
    if (done) return;
    done = true;
    const name = input.value.trim();
    row.remove();
    if (save && name) vn.createFolder(name, (fid) => {
      if (fid < 0) return;
      state.expanded.add(fid); saveExpanded(); setTarget(fid);
      if (onCreated) onCreated(fid);
    });
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); });
  input.addEventListener("blur", () => finish(true));
}

function renameFolder(row) {
  const fid = Number(row.dataset.folder);
  const name = folderName(fid);
  const span = row.querySelector(".fname");
  const input = document.createElement("input");
  input.value = name;
  span.replaceWith(input);
  input.focus(); input.select();
  let done = false;
  const finish = (save) => {
    if (done) return;
    done = true;
    const v = input.value.trim();
    if (save && v && v !== name) vn.renameFolder(fid, v); else renderSidebar();
  };
  input.addEventListener("click", (e) => e.stopPropagation());
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); });
  input.addEventListener("blur", () => finish(true));
}

function updateAskScope() {
  const f = state.current?.folder ?? null;
  $("#askAllLabel").textContent = f !== null && folderName(f) ? `hele map ${folderName(f)}` : "alle meetings";
}

function showEmpty(empty) {
  $("#emptyState").hidden = !empty;
  $("#meetingView").hidden = empty;
  if (empty) $("#answers").hidden = true;
}

/* ---------- meeting view ---------- */

function select(id) {
  if (id === state.currentId) return;
  state.currentId = id;
  const m = state.meetings.find((x) => x.id === id);
  if (m) setTarget(m.folder);
  renderSidebar();
  loadCurrent(true);
}

function loadCurrent(resetScroll = false) {
  if (state.currentId === null) return;
  vn.getMeeting(state.currentId, (json) => {
    const m = JSON.parse(json);
    if (!m) return;
    state.current = m;
    state.me = m.me;
    vn.setSelection(m.id, m.folder ?? -1);
    updateAskScope();
    if (m.recording && state.tab === "summary") state.tab = "transcript";
    renderMeeting();
    if (resetScroll) $("#scroll").scrollTop = 0;
  });
}

function isLive() { return state.current && state.current.id === state.recordingId; }

function currentSegments() {
  return isLive() ? state.live : (state.current?.segments || []);
}

function renderMeeting() {
  const m = state.current;
  if (!m) return;
  const title = $("#title");
  if (title.getAttribute("contenteditable") !== "true") title.textContent = m.title;

  const segs = currentSegments();
  const end = isLive() ? state.elapsed : segs.reduce((a, s) => Math.max(a, s.end), 0);
  const d = parseDate(m.created);
  const metaParts = [longDate(d)];
  if (end > 0 && !isLive()) metaParts.push(fmtDuration(end));
  metaParts.push(sourceLabel(m.source));
  if (m.folder !== null && folderName(m.folder)) metaParts.push(folderName(m.folder));
  if (m.speakers.length && !isLive()) metaParts.push(m.speakers.length === 1 ? "1 spreker" : `${m.speakers.length} sprekers`);
  $("#meta").innerHTML = metaParts.map((p) => `<span>${esc(p)}</span>`).join("");

  const working = m.busy && !isLive();
  $("#working").hidden = !working;
  $("#workingText").textContent = state.progress[m.id] || m.progress || "Wordt verwerkt";
  $("#liveHead").hidden = !isLive();

  renderStrip();
  renderTabs();
  renderSummary();
  renderTranscript();
  renderSpeakers();
}

function renderStrip() {
  const segs = currentSegments();
  const turns = mergeTurns(segs);
  const lastEnd = turns.reduce((a, t) => Math.max(a, t.end), 0);
  const total = isLive() ? Math.max(state.elapsed, lastEnd, 1) : lastEnd;
  const strip = $("#strip"), legend = $("#legend");
  if (!turns.length || !total) {
    strip.hidden = !isLive();
    strip.innerHTML = isLive() ? `<span class="now" style="left:calc(100% - 2px)"></span>` : "";
    legend.innerHTML = "";
    return;
  }
  strip.hidden = false;
  const colors = colorMap(turns.map((t) => t.speaker), state.me);
  strip.innerHTML = turns.map((t, i) => {
    const left = (t.start / total) * 100, width = ((t.end - t.start) / total) * 100;
    return `<i data-turn="${i}" style="left:${left}%;width:${width}%;--c:${colors[t.speaker]}" title="${esc(t.speaker)} om ${fmtTime(t.start)}"></i>`;
  }).join("") + (isLive() ? `<span class="now" style="left:calc(100% - 2px)"></span>` : "");
  legend.innerHTML = Object.entries(colors).map(([n, c]) => `<span><b style="--c:${c}"></b>${esc(n)}</span>`).join("");
}

function renderTabs() {
  for (const b of document.querySelectorAll("#tabs button")) {
    b.setAttribute("aria-selected", String(b.dataset.tab === state.tab));
  }
  for (const t of ["summary", "transcript", "speakers"]) $(`#pane-${t}`).hidden = t !== state.tab;
}

function renderSummary() {
  const m = state.current, el = $("#pane-summary");
  if (isLive()) {
    el.innerHTML = `<p class="placeholder">De samenvatting komt zodra je de opname stopt. Live meelezen kan in het transcript.</p>`;
  } else if (m.summaryHtml) {
    el.innerHTML = m.summaryHtml;
  } else if (m.busy) {
    el.innerHTML = `<p class="placeholder">De samenvatting wordt gemaakt. Je kunt het transcript al lezen zodra het klaar is.</p>`;
  } else {
    el.innerHTML = `<p class="placeholder">Er is nog geen samenvatting. Kies "Samenvatting opnieuw maken" in het menu rechtsboven.</p>`;
  }
}

function renderTranscript() {
  const el = $("#pane-transcript");
  const sc = $("#scroll");
  const nearBottom = sc.scrollHeight - sc.scrollTop - sc.clientHeight < 80;
  const turns = mergeTurns(currentSegments());
  const colors = colorMap(turns.map((t) => t.speaker), state.me);
  let html = "";
  if (isLive()) {
    html += `<p class="live-hint">Live meelezen. Na het stoppen komt er een nauwkeuriger versie met namen per spreker.</p>`;
  }
  if (!turns.length) {
    html += isLive()
      ? `<p class="placeholder">Wacht op spraak…</p>`
      : `<p class="placeholder">${state.current.busy ? "Het transcript wordt gemaakt." : "Er is geen spraak gevonden in deze opname."}</p>`;
  }
  html += turns.map((t, i) => `<div class="turn" id="turn-${i}" style="--c:${colors[t.speaker]}">
      <button class="time" data-turn="${i}" tabindex="-1">${fmtTime(t.start)}</button>
      <div><div class="who">${esc(t.speaker || "Onbekend")}</div><div class="text">${esc(t.text)}</div></div></div>`).join("");
  el.innerHTML = html;
  if (isLive() && state.tab === "transcript" && nearBottom) sc.scrollTop = sc.scrollHeight;
}

function renderSpeakers() {
  const m = state.current, el = $("#pane-speakers");
  if (isLive()) {
    el.innerHTML = `<p class="placeholder">Sprekers worden herkend zodra je de opname stopt.</p>`;
    return;
  }
  if (!m.speakers.length) {
    el.innerHTML = `<p class="placeholder">Nog geen sprekers.</p>`;
    return;
  }
  const colors = colorMap(m.segments.map((s) => s.speaker), state.me);
  const total = m.speakers.reduce((a, s) => a + s.talk, 0) || 1;
  // Speakers linked to the same person are shown as merged.
  const byPerson = {};
  for (const s of m.speakers) if (s.personId !== null) (byPerson[s.personId] ||= []).push(s);
  const rows = [...m.speakers].sort((a, b) => b.talk - a.talk).map((s) => {
    const pct = Math.round((s.talk / total) * 100);
    const badge = s.mic ? '<span class="tag">jij, via je microfoon</span>'
      : s.voiceKnown ? '<span class="tag known">stem bekend</span>'
      : s.linked ? '<span class="tag">gekoppeld</span>' : '<span class="tag">nieuwe stem</span>';
    const others = s.personId !== null ? byPerson[s.personId].filter((o) => o.label !== s.label) : [];
    const merged = others.length ? `<div class="linkinfo">Samengevoegd met ${others.map((o) => esc(o.defaultName)).join(" en ")}</div>` : "";
    const picker = s.mic ? "" : `<form class="who-form" data-label="${esc(s.label)}">
        <input type="text" list="peopleNames" value="${s.linked ? esc(s.name) : ""}" placeholder="Wie is dit?" aria-label="Persoon voor ${esc(s.defaultName)}">
        <button class="ghost" type="submit">${s.linked ? "Wijzigen" : "Opslaan"}</button>
        ${s.linked ? `<button class="unlink" type="button" data-unlink="${esc(s.label)}" title="Terug naar ${esc(s.defaultName)}">Ontkoppelen</button>` : ""}
      </form>`;
    return `<div class="spk" style="--c:${colors[s.name] || "var(--s1)"}">
      <div class="name"><i></i>${esc(s.name)} ${s.linked && !s.mic && s.defaultName !== s.name ? `<span class="code">${esc(s.defaultName)}</span>` : ""} ${badge}</div>${picker}
      ${merged}
      <div class="share"><div class="bar"><b style="width:${pct}%"></b></div><span>${fmtDuration(s.talk)}, ${pct}%</span></div>
      <blockquote>“${esc(s.sample)}”</blockquote></div>`;
  }).join("");
  const intro = `<p class="spk-intro">Typ een naam of kies iemand die je al kent. Kies je bij meerdere sprekers dezelfde persoon, dan worden ze samengevoegd. Namen in de samenvatting passen zich direct aan.</p>`;
  el.innerHTML = intro + rows;
}

/* ---------- people ---------- */

function loadPeople(then) {
  vn.listPeople((json) => {
    state.people = JSON.parse(json);
    $("#peopleNames").innerHTML = state.people.map((p) => `<option value="${esc(p.name)}"></option>`).join("");
    if ($("#peopleSheet").open) renderPeople();
    if (then) then();
  });
}

function renderPeople() {
  const el = $("#peopleList");
  if (!state.people.length) {
    el.innerHTML = `<p class="hint">Nog niemand. Voeg iemand toe, of geef een spreker in een meeting een naam.</p>`;
    return;
  }
  el.innerHTML = state.people.map((p) => {
    const others = state.people.filter((o) => o.id !== p.id);
    const merge = others.length ? `<select data-merge="${p.id}" aria-label="${esc(p.name)} samenvoegen met">
        <option value="">Samenvoegen met…</option>
        ${others.map((o) => `<option value="${o.id}">${esc(o.name)}</option>`).join("")}</select>` : "<span></span>";
    return `<div class="person">
      <input class="pname" value="${esc(p.name)}" data-person="${p.id}" aria-label="Naam">
      ${merge}
      ${p.isMe ? "<span></span>" : `<button class="del" type="button" data-delperson="${p.id}" title="Verwijderen" aria-label="${esc(p.name)} verwijderen">×</button>`}
      <div class="meta2">${p.isMe ? '<span class="badge2">jij</span>' : ""}${p.hasVoice ? '<span class="badge2 voice">stem bekend</span>' : ""}
        <span>${p.meetings === 1 ? "1 meeting" : `${p.meetings} meetings`}</span></div>
    </div>`;
  }).join("");
}

function openPeople() {
  loadPeople(() => { renderPeople(); $("#peopleSheet").showModal(); });
}

function jumpToTurn(i) {
  state.tab = "transcript";
  renderTabs();
  const el = document.getElementById(`turn-${i}`);
  if (!el) return;
  el.scrollIntoView({ block: "center", behavior: "smooth" });
  el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash");
}

/* ---------- recording ---------- */

function setRecording(active, id) {
  state.recordingId = active ? id : null;
  const btn = $("#recordBtn");
  btn.classList.toggle("on", active);
  btn.querySelector(".label").textContent = active ? "Opname loopt" : "Opname starten";
  btn.disabled = active;
  btn.title = active ? "Stop de opname met 'Stoppen en verwerken' bij de meeting" : "";
  $("#sideTimer").textContent = "";
  if (active) {
    state.live = [];
    state.elapsed = 0;
    state.currentId = id;
    state.tab = "transcript";
  }
  loadMeetings();
  loadCurrent();
}

function onLevels(json) {
  const l = JSON.parse(json);
  state.elapsed = l.elapsed;
  const t = fmtTime(l.elapsed);
  $("#sideTimer").textContent = t;
  if (isLive()) {
    $("#clock").textContent = t;
    const pct = (v) => `${Math.min(100, Math.sqrt(v) * 260)}%`;
    $("#micBar").style.width = pct(l.mic);
    $("#sysBar").style.width = pct(l.system);
    // Move the strip forward once per second.
    const sec = Math.floor(l.elapsed);
    if (sec !== state.stripSecond) { state.stripSecond = sec; renderStrip(); }
  }
}

function onLive(json) {
  const p = JSON.parse(json);
  if (p.id !== state.recordingId) return;
  state.live = p.segments;
  if (isLive()) { renderStrip(); renderTranscript(); }
}

/* ---------- questions ---------- */

let reqSeq = 0;
function ask(question, all) {
  const req = `q${++reqSeq}`;
  const panel = $("#answers"), list = $("#answerList");
  panel.hidden = false;
  const folder = state.current?.folder ?? null;
  const inFolder = all && folder !== null;
  const scope = inFolder ? `Over de map ${folderName(folder)}` : all ? "Over alle meetings" : `Over ${state.current?.title || "deze meeting"}`;
  const div = document.createElement("div");
  div.className = "qa";
  div.id = req;
  div.innerHTML = `<div class="q">${esc(question)}</div><div class="scope-note">${esc(scope)}</div>
    <div class="a"><span class="thinking"><span class="spinner"></span>Zoeken in het transcript…</span></div>`;
  list.appendChild(div);
  list.scrollTop = list.scrollHeight;
  vn.ask(req, question, all ? -1 : state.currentId, inFolder ? folder : -1);
}
function onAnswer(json) {
  const r = JSON.parse(json);
  const div = document.getElementById(r.req);
  if (!div) return;
  const a = div.querySelector(".a");
  if (r.error) {
    a.classList.add("err");
    a.textContent = `Geen antwoord: ${r.error}. Controleer of LM Studio draait en er een model geladen is.`;
  } else {
    a.innerHTML = r.html;
  }
}

/* ---------- settings ---------- */

function openSettings() {
  vn.getSettings((json) => {
    const s = (state.settings = JSON.parse(json));
    $("#sName").value = s.my_name;
    $("#sVocab").value = s.vocabulary.join("\n");
    $("#sWhisper").value = s.whisper_model;
    const llm = $("#sLlm");
    llm.innerHTML = `<option value="">Automatisch: het geladen model met de meeste context</option>` +
      s.llm_models.map((m) => {
        const ctx = s.llm_contexts?.[m];
        return `<option value="${esc(m)}">${esc(m)}${ctx ? `, context ${ctx.toLocaleString("nl-NL")} tokens` : ""}</option>`;
      }).join("");
    if (s.llm_model && !s.llm_models.includes(s.llm_model)) {
      llm.insertAdjacentHTML("beforeend", `<option value="${esc(s.llm_model)}">${esc(s.llm_model)}</option>`);
    }
    llm.value = s.llm_model;
    const hint = $("#sLlmHint");
    hint.textContent = s.lmstudio_ok ? "Samenvattingen en vragen lopen via LM Studio op deze Mac."
      : "LM Studio is niet bereikbaar. Open LM Studio en start de server, bijvoorbeeld met: lms server start";
    hint.classList.toggle("warn", !s.lmstudio_ok);
    $("#sLive").checked = s.live_transcription;
    $("#sThinking").checked = s.llm_thinking;
    $("#sDetect").checked = s.detect_calls;
    $("#sTheme").value = s.theme || "dark";
    $("#sHideShare").checked = s.hide_from_screen_share;
    $("#sLogin").checked = s.start_at_login;
    $("#loginField").hidden = !s.app_installed;
    $("#sDictation").checked = s.dictation;
    $("#sCleanup").checked = s.dictation_cleanup;
    $("#sDictModel").value = s.dictation_model;
    $("#sDictOpts").classList.toggle("off", !s.dictation);
    const perm = (ok, label, why, pane) => `<span class="st ${ok ? "ok" : "no"}"></span>
      <span>${label}: ${ok ? "toegestaan" : `nog niet toegestaan. ${why}`}</span>
      ${ok ? "" : `<button type="button" data-open="${pane}">Openen</button>`}`;
    $("#permInput").innerHTML = perm(s.perm_input, "Invoerbewaking", "Nodig om fn te herkennen.", "input");
    $("#permPaste").innerHTML = perm(s.perm_paste, "Toegankelijkheid", "Nodig om tekst te plakken.", "accessibility");
    $("#sApps").innerHTML = s.call_apps.map((a) => `<label class="app-row"><span>${esc(a.name)}</span>
        <select data-app="${esc(a.name)}">
          <option value="ask"${a.mode === "ask" ? " selected" : ""}>Vragen</option>
          <option value="auto"${a.mode === "auto" ? " selected" : ""}>Altijd opnemen</option>
          <option value="ignore"${a.mode === "ignore" ? " selected" : ""}>Nooit</option>
        </select></label>`).join("");
    $("#sApps").classList.toggle("off", !s.detect_calls);
    $("#sDiarize").checked = s.diarize;
    $("#sThreshold").value = s.speaker_threshold;
    $("#settings").showModal();
  });
}
function saveSettings() {
  vn.saveSettings(JSON.stringify({
    my_name: $("#sName").value,
    vocabulary: $("#sVocab").value.split("\n"),
    whisper_model: $("#sWhisper").value,
    llm_model: $("#sLlm").value,
    llm_thinking: $("#sThinking").checked,
    live_transcription: $("#sLive").checked,
    detect_calls: $("#sDetect").checked,
    theme: $("#sTheme").value,
    hide_from_screen_share: $("#sHideShare").checked,
    start_at_login: $("#sLogin").checked,
    dictation: $("#sDictation").checked,
    dictation_cleanup: $("#sCleanup").checked,
    dictation_model: $("#sDictModel").value,
    call_apps: Object.fromEntries([...document.querySelectorAll("#sApps select")].map((el) => [el.dataset.app, el.value])),
    diarize: $("#sDiarize").checked,
    speaker_threshold: parseFloat($("#sThreshold").value),
  }));
  $("#settings").close();
}

/* ---------- confirm & toast ---------- */

function confirmSheet(title, text, yes, onYes) {
  $("#confirmTitle").textContent = title;
  $("#confirmText").textContent = text;
  $("#confirmYes").textContent = yes;
  const dlg = $("#confirm");
  $("#confirmYes").onclick = () => { dlg.close(); onYes(); };
  $("#confirmNo").onclick = () => dlg.close();
  dlg.showModal();
}

let toastTimer = null;
function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 3500);
}

/* ---------- events ---------- */

function bindEvents() {
  // The sidebar button only starts. While recording it just shows the time, so a stray
  // click can't end a meeting; stopping is done with "Stoppen en verwerken".
  $("#recordBtn").addEventListener("click", () => { if (state.recordingId === null) vn.startRecording(); });
  $("#stopBtn").addEventListener("click", () => vn.stopRecording());

  // "…" menu of the open meeting
  const menu = $("#moreMenu");
  $("#moreBtn").addEventListener("click", (e) => {
    e.stopPropagation();
    if (menu.hidden) renderMoveMenu();
    menu.hidden = !menu.hidden;
  });
  document.addEventListener("click", (e) => { if (!menu.contains(e.target)) menu.hidden = true; });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") menu.hidden = true; });
  menu.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b || b.classList.contains("has-sub")) return;
    if (b.dataset.move !== undefined) {
      menu.hidden = true;
      if (b.dataset.move === "new") return newFolder((fid) => vn.moveMeeting(state.current.id, fid));
      const fid = Number(b.dataset.move);
      vn.moveMeeting(state.current.id, fid);
      if (fid >= 0) { state.expanded.add(fid); saveExpanded(); }
      return toast(fid < 0 ? "Uit de map gehaald" : `Verplaatst naar ${folderName(fid)}`);
    }
    if (b.dataset.cmd) { menu.hidden = true; runCommand(b.dataset.cmd); }
  });
  $("#search").addEventListener("input", renderSidebar);
  $("#search").addEventListener("keydown", (e) => { if (e.key === "Escape") { e.target.value = ""; renderSidebar(); e.target.blur(); } });

  // Sidebar tree
  const tree = document.querySelector(".tree");
  tree.addEventListener("click", (e) => {
    const del = e.target.closest("[data-del]");
    if (del) {
      e.stopPropagation();
      const fid = Number(del.dataset.del);
      confirmSheet("Map verwijderen?", `De map "${folderName(fid)}" verdwijnt. De meetings erin blijven bewaard, zonder map.`,
        "Map verwijderen", () => vn.deleteFolder(fid));
      return;
    }
    const item = e.target.closest(".item");
    if (item) return select(Number(item.dataset.id));
    const f = e.target.closest(".folder[data-folder]");
    if (f && e.target.tagName !== "INPUT") toggleFolder(Number(f.dataset.folder));
  });
  tree.addEventListener("keydown", (e) => {
    const f = e.target.closest(".folder[data-folder]");
    if (f && (e.key === "Enter" || e.key === " ") && e.target.tagName !== "INPUT") { e.preventDefault(); toggleFolder(Number(f.dataset.folder)); }
  });
  tree.addEventListener("dblclick", (e) => {
    const f = e.target.closest(".folder[data-folder]");
    if (f) { toggleFolder(Number(f.dataset.folder)); renameFolder(document.querySelector(`.folder[data-folder="${f.dataset.folder}"]`)); }
  });
  // Drag a meeting onto a folder (or onto the loose list) to move it
  tree.addEventListener("dragstart", (e) => {
    const item = e.target.closest(".item");
    if (item) { e.dataTransfer.setData("text/x-meeting", item.dataset.id); e.dataTransfer.effectAllowed = "move"; }
  });
  const dropTarget = (e) => e.target.closest(".folder-block") || e.target.closest("#meetings");
  tree.addEventListener("dragover", (e) => {
    const t = dropTarget(e);
    if (!t || !e.dataTransfer.types.includes("text/x-meeting")) return;
    e.preventDefault();
    tree.querySelectorAll(".drop").forEach((x) => x !== t && x.classList.remove("drop"));
    t.classList.add("drop");
  });
  tree.addEventListener("dragleave", (e) => { if (!tree.contains(e.relatedTarget)) tree.querySelectorAll(".drop").forEach((x) => x.classList.remove("drop")); });
  tree.addEventListener("drop", (e) => {
    const t = dropTarget(e);
    tree.querySelectorAll(".drop").forEach((x) => x.classList.remove("drop"));
    const mid = Number(e.dataTransfer.getData("text/x-meeting"));
    if (!t || !mid) return;
    e.preventDefault();
    const block = t.classList.contains("folder-block") ? t.querySelector(".folder") : null;
    const fid = block ? Number(block.dataset.folder) : -1;
    if (fid >= 0) { state.expanded.add(fid); saveExpanded(); }
    vn.moveMeeting(mid, fid);
    toast(fid < 0 ? "Uit de map gehaald" : `Verplaatst naar ${folderName(fid)}`);
  });
  document.querySelector(".empty-actions").addEventListener("click", (e) => {
    const a = e.target.closest("button")?.dataset.action;
    if (a === "record") vn.toggleRecording();
    if (a === "import") vn.importFile();
  });

  $("#tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    state.tab = b.dataset.tab;
    renderTabs();
  });
  $("#strip").addEventListener("click", (e) => {
    const i = e.target.closest("i")?.dataset.turn;
    if (i !== undefined) jumpToTurn(Number(i));
  });

  // Inline rename of the title
  const title = $("#title");
  title.addEventListener("dblclick", startRename);
  title.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); title.blur(); }
    if (e.key === "Escape") { title.textContent = state.current.title; title.blur(); }
  });
  title.addEventListener("blur", () => {
    if (title.getAttribute("contenteditable") !== "true") return;
    title.setAttribute("contenteditable", "false");
    const t = title.textContent.trim();
    if (t && t !== state.current.title) { state.current.title = t; vn.rename(state.current.id, t); }
    else title.textContent = state.current.title;
  });

  // Speaker naming
  $("#pane-speakers").addEventListener("submit", (e) => {
    e.preventDefault();
    const form = e.target.closest("form.who-form");
    const name = form.querySelector("input").value.trim();
    if (!name) return form.querySelector("input").focus();
    vn.assignSpeaker(state.current.id, form.dataset.label, name);
    toast(`Gekoppeld aan ${name}`);
  });
  $("#pane-speakers").addEventListener("click", (e) => {
    const label = e.target.closest("[data-unlink]")?.dataset.unlink;
    if (label) vn.assignSpeaker(state.current.id, label, "");
  });

  // People
  $("#openPeopleFromSettings").addEventListener("click", (e) => { e.preventDefault(); $("#settings").close(); openPeople(); });
  $("#peopleDone").addEventListener("click", () => $("#peopleSheet").close());
  const addPerson = () => {
    const input = $("#newPersonName"), name = input.value.trim();
    if (!name) return input.focus();
    vn.createPerson(name, () => { input.value = ""; });
  };
  $("#addPersonBtn").addEventListener("click", addPerson);
  $("#newPersonName").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addPerson(); } });
  $("#peopleList").addEventListener("change", (e) => {
    const sel = e.target.closest("select[data-merge]");
    if (sel && sel.value) {
      const src = state.people.find((p) => p.id === Number(sel.dataset.merge));
      const dst = state.people.find((p) => p.id === Number(sel.value));
      sel.value = "";
      confirmSheet(`${src.name} samenvoegen met ${dst.name}?`,
        `Alles van ${src.name} gaat naar ${dst.name}: de stem en alle meetings. ${src.name} verdwijnt uit de lijst.`,
        "Samenvoegen", () => vn.mergePeople(src.id, dst.id));
    }
  });
  $("#peopleList").addEventListener("focusout", (e) => {
    const input = e.target.closest("input[data-person]");
    if (!input) return;
    const p = state.people.find((x) => x.id === Number(input.dataset.person));
    const v = input.value.trim();
    if (p && v && v !== p.name) vn.renamePerson(p.id, v); else if (p) input.value = p.name;
  });
  $("#peopleList").addEventListener("keydown", (e) => {
    if (e.target.matches("input[data-person]") && e.key === "Enter") { e.preventDefault(); e.target.blur(); }
  });
  $("#peopleList").addEventListener("click", (e) => {
    const id = e.target.closest("[data-delperson]")?.dataset.delperson;
    if (!id) return;
    const p = state.people.find((x) => x.id === Number(id));
    confirmSheet(`${p.name} verwijderen?`,
      `Sprekers die aan ${p.name} gekoppeld zijn, krijgen weer hun oorspronkelijke naam (zoals "Spreker 2"). De stem van ${p.name} wordt vergeten.`,
      "Verwijderen", () => vn.deletePerson(p.id));
  });

  $("#pane-transcript").addEventListener("click", (e) => {
    const b = e.target.closest(".time");
    if (b) jumpToTurn(Number(b.dataset.turn));
  });

  // Questions
  $("#askForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = $("#askInput").value.trim();
    if (!q) return;
    $("#askInput").value = "";
    ask(q, $("#askAll").checked);
  });
  $("#closeAnswers").addEventListener("click", () => ($("#answers").hidden = true));

  // Settings
  $("#settingsSave").addEventListener("click", saveSettings);
  $("#sDictation").addEventListener("change", (e) => $("#sDictOpts").classList.toggle("off", !e.target.checked));
  $("#settings").addEventListener("click", (e) => {
    const pane = e.target.closest("[data-open]")?.dataset.open;
    if (pane) { e.preventDefault(); vn.openSystemSettings(pane); }
  });
  $("#sDetect").addEventListener("change", (e) => $("#sApps").classList.toggle("off", !e.target.checked));
  $("#settingsCancel").addEventListener("click", () => $("#settings").close());

  // Keyboard shortcuts live in the macOS menu bar (Archief, Meeting, Weergave).
}

function renderMoveMenu() {
  const cur = state.current?.folder ?? null;
  const opt = (v, label, on) => `<button role="menuitem" data-move="${v}"${on ? ' class="current"' : ""}>${esc(label)}</button>`;
  $("#moveMenu").innerHTML = opt(-1, "Geen map", cur === null) +
    state.folders.map((f) => opt(f.id, f.name, cur === f.id)).join("") +
    `<hr><button role="menuitem" data-move="new">Nieuwe map…</button>`;
}

/* Menu bar actions arrive here from Python. */
function runCommand(cmd) {
  if (cmd === "settings") return openSettings();
  if (cmd === "people") return openPeople();
  if (cmd === "newFolder") return newFolder();
  if (cmd === "find") { $("#search").focus(); $("#search").select(); return; }
  const m = state.current;
  if (!m) return;
  if (cmd.startsWith("tab:")) { state.tab = cmd.slice(4); renderTabs(); return; }
  if (cmd === "answers") { const a = $("#answers"); a.hidden = !a.hidden; if (!a.hidden) $("#askInput").focus(); return; }
  if (cmd === "rename") return startRename();
  if (cmd === "reveal") return vn.reveal(m.id);
  if (m.busy || isLive()) return toast("Deze meeting is nog bezig. Probeer het zo opnieuw.");
  if (cmd === "resummarize") vn.resummarize(m.id);
  if (cmd === "reprocess") vn.reprocess(m.id);
  if (cmd === "delete") {
    confirmSheet("Meeting verwijderen?", `"${m.title}" wordt met de opname en het transcript van deze Mac verwijderd. Dit kun je niet ongedaan maken.`,
      "Verwijderen", () => { vn.deleteMeeting(m.id); state.currentId = null; state.current = null; vn.setSelection(-1, -1); });
  }
}

function startRename() {
  const title = $("#title");
  title.setAttribute("contenteditable", "true");
  title.focus();
  const r = document.createRange();
  r.selectNodeContents(title);
  const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
}

/* ---------- boot ---------- */

new QWebChannel(qt.webChannelTransport, (channel) => {
  vn = channel.objects.vn;
  bindEvents();

  vn.meetingsChanged.connect(() => loadMeetings());
  vn.meetingChanged.connect((id) => { if (id === state.currentId && !isLive()) loadCurrent(); });
  vn.status.connect(toast);
  vn.recording.connect((json) => { const r = JSON.parse(json); setRecording(r.active, r.id); });
  vn.live.connect(onLive);
  vn.levels.connect(onLevels);
  vn.progress.connect((json) => {
    const p = JSON.parse(json);
    if (p.id !== null) {
      if (p.done) delete state.progress[p.id]; else state.progress[p.id] = p.line;
      if (p.id === state.currentId && state.current) $("#workingText").textContent = p.line;
    }
    if (p.done) toast(p.ok ? `${p.label}: klaar` : `${p.label} is mislukt. ${p.line.replace(/^Mislukt: /, "")}`);
    renderList();
  });
  vn.answer.connect(onAnswer);
  vn.command.connect(runCommand);
  vn.peopleChanged.connect(() => { loadPeople(); if (state.currentId !== null && !isLive()) loadCurrent(); });
  vn.theme.connect((t) => (document.body.className = t));

  vn.getState((json) => {
    const s = JSON.parse(json);
    document.body.className = s.theme;
    state.me = s.me;
    if (s.recording) {
      state.recordingId = s.recording;
      state.currentId = s.recording;
      state.tab = "transcript";
      $("#recordBtn").classList.add("on");
      $("#recordBtn").disabled = true;
      $("#recordBtn").querySelector(".label").textContent = "Opname loopt";
      vn.getLive((j) => { state.live = JSON.parse(j).segments; loadCurrent(); });
    }
    loadMeetings();
    loadPeople();
  });
});
