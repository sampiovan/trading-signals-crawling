# Changelog

Tutte le modifiche rilevanti di questo progetto sono documentate in questo file.

Il formato segue [Keep a Changelog](https://keepachangelog.com/it-IT/1.1.0/) e il progetto aderisce al
[Semantic Versioning](https://semver.org/lang/it/).

## [Unreleased]

In roadmap: **multi-canale** (impostazioni e rischio per canale) e **budget di perdita
giornaliero** (5% del deposito iniziale, con stop delle aperture all'80% del budget).

### Added
- Guardia anti-churn: con spread largo una posizione appena aperta parte già in
  perdita dello spread e il taglio immediato innescherebbe un ciclo di
  chiusure/riaperture. Tre protezioni in `[guard]`: età minima della posizione
  (`MIN_AGE_SECONDS`), cooldown per simbolo dopo un taglio (`COOLDOWN_SECONDS`)
  e rinvio del taglio finché `CUT_LOSS` non supera `SPREAD_FACTOR` volte il
  costo corrente dello spread.

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

[Unreleased]: https://github.com/sampiovan/trading-signals-crawling/compare/v2.1.0...HEAD
[2.1.0]: https://github.com/sampiovan/trading-signals-crawling/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/sampiovan/trading-signals-crawling/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/sampiovan/trading-signals-crawling/releases/tag/v1.0.0
