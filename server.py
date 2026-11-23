"""Dashboard web con la telemetria di ogni giro di ogni pilota, durante il GP.

Modalità:

  (nessuna)  automatica: trova nel calendario la sessione in corso, registra
             il live timing e aggiorna la dashboard; se non c'è nessuna
             sessione aspetta la prossima
  live       come sopra ma con un file già registrato a parte da
             ``python -m fastf1.livetiming save`` (--file, --year, --event)
  archivio   dalla dashboard si apre qualsiasi sessione dal 2018 in poi
  replay     riproduce una sessione passata (dalla cache di Fast-F1) mostrando
             i giri man mano che sarebbero stati completati; serve per provare
             la dashboard tra un GP e l'altro

Esempi:

  python server.py
  python server.py replay --year 2025 --event "Austrian Grand Prix" --session R
  python server.py live --file gara.txt --year 2026 --event "Brazil" --session R
"""
import argparse
import logging
import os
import threading
import time
import webbrowser
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import fastf1
import fastf1.plotting
import numpy as np
import pandas as pd
import plotly
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastf1.exceptions import F1AuthError
from fastf1.internals.f1account import clear_token, token_manager
from fastf1.internals.f1account.login import login
from fastf1.livetiming.client import SignalRClient
from fastf1.livetiming.data import LiveTimingData
from pydantic import BaseModel
from signalrcore.hub_connection_builder import HubConnectionBuilder

log = logging.getLogger("dashboard")

STATIC_DIR = Path(__file__).parent / "static"
RECORDINGS_DIR = Path(__file__).parent / "recordings"
PLOTLY_JS = Path(plotly.__file__).parent / "package_data" / "plotly.min.js"

TRACK_STATUS = {"1": "green", "2": "yellow", "4": "sc", "5": "red",
                "6": "vsc", "7": "vsc"}

# la registrazione parte un po' prima dell'orario ufficiale e dura quanto
# la sessione più eventuali ritardi o bandiere rosse
PRE_SESSION = pd.Timedelta(minutes=20)
SESSION_LENGTH = {"Race": pd.Timedelta(hours=3, minutes=30),
                  "Sprint": pd.Timedelta(hours=1, minutes=45)}
DEFAULT_SESSION_LENGTH = pd.Timedelta(hours=2)

WEEKDAYS = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì",
            "sabato", "domenica"]
SESSION_NAMES = {"Practice 1": "Prove libere 1", "Practice 2": "Prove libere 2",
                 "Practice 3": "Prove libere 3", "Qualifying": "Qualifica",
                 "Sprint Qualifying": "Qualifica Sprint",
                 "Sprint Shootout": "Sprint Shootout", "Sprint": "Sprint",
                 "Race": "Gara"}


def seconds(td):
    """Timedelta -> secondi (float) oppure None, per il JSON."""
    if td is None or pd.isna(td):
        return None
    return round(td.total_seconds(), 3)


def number(value, digits=None):
    if value is None or pd.isna(value):
        return None
    value = float(value)
    return round(value, digits) if digits is not None else value


def text(value):
    if value is None or pd.isna(value) or value == "":
        return None
    return str(value)


class Source:
    """Tiene la sessione Fast-F1 corrente e risponde alle richieste dell'API."""

    mode = ""

    def __init__(self, year, event, session_name):
        self.year = year
        self.event = event
        self.session_name = session_name
        self.session = None
        self.circuit = None
        self.status = "Avvio…"
        self.updated = None
        self.finished = False
        self._telemetry_cache = {}
        self._lock = threading.Lock()

    @property
    def session_key(self):
        """Cambia quando si passa a un'altra sessione: il browser svuota la cache."""
        return f"{self.year}|{self.event}|{self.session_name}"

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def credentials_changed(self):
        pass

    def _run(self):
        raise NotImplementedError

    def clock(self):
        """Tempo di sessione fino al quale i giri sono visibili (None = tutti)."""
        return None

    def visible_laps(self):
        session = self.session
        if session is None:
            return None
        try:
            laps = session.laps
        except Exception:
            # i dati dei giri non sono ancora disponibili
            return None
        laps = laps[laps["Time"].notna()]
        clock = self.clock()
        if clock is not None:
            laps = laps[laps["Time"] <= clock]
        return laps

    def _load_circuit(self):
        try:
            self.circuit = self.session.get_circuit_info()
        except Exception as exc:
            log.warning("Informazioni sul circuito non disponibili: %s", exc)

    def telemetry(self, driver, lap_number):
        key = (driver, lap_number)
        if key in self._telemetry_cache:
            return self._telemetry_cache[key]

        laps = self.visible_laps()
        if laps is None:
            return None
        selected = laps[(laps["Driver"] == driver)
                        & (laps["LapNumber"] == lap_number)]
        if selected.empty:
            return None
        lap = selected.iloc[0]

        with self._lock:
            car = lap.get_car_data().add_distance()
            try:
                pos = lap.get_pos_data()
            except Exception:
                pos = None

        car_time = car["SessionTime"].dt.total_seconds().to_numpy()
        data = {
            "driver": driver,
            "lap": int(lap_number),
            "lap_time": seconds(lap["LapTime"]),
            "compound": text(lap["Compound"]),
            "distance": car["Distance"].round(1).tolist(),
            "time": car["Time"].dt.total_seconds().round(3).tolist(),
            "speed": car["Speed"].tolist(),
            "throttle": car["Throttle"].tolist(),
            "brake": car["Brake"].astype(int).tolist(),
            "gear": car["nGear"].tolist(),
            "rpm": car["RPM"].tolist(),
            "x": [], "y": [], "map_speed": [],
        }
        if pos is not None and not pos.empty:
            pos_time = pos["SessionTime"].dt.total_seconds().to_numpy()
            data["x"] = pos["X"].tolist()
            data["y"] = pos["Y"].tolist()
            data["map_speed"] = np.interp(
                pos_time, car_time, car["Speed"].to_numpy()).round().tolist()

        self._telemetry_cache[key] = data
        return data


class ReplaySource(Source):
    mode = "replay"

    def __init__(self, year, event, session_name, speed, start_lap):
        super().__init__(year, event, session_name)
        self.speed = speed
        self.start_lap = start_lap
        self._t0 = None
        self._wall0 = None

    def _run(self):
        self.status = "Caricamento della sessione…"
        try:
            session = fastf1.get_session(self.year, self.event,
                                         self.session_name)
            session.load(weather=False, messages=False)
        except Exception as exc:
            log.exception("Caricamento fallito")
            self.status = f"Errore nel caricamento: {exc}"
            return

        laps = session.laps
        start = laps[laps["LapNumber"] == self.start_lap]["LapStartTime"].min()
        if pd.isna(start):
            start = laps["LapStartTime"].min()
        self._t0 = start
        self._wall0 = time.monotonic()
        self.session = session
        self.status = "ok"
        self._load_circuit()

    def clock(self):
        if self._wall0 is None:
            return pd.Timedelta(0)
        elapsed = (time.monotonic() - self._wall0) * self.speed
        return self._t0 + pd.Timedelta(seconds=elapsed)


class ArchiveSource(Source):
    """Una sessione passata, scelta dalla dashboard: tutti i giri, nessun orologio."""

    mode = "archive"

    def _run(self):
        self.status = ("Scarico i dati della sessione: la prima volta può volerci "
                       "un minuto, poi restano in cache…")
        try:
            session = fastf1.get_session(self.year, self.event, self.session_name)
            session.load(weather=False, messages=False)
            session.laps  # solleva un errore se i giri non ci sono
        except Exception as exc:
            log.warning("Sessione %s non disponibile: %s", self.session_key, exc)
            self.status = f"Dati non disponibili per questa sessione ({exc})"
            return
        self.session = session
        self.finished = True
        self.status = "ok"
        self._load_circuit()


class LiveSource(Source):
    mode = "live"

    def __init__(self, year, event, session_name, file, interval):
        super().__init__(year, event, session_name)
        self.file = Path(file) if file else None
        self.interval = interval
        # ultimo istante di telemetria ricevuto, per pilota
        self._telemetry_end = {}

    def _run(self):
        while True:
            started = time.monotonic()
            try:
                self._tick()
            except Exception as exc:
                log.exception("Ricaricamento fallito")
                self.status = f"Errore nel caricamento: {exc}"
            spent = time.monotonic() - started
            time.sleep(max(1.0, self.interval - spent))

    def _tick(self):
        self._reload()

    def _reload(self):
        if not self.file.exists() or self.file.stat().st_size == 0:
            self.status = f"In attesa di dati in {self.file}…"
            return

        session = fastf1.get_session(self.year, self.event, self.session_name)
        session.load(livedata=LiveTimingData(str(self.file)),
                     weather=False, messages=False)
        try:
            self._telemetry_end = {
                session.get_driver(number)["Abbreviation"]:
                    data["SessionTime"].max()
                for number, data in session.car_data.items()
            }
        except Exception:
            self._telemetry_end = {}

        self.session = session
        self.updated = time.time()
        self.status = "ok"
        if self.circuit is None:
            self._load_circuit()

    def visible_laps(self):
        laps = super().visible_laps()
        if laps is None or not self._telemetry_end:
            return laps
        # mostra un giro solo quando è arrivata tutta la sua telemetria
        end = laps["Driver"].map(self._telemetry_end)
        return laps[end.notna() & (laps["Time"] <= end)]


@dataclass(frozen=True)
class ScheduledSession:
    year: int
    event: str
    name: str
    start: pd.Timestamp  # UTC, senza fuso

    @property
    def end(self):
        return self.start + SESSION_LENGTH.get(self.name, DEFAULT_SESSION_LENGTH)

    @property
    def window_start(self):
        return self.start - PRE_SESSION

    def describe(self):
        local = self.start.tz_localize("UTC").tz_convert(
            datetime.now().astimezone().tzinfo)
        name = SESSION_NAMES.get(self.name, self.name)
        return (f"{name} del {self.event}, {WEEKDAYS[local.weekday()]} "
                f"{local:%d/%m} alle {local:%H:%M}")


def scheduled_sessions(now):
    """Tutte le sessioni dell'anno in corso (e del prossimo, a stagione finita)."""
    sessions = []
    for year in (now.year, now.year + 1):
        if year > now.year and any(s.start > now for s in sessions):
            break
        try:
            schedule = fastf1.get_event_schedule(year, include_testing=False)
        except Exception:
            continue
        for _, event in schedule.iterrows():
            for i in range(1, 6):
                name, start = event[f"Session{i}"], event[f"Session{i}DateUtc"]
                if text(name) and pd.notna(start):
                    sessions.append(ScheduledSession(
                        year, event["EventName"], name, pd.Timestamp(start)))
    return sorted(sessions, key=lambda s: s.start)


def utc_now():
    return pd.Timestamp.now(tz="UTC").tz_localize(None)


class _Client(SignalRClient):
    """Il client di Fast-F1, ma fermabile da un altro thread."""

    stopped = False
    until = None  # fine della sessione (UTC): dopo, il collegamento si chiude

    def _run(self):
        if not self._no_auth:
            return super()._run()
        # Senza login Fast-F1 3.9 passa access_token_factory=None, che
        # signalrcore 1.0 rifiuta: togliamo l'opzione per questa connessione
        original = HubConnectionBuilder.with_url

        def with_url(builder, url, options=None):
            options = {key: value for key, value in (options or {}).items()
                       if key != "access_token_factory"}
            return original(builder, url, options=options)

        HubConnectionBuilder.with_url = with_url
        try:
            super()._run()
        finally:
            HubConnectionBuilder.with_url = original

    def _supervise(self):
        # come l'originale, che però si ferma solo quando non arrivano più dati
        self._t_last_message = time.time()
        while not self.stopped:
            if time.time() - self._t_last_message > self.timeout:
                self.logger.warning("Nessun dato da %d secondi", self.timeout)
                break
            # a sessione finita il server manda ancora i battiti: senza questo
            # controllo il collegamento resterebbe aperto per sempre
            if self.until is not None and utc_now() > self.until:
                break
            time.sleep(1)
        self._exit()


class Recorder:
    """Registra il live timing su file e si riconnette se il flusso si interrompe.

    Si ferma da solo alla fine della sessione (``until``, UTC): così non continua
    a scrivere anche se il Mac era in stop proprio quando la sessione finiva."""

    def __init__(self, path, no_auth, until=None):
        self.path = path
        self.no_auth = no_auth
        self.until = until
        self.authenticated = False
        self._client = None
        self._stopped = False

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def expired(self):
        return self.until is not None and utc_now() > self.until

    def _loop(self):
        while not self._stopped and not self.expired():
            self.authenticated = not self.no_auth and logged_in()
            self._client = _Client(str(self.path), filemode="a", timeout=120,
                                   no_auth=not self.authenticated, logger=log)
            self._client.until = self.until
            # stop() chiamato mentre si creava il client: non va avviato
            self._client.stopped = self._stopped
            try:
                self._client.start()
            except Exception:
                log.exception("Registrazione interrotta")
            if self.expired():
                log.info("Sessione finita, chiudo la registrazione di %s", self.path.name)
            elif not self._stopped:
                log.warning("Nessun dato dal live timing, mi ricollego tra 10 s")
                time.sleep(10)

    def stop(self):
        self._stopped = True
        if self._client:
            self._client.stopped = True


class AutoLiveSource(LiveSource):
    """Segue il calendario: registra e mostra la sessione in corso, poi la successiva."""

    def __init__(self, interval, no_auth):
        super().__init__(None, None, None, None, interval)
        self.no_auth = no_auth
        self.target = None
        self.recorder = None
        self._schedule = []
        self._schedule_loaded = 0

    def credentials_changed(self):
        """Dopo login o logout la registrazione riparte con il nuovo accesso."""
        recorder = self.recorder
        live = self.target is not None and utc_now() <= self.target.end
        if (recorder and live and not self.no_auth
                and recorder.authenticated != logged_in()):
            log.info("Accesso F1 cambiato, riavvio la registrazione")
            recorder.stop()
            self.recorder = Recorder(self.file, self.no_auth, self.target.end)
            self.recorder.start()

    def _sessions(self, now):
        if time.monotonic() - self._schedule_loaded > 3600 or not self._schedule:
            self._schedule = scheduled_sessions(now)
            self._schedule_loaded = time.monotonic()
        return self._schedule

    def _tick(self, now=None):
        if now is None:
            now = utc_now()
        # prima di tutto: la registrazione si chiude a fine sessione, anche se al
        # risveglio dallo stop non c'è più nessuna sessione da mostrare
        if self.recorder and (self.target is None or now > self.target.end):
            self.recorder.stop()
            self.recorder = None
        sessions = self._sessions(now)
        started = [s for s in sessions if s.window_start <= now]
        upcoming = [s for s in sessions if s.window_start > now]
        next_text = (f"Prossima: {upcoming[0].describe()}." if upcoming
                     else "Nessuna sessione in calendario.")

        # resta sull'ultima sessione iniziata finché non si apre la successiva,
        # ma solo se è di oggi: dopo 12 ore non ha più senso mostrarla
        current = started[-1] if started else None
        if current is None or now - current.end > pd.Timedelta(hours=12):
            self.status = f"Nessuna sessione in corso. {next_text}"
            return

        if current != self.target:
            self._switch(current, now)

        live = now <= current.end

        if not self.file.exists() or self.file.stat().st_size == 0:
            self.status = (f"In attesa dell'inizio: {current.describe()}."
                           if live else f"Sessione non registrata. {next_text}")
            return
        if now < current.start:
            self.status = f"Collegato, in attesa dell'inizio: {current.describe()}."
            return
        if self.finished and self.session is not None:
            return  # sessione finita e già caricata per intero

        self._reload()
        if not live:
            self.finished = True
            self.status = "ok"

    def _switch(self, target, now):
        if self.recorder:
            self.recorder.stop()
            self.recorder = None
        self.target = target
        self.year, self.event, self.session_name = (target.year, target.event,
                                                    target.name)
        self.session = None
        self.circuit = None
        self.finished = False
        self._telemetry_cache = {}
        self._telemetry_end = {}

        RECORDINGS_DIR.mkdir(exist_ok=True)
        slug = f"{target.event} {target.name}".replace(" ", "_")
        self.file = RECORDINGS_DIR / f"{target.start:%Y-%m-%d}_{slug}.txt"
        log.info("Sessione: %s", target.describe())

        if now <= target.end:
            log.info("Registro il live timing in %s", self.file)
            self.recorder = Recorder(self.file, self.no_auth, target.end)
            self.recorder.start()


def logged_in():
    token = token_manager.get_stored()
    return token is not None and token.is_valid()


def login_without_prompt():
    """Login usato da Fast-F1 quando il token manca: mai domande nel terminale.

    Il login si fa dalla dashboard; in alternativa valgono le variabili
    d'ambiente F1_USERNAME e F1_PASSWORD."""
    username = os.environ.get("F1_USERNAME")
    password = os.environ.get("F1_PASSWORD")
    if username and password:
        return login(username, password)
    raise F1AuthError("accedi con il tuo account F1 dalla dashboard")


# sostituisce la richiesta di email e password nel terminale
token_manager._default_auth_func = login_without_prompt


SOURCE: Source = None

# sessioni d'archivio aperte di recente: ognuna occupa qualche centinaio di MB
ARCHIVE_SIZE = 3
FIRST_YEAR = 2018  # prima stagione con la telemetria
_archive = OrderedDict()
_archive_lock = threading.Lock()


def source_for(key):
    """La sessione live (chiave vuota) o una d'archivio, "anno|GP|sessione"."""
    if not key:
        return SOURCE
    try:
        year, event, name = key.split("|")
        year = int(year)
    except ValueError:
        raise HTTPException(400, "Sessione non valida")
    with _archive_lock:
        src = _archive.get(key)
        if src is None:
            src = ArchiveSource(year, event, name)
            src.start()
            _archive[key] = src
            while len(_archive) > ARCHIVE_SIZE:
                _archive.popitem(last=False)
        _archive.move_to_end(key)
    return src
app = FastAPI()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def revalidate(request, call_next):
    # il browser ricontrolla i file a ogni visita: niente versioni vecchie
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/plotly.min.js")
def plotly_js():
    return FileResponse(PLOTLY_JS, media_type="text/javascript")


def team_color(session, info):
    color = text(info.get("TeamColor"))
    if color:
        return "#" + color.lstrip("#")
    try:
        return fastf1.plotting.get_team_color(info["TeamName"], session=session)
    except Exception:
        return "#888888"


def current_track_status(session, clock):
    try:
        status = session.track_status
    except Exception:
        return None
    if status is None or status.empty:
        return None
    if clock is not None:
        status = status[status["Time"] <= clock]
    if status.empty:
        return "green"
    return TRACK_STATUS.get(str(status.iloc[-1]["Status"]), "green")


@app.get("/api/state")
def state(s: str = ""):
    src = source_for(s)
    out = {"mode": src.mode, "status": src.status,
           "speed": getattr(src, "speed", None),
           "updated": time.time() if src.mode == "replay" else src.updated,
           "event": src.event, "session": src.session_name, "year": src.year,
           "session_key": src.session_key, "finished": src.finished,
           "drivers": []}
    session = src.session
    if session is None:
        return out

    laps = src.visible_laps()
    clock = src.clock()
    out["event"] = session.event["EventName"]
    out["session"] = session.name
    try:
        out["total_laps"] = session.total_laps
    except Exception:
        out["total_laps"] = None
    out["lap"] = (int(laps["LapNumber"].max())
                  if laps is not None and not laps.empty else None)
    out["track_status"] = current_track_status(session, clock)

    drivers = []
    for driver_number in session.drivers:
        info = session.get_driver(driver_number)
        abbr = info["Abbreviation"]
        driver_laps = (laps[laps["Driver"] == abbr].sort_values("LapNumber")
                       if laps is not None else pd.DataFrame())
        last = driver_laps.iloc[-1] if not driver_laps.empty else None
        drivers.append({
            "abbr": abbr,
            "number": str(driver_number),
            "name": text(info.get("FullName")) or abbr,
            "team": text(info.get("TeamName")),
            "color": team_color(session, info),
            "laps": len(driver_laps),
            "last_lap": int(last["LapNumber"]) if last is not None else None,
            "last_time": seconds(last["LapTime"]) if last is not None else None,
            "best_time": (seconds(driver_laps["LapTime"].min())
                          if not driver_laps.empty else None),
            "line_time": seconds(last["Time"]) if last is not None else None,
            "compound": text(last["Compound"]) if last is not None else None,
            "tyre_life": number(last["TyreLife"]) if last is not None else None,
        })

    official = official_positions(session) if src.mode == "archive" else {}
    if official:
        # sessione conclusa: classifica ufficiale, con le penalità
        drivers.sort(key=lambda d: (d["abbr"] not in official,
                                    official.get(d["abbr"], 0), d["abbr"]))
        for d in drivers:
            d["position"] = official.get(d["abbr"])
        out["drivers"] = drivers
        return out

    if session.name in ("Race", "Sprint"):
        # ordine in pista: più giri completati, poi chi ha passato prima il traguardo
        drivers.sort(key=lambda d: (-d["laps"], d["line_time"] or 0, d["abbr"]))
    else:
        drivers.sort(key=lambda d: (d["best_time"] is None,
                                    d["best_time"] or 0, d["abbr"]))
    for position, d in enumerate(drivers, start=1):
        has_time = d["laps"] if session.name in ("Race", "Sprint") else d["best_time"]
        d["position"] = position if has_time else None
    out["drivers"] = drivers
    return out


def official_positions(session):
    try:
        results = session.results
    except Exception:
        return {}
    return {row.Abbreviation: int(row.Position)
            for row in results.itertuples() if pd.notna(row.Position)}


@app.get("/api/laps/{driver}")
def driver_laps(driver: str, s: str = ""):
    laps = source_for(s).visible_laps()
    if laps is None:
        return []
    laps = laps[laps["Driver"] == driver].sort_values("LapNumber")
    best = laps["LapTime"].min()
    return [{
        "lap": int(lap["LapNumber"]),
        "time": seconds(lap["LapTime"]),
        "s1": seconds(lap["Sector1Time"]),
        "s2": seconds(lap["Sector2Time"]),
        "s3": seconds(lap["Sector3Time"]),
        "compound": text(lap["Compound"]),
        "tyre_life": number(lap["TyreLife"]),
        "position": number(lap["Position"]),
        "pit": bool(pd.notna(lap["PitInTime"]) or pd.notna(lap["PitOutTime"])),
        "best": bool(pd.notna(lap["LapTime"]) and lap["LapTime"] == best),
        "deleted": bool(lap["Deleted"]) if pd.notna(lap["Deleted"]) else False,
    } for _, lap in laps.iterrows()]


@app.get("/api/telemetry/{driver}/{lap}")
def telemetry(driver: str, lap: int, s: str = ""):
    data = source_for(s).telemetry(driver, lap)
    if data is None:
        raise HTTPException(404, "Giro non disponibile")
    return data


@app.get("/api/circuit")
def circuit(s: str = ""):
    info = source_for(s).circuit
    if info is None:
        return {"corners": [], "rotation": 0}
    return {
        "rotation": float(info.rotation),
        "corners": [{
            "label": f"{int(c['Number'])}{c['Letter'] or ''}",
            "distance": number(c["Distance"], 1),
            "angle": number(c["Angle"], 1),
            "x": number(c["X"], 1),
            "y": number(c["Y"], 1),
        } for _, c in info.corners.iterrows()],
    }


@app.get("/api/schedule")
def schedule(year: int):
    """GP e sessioni di un anno, per l'archivio; "available" = già disputata."""
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    if not FIRST_YEAR <= year <= now.year:
        raise HTTPException(404, "Anno non disponibile")
    try:
        events = fastf1.get_event_schedule(year, include_testing=False)
    except Exception as exc:
        raise HTTPException(502, f"Calendario non disponibile: {exc}")
    out = []
    for _, event in events.iterrows():
        sessions = []
        for i in range(1, 6):
            name, start = event[f"Session{i}"], event[f"Session{i}DateUtc"]
            if not text(name):
                continue
            if pd.notna(start):
                available = pd.Timestamp(start) + SESSION_LENGTH.get(
                    name, DEFAULT_SESSION_LENGTH) < now
            else:
                available = year < now.year
            sessions.append({"name": name, "label": SESSION_NAMES.get(name, name),
                             "available": bool(available)})
        out.append({"round": int(event["RoundNumber"]),
                    "event": event["EventName"],
                    "country": text(event["Country"]),
                    "sessions": sessions})
    return {"years": list(range(now.year, FIRST_YEAR - 1, -1)), "events": out}


class Credentials(BaseModel):
    email: str
    password: str


def require_json(request: Request):
    # un altro sito aperto nel browser non può mandare JSON a localhost senza
    # permesso (CORS), quindi non può fare login o logout al posto tuo
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(415, "Serve una richiesta JSON")


@app.get("/api/auth")
def auth():
    token = token_manager.get_stored()
    valid = token is not None and token.is_valid()
    return {"needed": SOURCE.mode == "live" and not getattr(SOURCE, "no_auth", False),
            "logged_in": valid,
            "expires": token.expires_at if valid else None}


@app.post("/api/login")
def sign_in(credentials: Credentials, request: Request):
    require_json(request)
    try:
        # le credenziali vanno solo a Formula 1; Fast-F1 salva il token
        token_manager.get(force_renew=True, auth_func=lambda: login(
            credentials.email.strip(), credentials.password))
    except F1AuthError as exc:
        raise HTTPException(401, str(exc))
    except Exception as exc:
        log.warning("Login F1 non riuscito: %s", type(exc).__name__)
        raise HTTPException(502, "Login non riuscito, riprova tra poco")
    SOURCE.credentials_changed()
    return auth()


@app.post("/api/logout")
def sign_out(request: Request):
    require_json(request)
    clear_token()
    SOURCE.credentials_changed()
    return auth()


@app.post("/api/shutdown")
def shutdown(request: Request):
    """Pulsante "Spegni": il server gira in background, senza un terminale da chiudere."""
    require_json(request)
    log.info("Spegnimento richiesto dalla dashboard")
    # lascia il tempo di rispondere al browser prima di uscire
    threading.Timer(0.5, os._exit, [0]).start()
    return {"ok": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true",
                        help="modalità automatica (è già quella predefinita)")
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--interval", type=float, default=20,
                        help="secondi tra un ricaricamento e l'altro (default: 20)")
    parser.add_argument("--no-auth", action="store_true",
                        help="non fare il login all'account F1 (dati incompleti)")
    parser.add_argument("--no-browser", action="store_true",
                        help="non aprire il browser all'avvio")
    sub = parser.add_subparsers(dest="mode")
    for name in ("live", "replay"):
        p = sub.add_parser(name)
        p.add_argument("--year", type=int, required=True)
        p.add_argument("--event", required=True,
                       help='nome o luogo del GP, es. "Austrian Grand Prix"')
        p.add_argument("--session", default="R",
                       help="R, Q, S, SQ, FP1, FP2, FP3 (default: R)")
    sub.choices["live"].add_argument(
        "--file", required=True,
        help="file scritto da `python -m fastf1.livetiming save`")
    sub.choices["replay"].add_argument(
        "--speed", type=float, default=20,
        help="velocità della riproduzione, 20 = 20 volte il tempo reale")
    sub.choices["replay"].add_argument(
        "--start-lap", type=int, default=1,
        help="giro da cui far partire la riproduzione")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    log.setLevel(logging.INFO)
    fastf1.set_log_level("WARNING")
    # Fast-F1 ha già un suo handler: evita che ogni messaggio compaia due volte
    logging.getLogger("fastf1").propagate = False

    global SOURCE
    if args.mode == "live":
        SOURCE = LiveSource(args.year, args.event, args.session,
                            args.file, args.interval)
    elif args.mode == "replay":
        SOURCE = ReplaySource(args.year, args.event, args.session,
                              args.speed, args.start_lap)
    else:
        SOURCE = AutoLiveSource(args.interval, args.no_auth)
    SOURCE.start()

    url = f"http://localhost:{args.port}"
    log.info("Dashboard su %s", url)
    if not args.no_browser:
        threading.Timer(1.5, webbrowser.open, [url]).start()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
