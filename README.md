# Trading Signals Crawling

[![CI](https://github.com/sampiovan/trading-signals-crawling/actions/workflows/ci.yml/badge.svg)](https://github.com/sampiovan/trading-signals-crawling/actions/workflows/ci.yml)

> **Versioni** — la **v1.x** (questo ramo) è la versione stabile per **MetaTrader 4**: crawler Python + Expert Advisor MQL4 comunicanti via CSV. La **v2.0** in roadmap passerà a **MetaTrader 5** con esecuzione diretta degli ordini da Python (package ufficiale `MetaTrader5`), eliminando EA e ponte CSV.

Sistema di copy-trading automatico composto da due componenti che comunicano tramite file CSV:

1. **Crawler Python** (`crawler/`) — si connette a un canale Telegram con [Telethon](https://docs.telethon.dev/), riconosce i messaggi contenenti segnali di trading e li scrive in un file CSV nella cartella `Files` di MetaTrader 4.
2. **Expert Advisor MQL4** (`mt4/Crawler_Trading_Signal.mq4`) — in esecuzione su MetaTrader 4, legge periodicamente il CSV dei segnali (via `OnTimer`), esegue gli ordini corrispondenti e registra i ticket assegnati dal broker in un secondo CSV (`order_registry.csv`), che il crawler rilegge per associare le operazioni successive (modifica, chiusura, annullamento) all'ordine giusto.

## Architettura

```
Canale Telegram
      │  (nuovi messaggi)
      ▼
Crawler Python (Telethon)
      │  parse del messaggio → segnale strutturato
      ▼
trading_signals.csv          ◄── cartella MQL4/Files/ di MT4
      │  (lettura periodica ogni TIMER_SECONDS)
      ▼
Expert Advisor (MQL4)
      │  OrderSend / OrderModify / OrderClose / OrderDelete
      ▼
order_registry.csv  ──►  riletto dal crawler per risalire a ticket e magic number
```

### Flusso dei segnali

Il parser ([msg_parser.py](crawler/msg_parser.py)) riconosce questi tipi di messaggio:

| Tipo (`message_type`) | Significato | Esempio di messaggio |
|---|---|---|
| `placement` | Piazzamento di un ordine pendente | `📈BUY LIMIT EUR/USD  Prezzo 1.12500 (di apertura) …` |
| `placement` (a mercato) | Operazione diretta a mercato: l'EA esegue subito al prezzo corrente (Ask/Bid) | `ATTENZIONE QUESTA E' UNA OPERAZIONE IN SELL DIRETTA A MERCATO: … SELL GBP/USD Prezzo 1.34121 (nostro prezzo di apertura) …` |
| `open` | Ordine aperto: se deriva da un `placement` noto, l'EA verifica solo che il pending sia diventato posizione; se non c'è un placement nel registro, l'EA apre un ordine diretto a mercato | `Ordine Buy EUR/USD Aperto  Prezzo di ingresso 1.12500` |
| `modify` | Modifica del prezzo di ingresso (o del solo SL, se mirata via reply — vedi `move_sl`) | `(BUY LIMIT EUR/USD) - MODIFICARE IL PREZZO DI INGRESSO DA … A …` |
| `move_sl` | Spostamento dello stop loss. Due varianti: "Move Stop Loss to Breakeven … a 1.33890" (se arriva come **risposta Telegram** al messaggio di apertura e l'ordine è nel registro, diventa una `modify` mirata sul singolo ticket) e "MODIFICARE IL VALORE DI STOP LOSS SU TUTTE LE OPERAZIONI IN CORSO SU EUR/USD a …". Senza ticket, l'EA applica il nuovo SL a **tutte** le posizioni a mercato sull'asset | `GBP/USD Move Stop Loss to Breakeven … a 1.33890✅` |
| `close` | Chiusura manuale di una posizione | `CHIUDERE MANUALMENTE UNA POSIZIONE … (1.12500)` |
| `close` (multiplo) | Chiusura di più posizioni (anche su asset diversi): produce un segnale `close` per ognuna; le posizioni non trovate nel registro vengono saltate con errore nel log, senza bloccare le altre | `CHIUDERE MANUALMENTE QUATTRO POSIZIONI DI CUI: UNA IN PROFITTO su EUR/USD (1.14700) …` |
| `cancel` | Annullamento di un ordine pendente | `ANNULLARE BUY LIMIT EUR/USD … (1.12500)` |
| — (notifica) | Chiusura automatica già avvenuta al broker (SL o breakeven): il crawler la riconosce e logga, nessun ordine | `CHIUSA A BREAKEVEN GBP/USD A (1.35290)✅`, `CHIUSURA IN STOP (4.704.50)` |

> Il crawler legge anche il messaggio **citato** quando un segnale arriva come risposta Telegram: serve a collegare lo spostamento dello stop loss all'ordine esatto a cui si riferisce.

Al momento del `placement` il crawler genera un **magic number** ([utils.py](crawler/utils.py)) che identifica il segnale. L'EA, dopo aver piazzato l'ordine, scrive in `order_registry.csv` la coppia magic number ↔ ticket del broker. Per i messaggi successivi (`open`, `modify`, `close`, `cancel`) il crawler consulta il registro ([order_registry.py](crawler/order_registry.py)) e recupera il ticket confrontando asset e prezzo di ingresso.

## Struttura della repository

```
.
├── crawler/                       # Package Python del crawler
│   ├── main.py                    # Entry point: client Telegram ed event handler
│   ├── config.py                  # Caricamento centralizzato di config.ini
│   ├── log_setup.py               # Configurazione del logging (file + console)
│   ├── msg_parser.py              # Riconoscimento dei messaggi via regex
│   ├── order_registry.py          # Lettura di order_registry.csv e lookup dei ticket
│   ├── signals_csv.py             # Scrittura del CSV dei segnali
│   └── utils.py                   # Generazione del magic number
├── mt4/
│   └── Crawler_Trading_Signal.mq4 # Expert Advisor per MetaTrader 4
├── tests/                         # Test unitari (pytest)
├── config.example.ini             # Template di configurazione
├── requirements.txt               # Dipendenze Python
├── requirements-dev.txt           # Dipendenze di sviluppo (pytest, ruff)
└── README.md
```

## Requisiti

- Python 3.9+
- Un account Telegram con API ID e API hash (ottenibili su [my.telegram.org](https://my.telegram.org))
- MetaTrader 4 installato sulla stessa macchina (il crawler scrive direttamente nella cartella `MQL4/Files/` del terminale)

## Installazione

### 1. Crawler Python

```bash
python -m venv .venv
.venv\Scripts\activate       # Windows
pip install -r requirements.txt
```

Copia `config.example.ini` in `config.ini` e compila i valori:

```ini
[telegram]
YOUR_API_ID = 123456
YOUR_API_HASH = abcdef0123456789abcdef0123456789
SESSION_NAME = crawler_session
CHANNEL_ENTITY = @nome_canale

[paths]
MT4_FILES_FOLDER = C:\Users\<utente>\AppData\Roaming\MetaQuotes\Terminal\<ID_TERMINALE>\MQL4\Files
```

> ⚠️ `config.ini` contiene credenziali: non va mai committato (è già escluso dal `.gitignore`, insieme ai file `.session` di Telethon).

### 2. Expert Advisor

1. Apri MetaEditor e copia `mt4/Crawler_Trading_Signal.mq4` in `MQL4/Experts/`.
2. Compila il file (F7).
3. Trascina l'EA su un grafico qualsiasi e abilita l'**AutoTrading**.

Parametri di input dell'EA:

| Parametro | Default | Descrizione |
|---|---|---|
| `CSV_FILENAME` | `trading_signals.csv` | Nome del CSV dei segnali (in `MQL4/Files/`) |
| `LOT_SIZE` | `0.01` | Dimensione del lotto (validata contro MINLOT/MAXLOT/LOTSTEP del simbolo) |
| `TIMER_SECONDS` | `10` | Intervallo di lettura del CSV |
| `SLIPPAGE_PIPS` | `3` | Slippage massimo in pip, convertito automaticamente in punti in base ai Digits del broker (4 o 5 cifre) |

### File di stato dell'EA

L'EA salva in `MQL4/Files/crawler_ea_state.txt` il numero di righe del CSV già processate, così un riavvio del terminale **non riesegue i segnali storici**. Se il CSV dei segnali viene ricreato o svuotato (es. rotazione manuale per contenerne la crescita), l'EA lo rileva e riparte da zero contando solo le righe nuove. Per riprocessare tutto da capo, eliminare il file di stato.

## Utilizzo

Avvia il crawler dalla cartella del package (il `config.ini` deve trovarsi nella directory di lavoro):

```bash
cd crawler
python main.py
```

Al primo avvio Telethon chiede il numero di telefono e il codice di verifica, poi salva la sessione nel file `<SESSION_NAME>.session` e i login successivi sono automatici.

Il crawler resta in ascolto dei nuovi messaggi del canale e logga l'attività sia su console sia in `logs/crawler.log` (rotazione giornaliera a mezzanotte, 30 giorni di retention).

Al riavvio il crawler **recupera i messaggi persi** durante il downtime: l'ID dell'ultimo messaggio processato è salvato in `crawler_state.json` (nella directory di lavoro) e all'avvio i messaggi successivi vengono riprocessati in ordine. Al primo avvio in assoluto lo storico del canale NON viene riprocessato.

### Esecuzione come servizio (Windows)

Per far girare il crawler in autonomia (avvio al logon, riavvio automatico in caso di errore) è disponibile uno script che lo registra come Scheduled Task:

```powershell
# dalla root del progetto
powershell -ExecutionPolicy Bypass -File scripts\install-task.ps1

# rimozione
powershell -ExecutionPolicy Bypass -File scripts\uninstall-task.ps1
```

La task parte al logon dell'utente corrente, esegue `crawler\main.py` col Python del venv e viene riavviata (fino a 10 volte, a distanza di 1 minuto) se il processo esce con errore. Verifica lo stato con `Get-ScheduledTask TradingSignalsCrawler` e i log in `crawler\logs\crawler.log`.

## Formato dei file CSV

**`trading_signals.csv`** (scritto dal crawler, letto dall'EA):

```
timestamp, order_id, magic_number, message_type, asset, signal_type, entry, sl, tp, comment
2025-10-01 15:30:20, , 48231, placement, EURUSD, BUY LIMIT, 1.12500, 1.08500, 1.20000,
```

**`order_registry.csv`** (scritto dall'EA, letto dal crawler): registra per ogni ordine piazzato `timestamp, asset, signal_type, entry, magic, ticket`. L'EA lo aggiorna anche dopo una modifica del prezzo di ingresso, così le chiusure successive ritrovano l'ordine. Il crawler cerca gli ordini per asset, tipo di segnale e prezzo con una tolleranza di 2 pip (pip-aware: 0.01 per le coppie JPY, 0.0001 per le altre).

## Sviluppo

```bash
pip install -r requirements-dev.txt
ruff check .    # lint
pytest          # test unitari (parser dei messaggi e registro ordini)
```

I test coprono il riconoscimento dei 5 tipi di messaggio e la logica di lookup nel registro; girano anche in CI (GitHub Actions) su ogni push e pull request. L'Expert Advisor non è compilabile in CI: va compilato manualmente in MetaEditor (F7).

## Disclaimer

Questo progetto è a scopo personale/didattico. Il trading automatico comporta rischi finanziari significativi: usa l'EA su un conto demo prima di qualsiasi utilizzo reale.
