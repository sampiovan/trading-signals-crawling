# Changelog

Tutte le modifiche rilevanti di questo progetto sono documentate in questo file.

Il formato segue [Keep a Changelog](https://keepachangelog.com/it-IT/1.1.0/) e il progetto aderisce al
[Semantic Versioning](https://semver.org/lang/it/).

## [Unreleased]

In roadmap: **multi-canale** (impostazioni e rischio per canale) e **budget di perdita
giornaliero** (5% del deposito iniziale, con stop delle aperture all'80% del budget).

### Fixed
- Messaggio "Aperto" non duplica più la posizione: era la notifica di riempimento
  di un pending ma, se il lookup non lo trovava, apriva un ordine a mercato nuovo.
  Ora un "Aperto" arrivato come risposta Telegram usa il prezzo del placement citato
  per identificare il pending, e su un miss viene scartato con alert invece di
  aprire alla cieca. Concausa risolta: un refuso col prezzo spaziato ("1. 20200")
  veniva troncato a "1." dal parser.
- Prezzi con spazi spuri interni ("1. 20200") normalizzati su TUTTI i tipi di
  messaggio (placement, open, modify, close, cancel, move SL): un token di prezzo
  condiviso tollera lo spazio e un helper unico lo ripulisce prima del lookup.
- Refuso dell'asset nel canale (es. "GPS/USD" per GBP/USD) non fa più perdere il
  segnale: se il simbolo non è risolvibile, il lookup ripiega sul commento
  "@prezzo" cercandolo su tutte le posizioni e i pending del conto (col pip del
  simbolo reale di ogni candidato) e procede solo con un match univoco.
- Lo scarto di un segnale per ordine non trovato ora arriva anche nei Saved
  Messages di Telegram, come i fallimenti di esecuzione: una chiusura persa
  non passa più inosservata nel solo log.

### Changed
- Soglia della guardia in percentuale del budget giornaliero: `CUT_LOSS` (importo
  fisso in valuta del conto) sostituita da `CUT_LOSS_PERCENT` (default 2.5),
  percentuale della perdita giornaliera consentita = `DAILY_LOSS_PERCENT`
  (nuova chiave `[risk]`, default 5) del deposito iniziale.
  Il budget è calcolato sul deposito iniziale, quindi ogni taglio consuma una
  frazione nota e costante del budget anche quando il balance (e i lotti del
  sizing BALANCE) cresce. Migrazione: il vecchio `CUT_LOSS = 125` equivale a
  `CUT_LOSS_PERCENT = 2.5` con deposito 100k e limite giornaliero del 5%.
- Default della guardia più conservativi, allineati ai valori in uso:
  `INTERVAL_SECONDS` da 15 a 60 (un check al minuto basta: il taglio non è
  un'operazione da tempo di reazione) e `MIN_AGE_SECONDS` da 60 a 300 (l'età
  minima fa anche da pausa tra un taglio e l'altro, e a 60s le riaperture si
  susseguivano troppo in fretta). Chi li aveva già espliciti in config non è
  toccato.

### Fixed
- Scrittura atomica di `crawler_state.json` (file temporaneo + rename): un crash a
  metà scrittura corrompeva lo stato e al riavvio il catch-up ripartiva "da primo
  avvio", saltando in silenzio i segnali persi durante il downtime.
- Niente posizioni duplicate al riavvio dopo un crash: l'ordine viene eseguito
  prima del salvataggio dello stato, quindi il replay del catch-up poteva
  rieseguire un placement/open a mercato. Ora in catch-up i segnali che aprono
  esposizione vengono saltati se un ordine live corrisponde già (lookup per
  commento "@prezzo", l'identificatore stabile del segnale).

## [2.2.0] - 2026-07-16

### Added
- Guardia anti-churn: con spread largo una posizione appena aperta parte già in
  perdita dello spread e il taglio immediato innescherebbe un ciclo di
  chiusure/riaperture. Due protezioni in `[guard]`: età minima della posizione
  (`MIN_AGE_SECONDS` — ogni riaperta è una posizione nuova, quindi fa anche da
  pausa tra un taglio e l'altro) e rinvio del taglio finché `CUT_LOSS` non
  supera `SPREAD_FACTOR` volte il costo corrente dello spread.
- Blackout notizie: guardia sospesa su tutti gli asset per
  ±`NEWS_BLACKOUT_MINUTES` attorno a qualunque evento ad alto impatto, dal
  calendario settimanale gratuito di Forex Factory (in cache solo gli eventi
  High, refresh ogni 6 ore, fail-open senza feed né cache). Solo i tagli della
  guardia: i segnali del canale vengono sempre eseguiti.

## [2.1.0] - 2026-07-16

### Added
- Commenti degli ordini = prezzo di apertura del canale arrotondato al pip
  ("@1.3390", "@145.50" per JPY): identificatore stabile del segnale, usato anche
  dal lookup; sulla modifica di un pending l'ordine viene ricreato per aggiornare
  il commento (MT5 non permette di cambiarlo con la modify).
- Sizing `MODE=BALANCE`: 0.01 lotti ogni 1000 (valuta del conto) di capitale
  disponibile (= balance − 90% del deposito iniziale); deposito da config o
  rilevato al primo avvio e persistito.
- Guardia delle posizioni in perdita: oltre `CUT_LOSS` (default 125, esclusi
  swap/commissioni) la posizione viene chiusa e riaperta con stessi SL/TP/volume,
  accumulando nel commento la perdita realizzata: "@1.3390 (-120)".
- La guardia adotta anche le posizioni legacy del vecchio executor (commento
  `placement`/`open`) e quelle senza commento (es. aperte a mano): al primo
  taglio il prezzo di apertura reale diventa il prezzo del commento e la
  riaperta migra al formato nuovo.

### Fixed
- Perdita realizzata della guardia sempre "(-0)": `history_deals_get` chiamata
  con le date insieme a `position=` ignorava il filtro e sommava tutti i deal
  del conto (deposito incluso). Ora la chiamata è senza date, con filtro su
  `position_id`, attesa del deal di uscita e fallback sulla stima
  profit+swap al momento del taglio.

### Changed
- Layout `src/` con import assoluti (`crawler.*`) e packaging PEP 621 completo:
  installazione con `pip install -e .`, dipendenze in `pyproject.toml`
  (rimossi `requirements.txt`/`requirements-dev.txt`), entry point `signals-crawler`
  e supporto a `python -m crawler`.
- Nuovo argomento `--config`: sessione Telegram, stato del catch-up e log vengono
  creati accanto al file di config — nessuna dipendenza dalla working directory.
- `install-task.ps1` aggiornato al nuovo avvio (`python -m crawler --config <path>`,
  parametro `-ConfigPath`).

## [2.0.0] - 2026-07-15

Passaggio a **MetaTrader 5** con esecuzione ordini diretta da Python (package ufficiale
`MetaTrader5`): eliminati l'Expert Advisor e il ponte CSV. Collaudata end-to-end su conto
demo hedging (12/12 operazioni: pending, modify, cancel, market, move SL multiplo, close).

### Added
- Executor MT5: esecuzione diretta dei segnali via `order_send` con esito sincrono,
  retry sui retcode transitori (requote, prezzo cambiato, connessione) e filling mode
  per simbolo; slippage e lotto configurabili in `[mt5]`.
- Connessione al terminale con verifica bloccante del conto **hedging**.
- Lookup degli ordini sulle posizioni e sui pending live del conto (tolleranza pip-aware,
  best-match) al posto del registro CSV.
- Notifica Telegram (Saved Messages) sui fallimenti definitivi di esecuzione.
- Risk management: sezione `[risk]` con lotto fisso (default, parità v1) o sizing
  `RISK_PERCENT` calcolato da equity, distanza SL e tick value del simbolo,
  con normalizzazione sui limiti di volume del broker e fallback senza SL.

### Fixed
- Riconosciuta la variante "Livello di ingresso" (oltre a "Prezzo di ingresso") nei
  messaggi di apertura, emersa dall'esercizio live.

### Removed
- Expert Advisor MQL4 e ponte CSV (`trading_signals.csv`, `order_registry.csv`):
  restano disponibili nella v1 (tag `v1.0.0`, branch `release/v1.x`).

## [1.0.0] - 2026-07-13

Versione stabile per **MetaTrader 4**: crawler Telegram (Python/Telethon) + Expert Advisor MQL4
comunicanti via CSV. Congelata nel tag `v1.0.0`; eventuali hotfix sul branch `release/v1.x`.

### Added
- Crawler Telegram: riconoscimento di 10 tipi di messaggio (piazzamento pendente e a mercato,
  apertura, modifica, spostamento stop loss — anche mirato via reply Telegram —, chiusura singola
  e multipla, annullamento, notifiche di chiusura automatica).
- Expert Advisor MQL4: esecuzione ordini da CSV con magic number, slippage in pip configurabile,
  lotto validato sui limiti del simbolo, registro ordini e stato persistente.
- Catch-up dei messaggi persi durante i downtime del crawler.
- Esecuzione come servizio Windows (Scheduled Task con riavvio automatico).
- Validazione del config all'avvio; 50 unit test; lint ruff; CI GitHub Actions.

### Fixed
- Registro ordini troncato a ogni scrittura; registro non aggiornato dopo le modify.
- Riesecuzione dei segnali storici al riavvio dell'EA; segnali nello stesso secondo persi.
- Posizione duplicata sul messaggio "ordine aperto" con pending già triggerato.
- Multi-close che chiudeva una sola posizione (e sull'asset sbagliato nei messaggi multi-asset).
- Ordini a mercato inviati con prezzo 0 (rifiutati dal broker).
- Matching nel registro con tolleranza fissa inadatta alle coppie JPY.

[Unreleased]: https://github.com/sampiovan/trading-signals-crawling/compare/v2.2.0...HEAD
[2.2.0]: https://github.com/sampiovan/trading-signals-crawling/compare/v2.1.0...v2.2.0
[2.1.0]: https://github.com/sampiovan/trading-signals-crawling/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/sampiovan/trading-signals-crawling/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/sampiovan/trading-signals-crawling/releases/tag/v1.0.0
