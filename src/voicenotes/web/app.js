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
  forget: new Set(),
  folders: [],
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

/* ---------- sidebar ---------- */

function visibleMeetings() {
  const q = $("#search").value.trim().toLowerCase();
  return state.meetings.filter((m) => (state.folder === null || m.folder === state.folder)
    && (!q || m.title.toLowerCase().includes(q)));
}

function folderName(id) { return state.folders.find((f) => f.id === id)?.name; }

function renderFolders() {
  const el = $("#folders");
  const all = `<button class="folder${state.folder === null ? " active" : ""}" data-folder="-1">${ALL_ICON}
      <span class="fname">Alle meetings</span><span class="count">${state.meetings.length}</span></button>`;
  el.innerHTML = all + state.folders.map((f) => `<button class="folder${state.folder === f.id ? " active" : ""}" data-folder="${f.id}" title="Dubbelklik om te hernoemen">
      ${FOLDER_ICON}<span class="fname">${esc(f.name)}</span><span class="count">${f.count}</span>
      <span class="del" role="button" data-del="${f.id}" title="Map verwijderen" aria-label="Map ${esc(f.name)} verwijderen">×</span></button>`).join("");
}

function renderList() {
  const nav = $("#meetings");
  const q = $("#search").value.trim().toLowerCase();
  const items = visibleMeetings();
  if (!items.length) {
    nav.innerHTML = q ? `<p class="no-results">Geen meetings met "${esc(q)}".</p>`
      : state.folder !== null ? `<p class="no-results">Deze map is nog leeg. Sleep een meeting hierheen, of start een opname terwijl de map open staat.</p>` : "";
    return;
  }
  let html = "", lastGroup = null;
  for (const m of items) {
    const d = parseDate(m.created);
    const g = groupLabel(d);
    if (g !== lastGroup) { html += `<div class="group">${esc(g)}</div>`; lastGroup = g; }
    const badge = m.recording ? '<i class="badge rec" title="Opname loopt"></i>'
      : m.busy ? '<i class="badge busy" title="Wordt verwerkt"></i>' : "";
    const sub = m.recording ? "Opname loopt" : m.busy ? (state.progress[m.id] || "Wordt verwerkt") : hhmm(d);
    html += `<button class="item${m.id === state.currentId ? " active" : ""}" data-id="${m.id}" draggable="true">
      <div class="t">${badge}<span>${esc(m.title)}</span></div><div class="s">${esc(sub)}</div></button>`;
  }
  nav.innerHTML = html;
}

function loadMeetings() {
  vn.listFolders((fjson) => {
    state.folders = JSON.parse(fjson);
    if (state.folder !== null && !state.folders.some((f) => f.id === state.folder)) {
      state.folder = null;
      vn.setActiveFolder(-1);
    }
    vn.listMeetings((json) => {
      state.meetings = JSON.parse(json);
      if (state.currentId && !state.meetings.some((m) => m.id === state.currentId)) state.currentId = null;
      const visible = visibleMeetings();
      if (state.currentId === null && visible.length) {
        state.currentId = visible[0].id;
        loadCurrent(true);
      }
      renderFolders();
      renderList();
      renderFolderPick();
      updateAskScope();
      showEmpty(!state.meetings.length);
    });
  });
}

function selectFolder(id) {
  state.folder = id;
  vn.setActiveFolder(id === null ? -1 : id);
  const visible = visibleMeetings();
  if (!visible.some((m) => m.id === state.currentId) && state.currentId !== state.recordingId) {
    state.currentId = visible.length ? visible[0].id : state.currentId;
    loadCurrent(true);
  }
  renderFolders();
  renderList();
  updateAskScope();
}

function newFolder(onCreated) {
  const el = $("#folders");
  const row = document.createElement("div");
  row.className = "folder";
  row.innerHTML = `${FOLDER_ICON}<input type="text" placeholder="Naam van de map" aria-label="Naam van de nieuwe map">`;
  el.appendChild(row);
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
      if (onCreated) onCreated(fid); else selectFolder(fid);
    });
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); });
  input.addEventListener("blur", () => finish(true));
}

function renameFolder(btn) {
  const fid = Number(btn.dataset.folder);
  const name = folderName(fid);
  const span = btn.querySelector(".fname");
  const input = document.createElement("input");
  input.value = name;
  span.replaceWith(input);
  input.focus(); input.select();
  let done = false;
  const finish = (save) => {
    if (done) return;
    done = true;
    const v = input.value.trim();
    if (save && v && v !== name) vn.renameFolder(fid, v); else renderFolders();
  };
  input.addEventListener("click", (e) => e.stopPropagation());
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); });
  input.addEventListener("blur", () => finish(true));
}

function renderFolderPick() {
  const pick = $("#folderPick");
  if (!pick || !state.current) return;
  const cur = state.current.folder ?? -1;
  pick.innerHTML = `<option value="-1">Geen map</option>` +
    state.folders.map((f) => `<option value="${f.id}">${esc(f.name)}</option>`).join("") +
    `<option value="new">Nieuwe map…</option>`;
  pick.value = String(cur);
}

function updateAskScope() {
  const label = state.folder !== null ? `hele map ${folderName(state.folder) || ""}` : "alle meetings";
  $("#askAllLabel").textContent = label;
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
  renderList();
  loadCurrent(true);
}

function loadCurrent(resetScroll = false) {
  if (state.currentId === null) return;
  vn.getMeeting(state.currentId, (json) => {
    const m = JSON.parse(json);
    if (!m) return;
    state.current = m;
    state.me = m.me;
    renderFolderPick();
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
  const rows = [...m.speakers].sort((a, b) => b.talk - a.talk).map((s) => {
    const pct = Math.round((s.talk / total) * 100);
    const tag = s.me ? '<span class="tag">jij, via je microfoon</span>'
      : s.known ? '<span class="tag known">herkend</span>' : '<span class="tag">nieuwe stem</span>';
    const formHtml = `<form class="rename" data-old="${esc(s.name)}"${s.known ? " hidden" : ""}>
        <input type="text" value="${s.known ? esc(s.name) : ""}" placeholder="Wie is dit?" aria-label="Naam voor ${esc(s.name)}">
        <button class="ghost" type="submit">Opslaan</button></form>`;
    const form = !s.renamable ? "" : s.known
      ? `<div class="edit"><button type="button" class="edit-btn">Naam wijzigen</button>${formHtml}</div>` : formHtml;
    return `<div class="spk" style="--c:${colors[s.name] || "var(--s1)"}">
      <div class="name"><i></i>${esc(s.name)} ${tag}</div>${form}
      <div class="share"><div class="bar"><b style="width:${pct}%"></b></div><span>${fmtDuration(s.talk)}, ${pct}%</span></div>
      <blockquote>“${esc(s.sample)}”</blockquote></div>`;
  }).join("");
  const intro = m.speakers.some((s) => s.renamable && !s.known)
    ? `<p class="spk-intro">Geef nieuwe stemmen een naam. Voicenotes onthoudt de stem en herkent die persoon in volgende meetings.</p>` : "";
  el.innerHTML = intro + rows;
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
  btn.querySelector(".label").textContent = active ? "Opname stoppen" : "Opname starten";
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
  const inFolder = all && state.folder !== null;
  const scope = inFolder ? `Over de map ${folderName(state.folder)}` : all ? "Over alle meetings" : `Over ${state.current?.title || "deze meeting"}`;
  const div = document.createElement("div");
  div.className = "qa";
  div.id = req;
  div.innerHTML = `<div class="q">${esc(question)}</div><div class="scope-note">${esc(scope)}</div>
    <div class="a"><span class="thinking"><span class="spinner"></span>Zoeken in het transcript…</span></div>`;
  list.appendChild(div);
  list.scrollTop = list.scrollHeight;
  vn.ask(req, question, all ? -1 : state.currentId, inFolder ? state.folder : -1);
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
    state.forget = new Set();
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
    renderProfiles();
    $("#settings").showModal();
  });
}
function renderProfiles() {
  const el = $("#sProfiles"), names = state.settings.profiles;
  el.innerHTML = names.length ? names.map((n) => `<span class="chip${state.forget.has(n) ? " gone" : ""}">${esc(n)}
      <button type="button" data-forget="${esc(n)}" title="${state.forget.has(n) ? "Toch bewaren" : "Stem vergeten"}" aria-label="Stem van ${esc(n)} vergeten">${state.forget.has(n) ? "↺" : "×"}</button></span>`).join("")
    : `<span class="none">Nog geen. Geef sprekers een naam op het tabblad Sprekers.</span>`;
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
    start_at_login: $("#sLogin").checked,
    dictation: $("#sDictation").checked,
    dictation_cleanup: $("#sCleanup").checked,
    dictation_model: $("#sDictModel").value,
    call_apps: Object.fromEntries([...document.querySelectorAll("#sApps select")].map((el) => [el.dataset.app, el.value])),
    diarize: $("#sDiarize").checked,
    speaker_threshold: parseFloat($("#sThreshold").value),
    forget_profiles: [...state.forget],
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
  $("#recordBtn").addEventListener("click", () => vn.toggleRecording());
  $("#stopBtn").addEventListener("click", () => vn.toggleRecording());
  $("#importBtn").addEventListener("click", () => vn.importFile());
  $("#settingsBtn").addEventListener("click", openSettings);
  $("#search").addEventListener("input", renderList);

  // Folders
  $("#newFolderBtn").addEventListener("click", () => newFolder());
  const folders = $("#folders");
  folders.addEventListener("click", (e) => {
    const del = e.target.closest("[data-del]");
    if (del) {
      e.stopPropagation();
      const fid = Number(del.dataset.del);
      confirmSheet("Map verwijderen?", `De map "${folderName(fid)}" verdwijnt. De meetings erin blijven bewaard, zonder map.`,
        "Map verwijderen", () => vn.deleteFolder(fid));
      return;
    }
    const b = e.target.closest(".folder[data-folder]");
    if (!b || e.target.tagName === "INPUT") return;
    const fid = Number(b.dataset.folder);
    selectFolder(fid < 0 ? null : fid);
  });
  folders.addEventListener("dblclick", (e) => {
    const b = e.target.closest(".folder[data-folder]");
    if (b && Number(b.dataset.folder) >= 0) renameFolder(b);
  });
  // Drag a meeting onto a folder to move it
  $("#meetings").addEventListener("dragstart", (e) => {
    const item = e.target.closest(".item");
    if (item) { e.dataTransfer.setData("text/x-meeting", item.dataset.id); e.dataTransfer.effectAllowed = "move"; }
  });
  folders.addEventListener("dragover", (e) => {
    const b = e.target.closest(".folder[data-folder]");
    if (!b || !e.dataTransfer.types.includes("text/x-meeting")) return;
    e.preventDefault();
    folders.querySelectorAll(".drop").forEach((x) => x !== b && x.classList.remove("drop"));
    b.classList.add("drop");
  });
  folders.addEventListener("dragleave", (e) => e.target.closest?.(".folder")?.classList.remove("drop"));
  folders.addEventListener("drop", (e) => {
    const b = e.target.closest(".folder[data-folder]");
    folders.querySelectorAll(".drop").forEach((x) => x.classList.remove("drop"));
    const mid = Number(e.dataTransfer.getData("text/x-meeting"));
    if (!b || !mid) return;
    e.preventDefault();
    const fid = Number(b.dataset.folder);
    vn.moveMeeting(mid, fid);
    toast(fid < 0 ? "Meeting uit de map gehaald" : `Verplaatst naar ${folderName(fid)}`);
  });
  $("#folderPick").addEventListener("change", (e) => {
    const v = e.target.value;
    if (v === "new") {
      renderFolderPick();
      const mid = state.current.id;
      newFolder((fid) => { vn.moveMeeting(mid, fid); toast(`Verplaatst naar de nieuwe map`); });
      return;
    }
    vn.moveMeeting(state.current.id, Number(v));
  });
  $("#meetings").addEventListener("click", (e) => {
    const b = e.target.closest(".item");
    if (b) select(Number(b.dataset.id));
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

  // More menu
  const menu = $("#moreMenu");
  $("#moreBtn").addEventListener("click", (e) => { e.stopPropagation(); menu.hidden = !menu.hidden; });
  document.addEventListener("click", () => (menu.hidden = true));
  menu.addEventListener("click", (e) => {
    const cmd = e.target.closest("button")?.dataset.cmd;
    const m = state.current;
    if (!cmd || !m) return;
    menu.hidden = true;
    const busy = m.busy || isLive();
    if (cmd === "rename") return startRename();
    if (cmd === "reveal") return vn.reveal(m.id);
    if (busy) return toast("Deze meeting is nog bezig. Probeer het zo opnieuw.");
    if (cmd === "resummarize") vn.resummarize(m.id);
    if (cmd === "reprocess") vn.reprocess(m.id);
    if (cmd === "delete") {
      confirmSheet("Meeting verwijderen?", `"${m.title}" wordt met de opname en het transcript van deze Mac verwijderd. Dit kun je niet ongedaan maken.`,
        "Verwijderen", () => { vn.deleteMeeting(m.id); state.currentId = null; state.current = null; });
    }
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
    const form = e.target.closest("form.rename");
    const name = form.querySelector("input").value.trim();
    if (!name) return form.querySelector("input").focus();
    vn.nameSpeaker(state.current.id, form.dataset.old, name);
    toast(`${name} opgeslagen. De samenvatting wordt bijgewerkt.`);
  });

  $("#pane-speakers").addEventListener("click", (e) => {
    const b = e.target.closest(".edit-btn");
    if (!b) return;
    const form = b.parentElement.querySelector("form");
    b.hidden = true;
    form.hidden = false;
    form.querySelector("input").select();
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
  $("#sProfiles").addEventListener("click", (e) => {
    const n = e.target.closest("button")?.dataset.forget;
    if (!n) return;
    state.forget.has(n) ? state.forget.delete(n) : state.forget.add(n);
    renderProfiles();
  });

  document.addEventListener("keydown", (e) => {
    if (e.metaKey && e.key === ",") { e.preventDefault(); openSettings(); }
    if (e.metaKey && e.key.toLowerCase() === "r") { e.preventDefault(); vn.toggleRecording(); }
    if (e.metaKey && e.key.toLowerCase() === "f") { e.preventDefault(); $("#search").focus(); }
    if (e.key === "Escape") menu.hidden = true;
  });
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
      $("#recordBtn").querySelector(".label").textContent = "Opname stoppen";
      vn.getLive((j) => { state.live = JSON.parse(j).segments; loadCurrent(); });
    }
    loadMeetings();
  });
});
