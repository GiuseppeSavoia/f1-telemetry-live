// Interroga il server ogni pochi secondi e ridisegna i grafici quando arriva
// un giro nuovo (se "Segui l'ultimo giro" è attivo) o quando cambi selezione.
// La vista è la sessione live oppure una d'archivio scelta in alto.

const POLL_MS = 3000;

const TRACK_LABELS = {
  green: "Pista libera", yellow: "Bandiera gialla", sc: "Safety Car",
  vsc: "Virtual Safety Car", red: "Bandiera rossa",
};

const CHART = {
  grid: "#2a2f3a", text: "#e8eaee", muted: "#8b93a3", corner: "rgba(255,255,255,0.14)",
};

// lento = blu, veloce = rosso
const SPEED_SCALE = [[0, "#3b6fd8"], [0.35, "#2fbf71"], [0.65, "#f5c518"], [1, "#e10600"]];

const SESSION_LABELS = {
  "Practice 1": "Prove libere 1", "Practice 2": "Prove libere 2", "Practice 3": "Prove libere 3",
  Qualifying: "Qualifica", "Sprint Qualifying": "Qualifica Sprint", "Sprint Shootout": "Sprint Shootout",
  Sprint: "Sprint", Race: "Gara",
};

const state = {
  view: "", // "" = live, altrimenti "anno|GP|sessione"
  gen: 0, // cresce a ogni cambio di vista: le risposte arrivate dopo vengono scartate
  info: null,
  a: { driver: null, lap: null, laps: [], count: null },
  b: { driver: "", mode: "same", laps: [], count: null },
  follow: true,
  circuit: { corners: [], rotation: 0 },
  telemetry: new Map(),
  drawn: null,
  drawToken: 0,
};

const $ = (id) => document.getElementById(id);

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  return res.json();
}

function fmtLap(s) {
  if (s == null) return "—";
  const m = Math.floor(s / 60);
  const rest = (s - m * 60).toFixed(3);
  return m ? `${m}:${rest.padStart(6, "0")}` : rest;
}

// aggiunge la vista corrente alle richieste all'API
function api(path) {
  return state.view ? `${path}${path.includes("?") ? "&" : "?"}s=${encodeURIComponent(state.view)}` : path;
}

const isArchive = () => Boolean(state.view);

function driver(abbr) {
  return state.info?.drivers.find((d) => d.abbr === abbr);
}

// ---------------------------------------------------------------- polling

async function loop() {
  if (state.stopped) return;
  try {
    await poll();
  } catch (err) {
    console.error(err);
    showStatus("La dashboard è spenta o non risponde. Riaprila dall'icona F1 Telemetria nel Dock.");
  }
  setTimeout(loop, POLL_MS);
}

async function poll() {
  const gen = state.gen;
  const [info, auth] = await Promise.all([getJSON(api("/api/state")), getJSON("/api/auth")]);
  if (gen !== state.gen) return;
  if (state.info && info.session_key !== state.info.session_key) resetSession();
  state.info = info;
  renderHeader();
  renderAccount(auth);
  const needsLogin = !isArchive() && auth.needed && !auth.logged_in;
  showStatus(info.status === "ok" && !needsLogin ? null : [
    info.status === "ok" ? null : info.status,
    needsLogin ? "Accedi con il tuo account F1 TV (in alto a destra) per ricevere telemetria e posizioni." : null,
  ].filter(Boolean).join(" "));
  if (!info.drivers.length) return;

  if (!state.circuit.corners.length) {
    const circuit = await getJSON(api("/api/circuit"));
    if (gen !== state.gen) return;
    state.circuit = circuit;
  }
  if (!state.a.driver) state.a.driver = info.drivers[0].abbr;

  renderStandings();
  renderDriverOptions();
  await refreshLaps();
}

async function refreshLaps() {
  const gen = state.gen;
  const a = driver(state.a.driver);
  if (a && a.laps !== state.a.count) {
    const laps = await getJSON(api(`/api/laps/${a.abbr}`));
    if (gen !== state.gen || a.abbr !== state.a.driver) return;
    state.a.laps = laps;
    state.a.count = a.laps;
  }
  const b = driver(state.b.driver);
  if (b && b.laps !== state.b.count) {
    const laps = await getJSON(api(`/api/laps/${b.abbr}`));
    if (gen !== state.gen || b.abbr !== state.b.driver) return;
    state.b.laps = laps;
    state.b.count = b.laps;
    renderLapOptionsB();
  }

  // live: l'ultimo giro completato; archivio: il giro più veloce del pilota
  const laps = state.a.laps;
  if (laps.length && (state.follow || !laps.some((l) => l.lap === state.a.lap))) {
    state.a.lap = isArchive()
      ? (laps.find((l) => l.best) ?? laps[laps.length - 1]).lap
      : laps[laps.length - 1].lap;
  }
  renderLapOptionsA();
  renderLapStrip();
  await draw();
}

// è iniziata un'altra sessione (es. dalle prove alla qualifica)
function resetSession() {
  state.a = { driver: null, lap: null, laps: [], count: null };
  state.b = { driver: "", mode: "same", laps: [], count: null };
  state.circuit = { corners: [], rotation: 0 };
  state.telemetry.clear();
  state.drawn = null;
  state.drawToken++;
  $("driverB").value = "";
  renderLapOptionsB();
  for (const id of ["chart", "map"]) Plotly.purge(id);
  for (const id of ["summary", "lapstrip", "lapA", "drivers"]) $(id).replaceChildren();
}

// ---------------------------------------------------------------- selection

function lapB() {
  const laps = state.b.laps;
  if (!state.b.driver || !laps.length) return null;
  if (state.b.mode === "same") {
    return (laps.find((l) => l.lap === state.a.lap) ?? laps[laps.length - 1]).lap;
  }
  if (state.b.mode === "best") {
    return (laps.find((l) => l.best) ?? laps[laps.length - 1]).lap;
  }
  return Number(state.b.mode);
}

function selectDriverA(abbr) {
  state.a = { driver: abbr, lap: null, laps: [], count: null };
  setFollow(!isArchive());
  renderStandings();
  refreshLaps();
}

function selectLapA(lap) {
  state.a.lap = lap;
  setFollow(false);
  renderLapOptionsA();
  renderLapStrip();
  draw();
}

function setFollow(on) {
  state.follow = on;
  $("follow").checked = on;
}

async function telemetry(abbr, lap) {
  const key = `${state.view}|${abbr}/${lap}`;
  if (!state.telemetry.has(key)) {
    state.telemetry.set(key, await getJSON(api(`/api/telemetry/${abbr}/${lap}`)));
  }
  return state.telemetry.get(key);
}

async function draw() {
  if (state.a.lap == null) return;
  const lb = lapB();
  const key = `${state.a.driver}/${state.a.lap}|${state.b.driver}/${lb}`;
  if (key === state.drawn) return;

  const token = ++state.drawToken;
  const [ta, tb] = await Promise.all([
    telemetry(state.a.driver, state.a.lap),
    lb != null ? telemetry(state.b.driver, lb) : null,
  ]);
  if (token !== state.drawToken) return; // nel frattempo è cambiata la selezione

  state.drawn = key;
  renderSummary(ta, tb);
  renderChart(ta, tb);
  renderMap(ta);
}

// ---------------------------------------------------------------- rendering

function showStatus(message) {
  $("status").textContent = message ?? "";
  $("status").classList.toggle("hidden", !message);
}

function renderHeader() {
  const info = state.info;
  $("event").textContent = info.event ? `${info.event} ${info.year}` : "F1 Telemetria Live";
  $("session").textContent = SESSION_LABELS[info.session] ?? info.session ?? "";
  document.title = info.event ? `${info.event} – F1 Telemetria Live` : "F1 Telemetria Live";

  const waiting = !info.drivers.length;
  document.querySelector(".layout").classList.toggle("hidden", waiting);

  const mode = $("mode");
  const live = info.mode === "live" && !info.finished && !waiting;
  mode.className = `pill ${live ? "live" : ""}`;
  mode.textContent = info.mode === "archive" ? "ARCHIVIO"
    : info.mode === "replay" ? `REPLAY ×${info.speed}`
    : info.finished ? "SESSIONE FINITA" : waiting ? "IN ATTESA" : "LIVE";

  // in qualifica e prove il numero di giro del leader non dice nulla
  $("lapcount").classList.toggle("hidden",
    !info.drivers.length || Boolean(info.lap && !info.total_laps));
  $("lapcount").textContent = info.lap
    ? `Giro ${info.lap}/${info.total_laps}`
    : "In attesa del primo giro";

  const track = $("track");
  const showTrack = info.track_status && info.mode !== "archive";
  track.classList.toggle("hidden", !showTrack);
  if (showTrack) {
    track.className = `pill ${info.track_status}`;
    track.textContent = TRACK_LABELS[info.track_status] ?? info.track_status;
  }

  $("updated").textContent = info.updated
    ? `Aggiornato alle ${new Date(info.updated * 1000).toLocaleTimeString("it-IT")}`
    : "";
}

function renderStandings() {
  const drivers = state.info.drivers;
  const best = Math.min(...drivers.map((d) => d.best_time ?? Infinity));
  $("drivers").replaceChildren(...drivers.map((d) => {
    const li = document.createElement("li");
    li.classList.toggle("selected", d.abbr === state.a.driver);
    li.title = `${d.name} · ${d.team ?? ""}`;
    // live: ultimo giro; archivio: miglior giro della sessione
    const time = isArchive() ? d.best_time : d.last_time;
    const timeClass = time != null && time === best ? "best" : "";
    li.innerHTML = `
      <span class="pos">${d.position ?? "–"}</span>
      <span class="bar" style="background:${d.color}"></span>
      <span class="abbr">${d.abbr}</span>
      <span class="time ${timeClass}">${fmtLap(time)}</span>
      <span class="dot ${d.compound ?? ""}" title="${d.compound ?? ""} ${d.tyre_life ? `· ${d.tyre_life} giri` : ""}"></span>`;
    li.addEventListener("click", () => selectDriverA(d.abbr));
    return li;
  }));

  const a = driver(state.a.driver);
  const tag = $("driverA");
  tag.textContent = a ? a.abbr : "—";
  tag.style.borderLeftColor = a?.color ?? "";
}

function renderDriverOptions() {
  const select = $("driverB");
  const wanted = ["", ...state.info.drivers.map((d) => d.abbr).sort()];
  const current = [...select.options].map((o) => o.value);
  if (wanted.join() === current.join()) return;
  select.replaceChildren(...wanted.map((abbr) => new Option(abbr || "Nessuno", abbr)));
  select.value = state.b.driver;
}

function renderLapOptionsA() {
  const select = $("lapA");
  const options = state.a.laps.map((l) => new Option(`Giro ${l.lap} · ${fmtLap(l.time)}`, l.lap));
  select.replaceChildren(...options);
  if (state.a.lap != null) select.value = state.a.lap;
}

function renderLapOptionsB() {
  const select = $("lapB");
  select.disabled = !state.b.driver;
  select.replaceChildren(
    new Option("Stesso giro", "same"),
    new Option("Giro più veloce", "best"),
    ...state.b.laps.map((l) => new Option(`Giro ${l.lap} · ${fmtLap(l.time)}`, l.lap)),
  );
  select.value = state.b.mode;
}

function renderLapStrip() {
  $("lapstrip").replaceChildren(...state.a.laps.map((l) => {
    const chip = document.createElement("button");
    chip.className = `lapchip ${l.compound ?? ""}`;
    chip.classList.toggle("best", l.best);
    chip.classList.toggle("deleted", l.deleted);
    chip.classList.toggle("selected", l.lap === state.a.lap);
    chip.title = [
      l.compound && `${l.compound}${l.tyre_life ? ` (${l.tyre_life} giri)` : ""}`,
      l.position && `P${l.position}`,
      l.pit && "Pit stop",
      l.deleted && "Tempo cancellato",
      `S1 ${fmtLap(l.s1)} · S2 ${fmtLap(l.s2)} · S3 ${fmtLap(l.s3)}`,
    ].filter(Boolean).join(" · ");
    chip.innerHTML = `<span class="n">${l.lap}${l.pit ? " · PIT" : ""}</span><span class="t">${fmtLap(l.time)}</span>`;
    chip.addEventListener("click", () => selectLapA(l.lap));
    return chip;
  }));
  // scorre solo la striscia (scrollIntoView sposterebbe anche la pagina)
  const strip = $("lapstrip");
  const selected = strip.querySelector(".selected");
  if (selected) strip.scrollLeft = selected.offsetLeft - (strip.clientWidth - selected.offsetWidth) / 2;
}

function renderSummary(ta, tb) {
  const item = (t) => {
    const d = driver(t.driver);
    return `<span><span class="driver-tag" style="border-left-color:${d?.color}">${t.driver}</span>
      Giro ${t.lap} · <b>${fmtLap(t.lap_time)}</b> · ${t.compound ?? "?"} · max ${Math.max(...t.speed)} km/h</span>`;
  };
  let html = item(ta);
  if (tb) {
    html += item(tb);
    if (ta.lap_time != null && tb.lap_time != null) {
      const gap = tb.lap_time - ta.lap_time;
      html += `<span class="muted">${tb.driver} ${gap >= 0 ? "+" : ""}${gap.toFixed(3)} s</span>`;
    }
  }
  $("summary").innerHTML = html;
}

// valore di ys in x, interpolando linearmente su xs (crescente)
function interp(x, xs, ys) {
  if (x <= xs[0]) return ys[0];
  if (x >= xs[xs.length - 1]) return ys[ys.length - 1];
  let lo = 0, hi = xs.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (xs[mid] <= x) lo = mid; else hi = mid;
  }
  const f = (x - xs[lo]) / (xs[hi] - xs[lo] || 1);
  return ys[lo] + f * (ys[hi] - ys[lo]);
}

function renderChart(ta, tb) {
  const colorA = driver(ta.driver)?.color ?? "#e10600";
  const colorB = driver(tb?.driver)?.color ?? "#ffffff";
  const dashB = tb && colorA.toLowerCase() === colorB.toLowerCase() ? "dot" : "solid";

  const rows = [];
  if (tb) rows.push({ key: "delta", title: "Δ tempo (s)", weight: 1 });
  rows.push(
    { key: "speed", title: "Velocità (km/h)", weight: 3 },
    { key: "throttle", title: "Gas (%)", weight: 1.2 },
    { key: "brake", title: "Freno", weight: 0.6 },
    { key: "gear", title: "Marcia", weight: 1 },
  );

  const gap = 0.025;
  const total = rows.reduce((s, r) => s + r.weight, 0);
  const usable = 1 - gap * (rows.length - 1);
  let top = 1;
  const layout = {
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: CHART.text, size: 11 },
    margin: { l: 56, r: 12, t: 28, b: 40 },
    hovermode: "x unified", hoversubplots: "axis",
    hoverlabel: { bgcolor: "#1f232c", bordercolor: CHART.grid },
    showlegend: Boolean(tb),
    legend: { orientation: "h", x: 1, xanchor: "right", y: 1.06 },
    shapes: [], annotations: [],
  };
  const axisName = (i) => (i === 0 ? "y" : `y${i + 1}`);
  rows.forEach((row, i) => {
    const h = (row.weight / total) * usable;
    layout[i === 0 ? "yaxis" : `yaxis${i + 1}`] = {
      domain: [top - h, top], anchor: "x", gridcolor: CHART.grid, zeroline: false,
      fixedrange: true, title: { text: row.title, font: { size: 11, color: CHART.muted } },
      ...(row.key === "brake" ? { range: [-0.1, 1.1], tickvals: [0, 1], ticktext: ["off", "on"] } : {}),
      ...(row.key === "throttle" ? { range: [-5, 105] } : {}),
      ...(row.key === "gear" ? { range: [0.5, 8.5], dtick: 1 } : {}),
      ...(row.key === "delta" ? { zeroline: true, zerolinecolor: CHART.muted } : {}),
    };
    row.axis = axisName(i);
    top -= h + gap;
  });
  layout.xaxis = {
    anchor: axisName(rows.length - 1), gridcolor: CHART.grid, zeroline: false,
    title: { text: "Distanza (m)", font: { size: 11, color: CHART.muted } },
    showspikes: true, spikemode: "across", spikecolor: CHART.muted, spikethickness: 1, spikedash: "solid",
  };

  for (const c of state.circuit.corners) {
    layout.shapes.push({
      type: "line", xref: "x", yref: "paper", x0: c.distance, x1: c.distance, y0: 0, y1: 1,
      line: { color: CHART.corner, width: 1, dash: "dot" }, layer: "below",
    });
    layout.annotations.push({
      x: c.distance, y: 1, xref: "x", yref: "paper", yanchor: "bottom", showarrow: false,
      text: c.label, font: { size: 10, color: CHART.muted },
    });
  }

  const traces = [];
  const axis = Object.fromEntries(rows.map((r) => [r.key, r.axis]));
  const line = (t, key, color, dash, shape = "linear") => ({
    x: t.distance, y: t[key], yaxis: axis[key], type: "scatter", mode: "lines",
    name: `${t.driver} L${t.lap}`, legendgroup: `${t.driver}${t.lap}`, showlegend: key === "speed",
    line: { color, width: key === "speed" ? 2 : 1.5, dash, shape },
    hovertemplate: `${t.driver}: %{y}<extra></extra>`,
  });
  for (const [t, color, dash] of [[ta, colorA, "solid"], [tb, colorB, dashB]]) {
    if (!t) continue;
    traces.push(line(t, "speed", color, dash), line(t, "throttle", color, dash),
      line(t, "brake", color, dash, "hv"), line(t, "gear", color, dash, "hv"));
  }
  if (tb) {
    const delta = ta.distance.map((d, i) => interp(d, tb.distance, tb.time) - ta.time[i]);
    traces.push({
      x: ta.distance, y: delta, yaxis: axis.delta, type: "scatter", mode: "lines", showlegend: false,
      line: { color: colorB, width: 1.5 }, fill: "tozeroy", fillcolor: "rgba(255,255,255,0.05)",
      hovertemplate: `Δ ${tb.driver}–${ta.driver}: %{y:+.3f} s<extra></extra>`,
    });
  }

  Plotly.react("chart", traces, layout, { responsive: true, displaylogo: false });
}

function renderMap(t) {
  const rot = (state.circuit.rotation * Math.PI) / 180;
  const rotate = (x, y) => [x * Math.cos(rot) - y * Math.sin(rot), x * Math.sin(rot) + y * Math.cos(rot)];
  const pts = t.x.map((x, i) => rotate(x, t.y[i]));

  const corners = state.circuit.corners.filter((c) => c.x != null).map((c) => {
    const a = (c.angle * Math.PI) / 180;
    return rotate(c.x + 600 * Math.cos(a), c.y + 600 * Math.sin(a));
  });

  const traces = [{
    x: pts.map((p) => p[0]), y: pts.map((p) => p[1]), type: "scattergl", mode: "markers",
    marker: {
      size: 5, color: t.map_speed, colorscale: SPEED_SCALE, cmin: 60, cmax: 340,
      colorbar: { thickness: 8, len: 0.7, tickfont: { size: 10, color: CHART.muted }, outlinewidth: 0 },
    },
    hovertemplate: "%{marker.color} km/h<extra></extra>",
  }, {
    x: corners.map((p) => p[0]), y: corners.map((p) => p[1]), type: "scatter", mode: "text",
    text: state.circuit.corners.filter((c) => c.x != null).map((c) => c.label),
    textfont: { size: 10, color: CHART.muted }, hoverinfo: "skip",
  }];
  const layout = {
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    margin: { l: 0, r: 0, t: 0, b: 0 }, showlegend: false,
    xaxis: { visible: false }, yaxis: { visible: false, scaleanchor: "x" },
  };
  Plotly.react("map", traces, layout, { responsive: true, displayModeBar: false });
}

// ---------------------------------------------------------------- account F1

function renderAccount(auth) {
  state.auth = auth;
  const button = $("account");
  button.classList.toggle("hidden", !auth.needed);
  button.classList.toggle("ok", auth.logged_in);
  button.textContent = auth.logged_in ? "✓ F1 TV" : "Accedi F1 TV";
}

async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Errore ${res.status}`);
  return data;
}

$("account").addEventListener("click", () => {
  if (!state.auth?.logged_in) {
    $("loginError").classList.add("hidden");
    $("login").showModal();
    return;
  }
  const until = new Date(state.auth.expires * 1000);
  $("logoutInfo").textContent = `Accesso valido fino a ${until.toLocaleString("it-IT",
    { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" })}.`;
  $("logout").showModal();
});

$("loginCancel").addEventListener("click", () => $("login").close());

$("loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const submit = $("loginSubmit");
  submit.disabled = true;
  submit.textContent = "Accesso in corso…";
  $("loginError").classList.add("hidden");
  try {
    renderAccount(await postJSON("/api/login", {
      email: form.email.value, password: form.password.value,
    }));
    form.reset();
    $("login").close();
    poll();
  } catch (err) {
    $("loginError").textContent = err.message;
    $("loginError").classList.remove("hidden");
  } finally {
    form.password.value = "";
    submit.disabled = false;
    submit.textContent = "Accedi";
  }
});

$("logout").addEventListener("close", async () => {
  if ($("logout").returnValue !== "logout") return;
  renderAccount(await postJSON("/api/logout", {}));
  poll();
});

$("shutdown").addEventListener("click", async () => {
  const live = await getJSON("/api/state").catch(() => null);
  const recording = live?.mode === "live" && live.event && !live.finished;
  const question = recording
    ? "La sessione è in corso: spegnendo si ferma anche la registrazione. Spegnere?"
    : "Spegnere la dashboard?";
  if (!confirm(question)) return;
  await postJSON("/api/shutdown", {});
  state.stopped = true;
  document.querySelector(".layout").classList.add("hidden");
  showStatus("Dashboard spenta. Per riaprirla clicca l'icona F1 Telemetria nel Dock.");
});

// ---------------------------------------------------------------- archivio

const picker = { events: [] };

async function initPicker() {
  for (let y = new Date().getFullYear(); y >= 2018; y--) $("year").add(new Option(String(y), y));
  await applyHash();
}

// il link della pagina (#anno|GP|sessione) dice quale sessione mostrare
async function applyHash() {
  const [year, event, name] = decodeURIComponent(location.hash.slice(1)).split("|");
  $("year").value = year || "";
  await loadYear(year || "", event, name);
}

window.addEventListener("hashchange", applyHash);

async function loadYear(year, event, name) {
  $("gp").classList.toggle("hidden", !year);
  $("sess").classList.toggle("hidden", !year);
  if (!year) return setView("");
  try {
    const data = await getJSON(`/api/schedule?year=${year}`);
    picker.events = data.events.filter((e) => e.sessions.some((s) => s.available));
  } catch (err) {
    console.error(err);
    showStatus(`Calendario ${year} non disponibile, riprova tra poco.`);
    return;
  }
  $("gp").replaceChildren(...picker.events.map((e) => new Option(`${e.round}. ${e.event}`, e.event)));
  $("gp").value = picker.events.some((e) => e.event === event)
    ? event : picker.events[picker.events.length - 1]?.event;
  loadEvent(name);
}

function loadEvent(name) {
  const event = picker.events.find((e) => e.event === $("gp").value);
  const sessions = event?.sessions.filter((s) => s.available) ?? [];
  $("sess").replaceChildren(...sessions.map((s) => new Option(s.label, s.name)));
  $("sess").value = sessions.some((s) => s.name === name)
    ? name : (sessions.find((s) => s.name === "Race") ?? sessions[sessions.length - 1])?.name;
  setView(`${$("year").value}|${$("gp").value}|${$("sess").value}`);
}

function setView(key) {
  if (key === state.view) return;
  state.view = key;
  state.gen++;
  history.replaceState(null, "", key ? `#${encodeURIComponent(key)}` : location.pathname);
  resetSession();
  state.info = null;
  setFollow(!key);
  $("follow").parentElement.classList.toggle("hidden", Boolean(key));
  document.querySelector(".layout").classList.add("hidden");
  showStatus("Caricamento…");
  poll().catch(console.error);
}

$("year").addEventListener("change", (e) => loadYear(e.target.value));
$("gp").addEventListener("change", () => loadEvent());
$("sess").addEventListener("change", () => setView(`${$("year").value}|${$("gp").value}|${$("sess").value}`));

// ---------------------------------------------------------------- events

$("follow").addEventListener("change", (e) => {
  state.follow = e.target.checked;
  if (state.follow) refreshLaps();
});
$("lapA").addEventListener("change", (e) => selectLapA(Number(e.target.value)));
$("driverB").addEventListener("change", (e) => {
  state.b = { driver: e.target.value, mode: "same", laps: [], count: null };
  renderLapOptionsB();
  refreshLaps();
});
$("lapB").addEventListener("change", (e) => {
  state.b.mode = e.target.value;
  draw();
});

initPicker().finally(loop);
