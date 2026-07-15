# Trading Signals Crawling

[![CI](https://github.com/sampiovan/trading-signals-crawling/actions/workflows/ci.yml/badge.svg)](https://github.com/sampiovan/trading-signals-crawling/actions/workflows/ci.yml)

> **Versioni** — su questo ramo (`main`) è in sviluppo la **v2.0** per **MetaTrader 5**: esecuzione ordini diretta da Python col package ufficiale `MetaTrader5`, senza Expert Advisor né ponte CSV. Cerchi la **v1 stabile per MetaTrader 4** (crawler + EA MQL4 via CSV)? È congelata nel tag [`v1.0.0`](https://github.com/sampiovan/trading-signals-crawling/releases/tag/v1.0.0) e mantenuta sul branch `release/v1.x`.

Sistema di copy-trading automatico: un crawler Python si connette a un canale Telegram con [Telethon](https://docs.telethon.dev/), riconosce i messaggi contenenti segnali di trading e **li esegue direttamente su MetaTrader 5** tramite il package ufficiale [`MetaTrader5`](https://pypi.org/project/MetaTrader5/), con esito sincrono, retry sugli errori transitori e notifica Telegram sui fallimenti.

## Architettura

```
Canale Telegram
      │  (nuovi messaggi + catch-up dei messaggi persi)
      ▼
Crawler Python (Telethon)
      │  parse del messaggio → segnali strutturati
      ▼
Executor (package MetaTrader5)
      │  order_send con retry → esito sincrono
      ▼
Terminale MetaTrader 5 (conto HEDGING)
      │
      └──► posizioni/ordini live = fonte di verità per il matching
           dei messaggi successivi (modify, move SL, close, cancel)

Fallimento definitivo ──► notifica nei Saved Messages di Telegram
```

Rispetto alla v1 non esiste più un registro ordini su file: quando un messaggio cita un ordine per prezzo (es. "CHIUDERE … (1.12500)"), il crawler lo cerca **tra le posizioni e i pending reali del conto** ([order_lookup.py](src/crawler/order_lookup.py)), con tolleranza pip-aware e best-match — e tramite il **commento dell'ordine**, che contiene il prezzo di apertura inviato dal canale arrotondato al pip (`@1.3390`, `@145.50` per le coppie JPY). Il commento è l'identificatore stabile del segnale: non cambia se il fill avviene a un livello diverso e sopravvive ai tagli/riaperture della guardia posizioni, dove accumula anche la perdita realizzata (`@1.3390 (-120)`). Sulla modifica del prezzo di un pending, il commento viene aggiornato ricreando l'ordine (MT5 non permette di cambiare il commento con una semplice modify).

### Tipi di messaggio riconosciuti

Il parser ([msg_parser.py](src/crawler/msg_parser.py)) riconosce questi tipi di messaggio:

| Tipo (`message_type`) | Significato | Esempio di messaggio |
|---|---|---|
| `placement` | Piazzamento di un ordine pendente | `📈BUY LIMIT EUR/USD  Prezzo 1.12500 (di apertura) …` |
| `placement` (a mercato) | Operazione diretta a mercato, eseguita subito al prezzo corrente (Ask/Bid) | `ATTENZIONE QUESTA E' UNA OPERAZIONE IN SELL DIRETTA A MERCATO: … SELL GBP/USD Prezzo 1.34121 …` |
| `open` | Ordine aperto: se deriva da un `placement` noto, viene solo verificato che il pending sia diventato posizione; senza placement corrispondente viene aperto a mercato | `Ordine Buy EUR/USD Aperto  Prezzo di ingresso 1.12500` |
| `modify` | Modifica del prezzo di ingresso di un pending (o del solo SL se mirata via reply) | `(BUY LIMIT EUR/USD) - MODIFICARE IL PREZZO DI INGRESSO DA … A …` |
| `move_sl` | Spostamento dello stop loss: "Move Stop Loss to Breakeven…" (se arriva come **risposta Telegram** al messaggio di apertura diventa una modifica mirata al singolo ticket) oppure "MODIFICARE IL VALORE DI STOP LOSS SU TUTTE LE OPERAZIONI IN CORSO SU …". Senza ticket, il nuovo SL è applicato a **tutte** le posizioni sull'asset | `GBP/USD Move Stop Loss to Breakeven … a 1.33890✅` |
| `close` | Chiusura manuale di una posizione | `CHIUDERE MANUALMENTE UNA POSIZIONE … (1.12500)` |
| `close` (multiplo) | Chiusura di più posizioni (anche su asset diversi): un ordine di chiusura per ognuna; quelle non trovate vengono saltate con errore nel log senza bloccare le altre | `CHIUDERE MANUALMENTE QUATTRO POSIZIONI DI CUI: …` |
| `cancel` | Annullamento di un ordine pendente | `ANNULLARE BUY LIMIT EUR/USD … (1.12500)` |
| — (notifica) | Chiusura automatica già avvenuta al broker (SL o breakeven): riconosciuta e loggata, nessun ordine | `CHIUSA A BREAKEVEN GBP/USD A (1.35290)✅` |

> Il crawler legge anche il messaggio **citato** quando un segnale arriva come risposta Telegram: serve a collegare lo spostamento dello stop loss all'ordine esatto a cui si riferisce.

## Struttura della repository

```
.
├── src/crawler/              # Package Python (layout src, installabile con pip)
│   ├── main.py               # Entry point: CLI, Telegram, catch-up, pipeline di esecuzione
│   ├── __main__.py           # Avvio con `python -m crawler`
│   ├── config.py             # Caricamento e validazione di config.ini
│   ├── crawler_state.py      # Stato: ultimo messaggio processato (per il catch-up)
│   ├── executor.py           # Esecuzione dei segnali su MT5 (order_send + retry)
│   ├── mt5_client.py         # Connessione al terminale, check hedging, simboli
│   ├── log_setup.py          # Logging (file con rotazione + console)
│   ├── msg_parser.py         # Riconoscimento dei messaggi via regex
│   └── order_lookup.py       # Matching segnale → ticket sulle posizioni live
├── scripts/                  # Install/uninstall del servizio Windows
├── tests/                    # Unit test (pytest)
├── CHANGELOG.md
├── config.example.ini
├── pyproject.toml            # Metadati, dipendenze ed entry point (PEP 621)
└── README.md
```

## Requisiti

- **Windows** (il package `MetaTrader5` è disponibile solo per Windows)
- Python 3.9+
- **MetaTrader 5** installato, in esecuzione e loggato su un conto **HEDGING** (i conti netting ammettono una sola posizione per simbolo e non sono supportati: il canale tiene più posizioni sullo stesso cambio — il crawler lo verifica all'avvio e si rifiuta di partire su conti netting), con il pulsante **Algo Trading** della toolbar abilitato: se è spento il terminale rifiuta ogni ordine con retcode `10027 AutoTrading disabled by client` (i rifiuti vengono comunque notificati come alert, ma i segnali nel frattempo vanno persi)
- Un account Telegram con API ID e API hash ([my.telegram.org](https://my.telegram.org))

## Installazione

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

Copia `config.example.ini` in `config.ini` (nella root del progetto, o dove preferisci: il percorso si passa con `--config`) e compila i valori:

```ini
[telegram]
YOUR_API_ID = 123456
YOUR_API_HASH = abcdef0123456789abcdef0123456789
SESSION_NAME = crawler_session
CHANNEL_ENTITY = @nome_canale

[mt5]
; vuoto = terminale MT5 di default
TERMINAL_PATH =
; suffisso simboli del broker (es. .m per EURUSD.m), vuoto se non usato
SYMBOL_SUFFIX =
; slippage massimo in punti (30 = 3 pip a 5 cifre)
DEVIATION_POINTS = 30

[risk]
; FIXED = lotto fisso | RISK_PERCENT = rischio % per trade | BALANCE = scalini sul balance
MODE = BALANCE
FIXED_LOT = 0.01
RISK_PERCENT = 1.0
; deposito iniziale: vuoto = rilevato dal balance al primo avvio e persistito
INITIAL_DEPOSIT =
AVAILABLE_PERCENT = 10
BALANCE_STEP = 1000
LOT_PER_STEP = 0.01

[guard]
; guardia delle posizioni in perdita: taglio e riapertura immediata
ENABLED = true
CUT_LOSS = 125
INTERVAL_SECONDS = 15
```

### Gestione del rischio

Il volume di ogni ordine è calcolato da [risk.py](src/crawler/risk.py):

- **`MODE = BALANCE`**: `LOT_PER_STEP` lotti (default 0.01) ogni `BALANCE_STEP` (default 1000, valuta del conto) di capitale **disponibile**, dove disponibile = balance − (100 − `AVAILABLE_PERCENT`)% del deposito iniziale. Con deposito 100k e il 10% disponibile: a balance 100k → 0.10 lotti; i lotti seguono solo i profitti/perdite **realizzati** (balance, niente flottante). Il deposito iniziale viene da `INITIAL_DEPOSIT` in config oppure, se vuoto, è rilevato dal balance al primo avvio e persistito in `crawler_state.json`.
- **`MODE = FIXED`**: lotto fisso `FIXED_LOT`, come nella v1.
- **`MODE = RISK_PERCENT`**: rischia al massimo `RISK_PERCENT`% dell'equity per trade — il lotto è calcolato dalla distanza dello Stop Loss e dal valore del tick del simbolo (`rischio / perdita-per-lotto-se-SL-colpito`). Se il segnale non ha SL, fallback su `FIXED_LOT` con warning nel log.

In tutti i casi il volume è normalizzato sui limiti del simbolo (min/max/step del broker).

### Guardia delle posizioni in perdita

Un controllo periodico ([position_guard.py](src/crawler/position_guard.py), ogni `INTERVAL_SECONDS`) sorveglia le posizioni aperte dal crawler (riconosciute dal commento `@prezzo`): quando la **perdita di prezzo** di una posizione supera `CUT_LOSS` (valuta del conto, esclusi swap e commissioni), la posizione viene **chiusa e riaperta immediatamente** a mercato con stessi direzione, volume, SL, TP e magic number. Il commento della nuova posizione conserva il prezzo originale del canale e accumula la perdita realizzata — inclusi swap e commissioni, arrotondata agli interi: `@1.3390 (-120)`, poi `@1.3390 (-245)` a un secondo taglio. I segnali successivi del canale (chiusura, move SL) ritrovano la posizione riaperta proprio grazie al commento. Le posizioni manuali o di altri sistemi non vengono mai toccate. Se la riapertura fallisce, arriva un alert nei Saved Messages con i dati per riaprire a mano.

> ⚠️ `config.ini` contiene credenziali: non va mai committato (è già escluso dal `.gitignore`, insieme ai file `.session` di Telethon).

## Utilizzo

Con il terminale MT5 aperto e loggato sul conto:

```bash
signals-crawler                       # config.ini nella directory corrente
signals-crawler --config C:\percorso\config.ini
python -m crawler                     # equivalente
```

Tutti i file di runtime — sessione Telegram, `crawler_state.json`, `logs/` — vengono creati **accanto al file di config**, quindi il comando funziona da qualsiasi directory.

Al primo avvio Telethon chiede il numero di telefono e il codice di verifica, poi salva la sessione e i login successivi sono automatici. Il crawler logga su console e in `logs/crawler.log` (rotazione giornaliera, 30 giorni di retention). Tieni d'occhio le righe `Messaggio non riconosciuto come segnale di trading`: il provider a volte varia le diciture (es. "Livello di ingresso" al posto di "Prezzo di ingresso") — se il messaggio scartato era in realtà un segnale, è una nuova variante di formato da aggiungere al parser.

Al riavvio il crawler **recupera i messaggi persi** durante il downtime: l'ID dell'ultimo messaggio processato è salvato in `crawler_state.json` e i messaggi successivi vengono riprocessati in ordine. Al primo avvio in assoluto lo storico del canale NON viene riprocessato. Lo stato avanza anche sui messaggi non riconosciuti o falliti (il catch-up è deterministico, non ritenta): un segnale sfuggito — formato nuovo, Algo Trading spento, ecc. — va eventualmente riallineato **a mano** sul terminale.

Ogni esecuzione fallita in modo definitivo (dopo i retry) viene **notificata nei tuoi Saved Messages** di Telegram.

Dopo ogni aggiornamento del codice (`git pull`) il crawler va **riavviato**: il processo in esecuzione non ricarica i sorgenti.

### Esecuzione come servizio (Windows)

```powershell
# dalla root del progetto
powershell -ExecutionPolicy Bypass -File scripts\install-task.ps1

# rimozione
powershell -ExecutionPolicy Bypass -File scripts\uninstall-task.ps1
```

La task parte al logon, esegue `python -m crawler --config <path>` col Python del venv (default: `config.ini` nella root del progetto, personalizzabile con `-ConfigPath`) e viene riavviata (fino a 10 volte, a distanza di 1 minuto) se il processo esce con errore. Stato con `Get-ScheduledTask TradingSignalsCrawler`, log in `logs\crawler.log` accanto al config.

Per riavviare il crawler (es. dopo un aggiornamento del codice):

```powershell
Stop-ScheduledTask TradingSignalsCrawler; Start-ScheduledTask TradingSignalsCrawler
```

## Sviluppo

```bash
pip install -e ".[dev]"
ruff check .    # lint
pytest          # unit test (parser, executor, lookup, config, stato)
```

I test girano senza MetaTrader 5 installato (il modulo è stubbato) e anche in CI su Linux: la dipendenza `MetaTrader5` è marcata `sys_platform == 'win32'` e l'import è difensivo.

Quando il canale introduce una variante di formato nei messaggi, il testo reale va aggiunto come fixture in `tests/test_msg_parser.py` insieme al fix della regex: i messaggi veri del canale sono la base di tutti i test del parser.

## Disclaimer

Questo progetto è a scopo personale/didattico. Il trading automatico comporta rischi finanziari significativi: prima di qualsiasi utilizzo reale, lascia girare il sistema **qualche giorno su un conto demo col canale vero**, così da validare parsing e flusso live con i messaggi reali del provider senza rischio.
