<div align="center">

# 📡 F1 Telemetry Live

**A live web dashboard with speed, throttle, brake and gear for every lap of every driver, updated during the Grand Prix.**

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![FastF1](https://img.shields.io/badge/FastF1-3.9-E10600)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Plotly](https://img.shields.io/badge/Plotly-3F4F75?logo=plotly&logoColor=white)

</div>

## Features

- **Fully automatic**: reads the calendar, finds the current (or next) session, starts recording the official live-timing feed 20 minutes before the start and moves on to the next session by itself (FP → Quali → Race).
- **Every lap, every driver**: speed, throttle, brake and gear traces, shown as soon as the lap's telemetry is complete.
- **Driver comparison** with Δ time along the lap and a **track map coloured by speed**.
- **Archive mode**: open any session since 2018; **replay mode** re-streams a past session lap by lap to test between race weekends.
- **Resilient recording**: restart mid-session and it keeps appending to the same file; login changes restart the recorder with the new access.
- Recordings feed the race-engineer panel [**Muretto box**](https://github.com/GiuseppeSavoia/muretto-box).

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python server.py                    # automatic mode, opens http://localhost:8050
```

```bash
.venv/bin/python server.py replay --year 2025 --event "Austrian Grand Prix" --session R
.venv/bin/python server.py live --file race.txt --year 2026 --event "Brazil" --session R
```

Options: `--port`, `--interval`, `--no-browser`, `--no-auth`.

**F1 TV access**: full telemetry and positions require an F1 TV subscription. Log in from the dashboard (*Accedi F1 TV*): credentials go straight from the local server to Formula 1 and are never stored; only Fast-F1's access token is cached on your machine. Alternatively set `F1_USERNAME` / `F1_PASSWORD`.

- 🇮🇹 [Full guide (Italian)](docs/GUIDA.md)

---

<sub>Built by <a href="https://github.com/GiuseppeSavoia">Giuseppe Savoia</a> on top of <a href="https://github.com/theOehrly/Fast-F1">FastF1</a>. Unofficial fan project, not associated with Formula 1 companies.</sub>
