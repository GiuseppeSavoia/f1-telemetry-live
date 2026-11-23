# F1 Telemetria Live

Dashboard web con velocità, gas, freno e marcia di **ogni giro di ogni pilota**, aggiornata
durante il GP. Puoi confrontare due piloti (con il Δ tempo lungo il giro) e vedere il
tracciato colorato in base alla velocità.

Usa Fast-F1 3.9 in un ambiente separato (`.venv`).

```bash
cd f1-telemetry-live
.venv/bin/python server.py
```

e basta: trova da solo la sessione in corso (o aspetta la prossima), registra e mostra i dati.

## Installazione (una volta sola)

L'ambiente `.venv` è già pronto. Se va ricreato:

```bash
cd f1-telemetry-live
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Durante un GP

Clicca l'icona **F1 Telemetria** nel Dock. La prima volta trascinala nel Dock dalla cartella
`live_dashboard` (è `F1 Telemetria.app`) e lascia l'app in quella cartella.

L'icona avvia la dashboard in background (senza terminale) e la apre nel browser; se è già
accesa la apre e basta. Per spegnerla usa il pulsante **Spegni** in alto a destra nella
dashboard. I messaggi del programma finiscono in `server.log`.

In alternativa, da terminale:

```bash
cd f1-telemetry-live
.venv/bin/python server.py
```

Il programma:

1. apre la dashboard nel browser (http://localhost:8050);
2. guarda il calendario e trova la sessione in corso. Se non ce n'è nessuna mostra quando
   inizia la prossima e aspetta: puoi avviarlo anche ore prima;
3. 20 minuti prima dell'inizio si collega al live timing e salva i dati in `recordings/`;
4. aggiorna la dashboard da sola ogni 20 secondi (`--interval` per cambiare). Un giro
   compare quando è arrivata tutta la sua telemetria;
5. a fine sessione smette di registrare e, se lo lasci acceso, passa da solo alla sessione
   successiva (es. dalle prove libere alla qualifica).

### Login F1 TV

Telemetria e posizioni arrivano solo con un abbonamento F1 TV. Clicca **Accedi F1 TV** in
alto a destra nella dashboard e inserisci email e password dell'account F1:

- vanno dal server locale direttamente a Formula 1 e non vengono salvate; sul Mac resta solo
  il token di accesso (in `~/Library/Application Support/fastf1/`), che vale fino alla sua
  scadenza;
- se accedi a registrazione già iniziata, la registrazione riparte da sola con l'accesso;
- quando il token scade il pulsante torna rosso: basta rifare l'accesso;
- cliccando **✓ F1 TV** vedi fino a quando vale l'accesso e puoi uscire.

In alternativa puoi impostare le variabili d'ambiente `F1_USERNAME` e `F1_PASSWORD` prima
di avviare il programma.

Se il programma si chiude a metà sessione, riavvialo: riprende ad aggiungere dati allo
stesso file in `recordings/`.

Opzioni utili (vanno prima di `live`/`replay`): `--port 8060`, `--no-browser`,
`--no-auth` (senza login: dati incompleti).

### Con un file registrato a parte

Se registri da solo con `.venv/bin/python -m fastf1.livetiming save gara.txt`:

```bash
.venv/bin/python server.py live --file gara.txt --year 2026 --event "Brazil" --session R
```

Sessioni: `R` gara, `Q` qualifica, `S` sprint, `SQ` sprint shootout, `FP1`/`FP2`/`FP3` prove.

## Archivio: tutte le sessioni dal 2018

In alto a sinistra c'è il menu **● Live**: scegli una stagione (dal 2018, prima stagione con
la telemetria), poi il GP e la sessione (prove libere, qualifica, sprint, gara). La live
continua a girare e a registrare in background; per tornarci scegli di nuovo **● Live**.

- La prima volta che apri una sessione i dati vengono scaricati (circa 30 secondi), poi
  restano nella cache di Fast-F1 in `~/Library/Caches/fastf1` e si aprono in pochi secondi.
  Una gara occupa circa 100–150 MB su disco, le altre sessioni meno: se serve spazio puoi
  cancellare le cartelle degli anni che non guardi più.
- La classifica è quella ufficiale (con le penalità) e accanto a ogni pilota c'è il suo giro
  più veloce; il viola è il più veloce di tutti. Cliccando un pilota si apre il suo giro migliore.
- L'indirizzo della pagina cambia con la sessione (es. `#2021|Abu Dhabi Grand Prix|Race`):
  puoi salvarlo nei preferiti o usare Indietro nel browser.

## Prova con una gara passata (replay)

Riproduce una sessione già scaricata, mostrando i giri come se fosse live:

```bash
.venv/bin/python server.py replay --year 2025 --event "Austrian Grand Prix" --session R --speed 20
```

`--speed 20` = 20 volte più veloce del reale, `--start-lap 30` parte dal giro 30.

## Come si usa

- **Classifica a sinistra**: clic su un pilota per vederne i giri.
- **Striscia dei giri**: clic su un giro; viola = suo giro più veloce, il colore sotto è la
  gomma. "Segui l'ultimo giro" mostra sempre il giro appena completato.
- **Confronta con**: aggiunge un secondo pilota, sullo stesso giro, sul suo giro più veloce o
  su un giro a scelta. In alto compare il Δ tempo: sopra lo zero il secondo pilota è più lento.
