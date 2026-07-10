# Trading Signals Crawling

Sistema di copy-trading automatico composto da due componenti che comunicano tramite file CSV:

1. **Crawler Python** (`data_crawler_trading_signal/`) — si connette a un canale Telegram con [Telethon](https://docs.telethon.dev/), riconosce i messaggi contenenti segnali di trading e li scrive in un file CSV nella cartella `Files` di MetaTrader 4.
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

Il parser ([msg_parser.py](data_crawler_trading_signal/msg_parser.py)) riconosce cinque tipi di messaggio:

| Tipo (`message_type`) | Significato | Esempio di messaggio |
|---|---|---|
| `placement` | Piazzamento di un ordine (anche pendente) | `📈BUY LIMIT EUR/USD  Prezzo 1.12500 (di apertura) …` |
| `open` | Apertura a mercato di un ordine | `Ordine Buy EUR/USD Aperto  Prezzo di ingresso 1.12500` |
| `modify` | Modifica del prezzo di ingresso | `(BUY LIMIT EUR/USD) - MODIFICARE IL PREZZO DI INGRESSO DA … A …` |
| `close` | Chiusura manuale di una posizione | `CHIUDERE MANUALMENTE UNA POSIZIONE … (1.12500)` |
| `cancel` | Annullamento di un ordine pendente | `ANNULLARE BUY LIMIT EUR/USD … (1.12500)` |

Al momento del `placement` il crawler genera un **magic number** ([utils.py](data_crawler_trading_signal/utils.py)) che identifica il segnale. L'EA, dopo aver piazzato l'ordine, scrive in `order_registry.csv` la coppia magic number ↔ ticket del broker. Per i messaggi successivi (`open`, `modify`, `close`, `cancel`) il crawler consulta il registro ([order_registry_manager.py](data_crawler_trading_signal/order_registry_manager.py)) e recupera il ticket confrontando asset e prezzo di ingresso.

## Struttura della repository

```
.
├── data_crawler_trading_signal/   # Package Python del crawler
│   ├── main.py                    # Entry point: client Telegram, logging, scrittura CSV
│   ├── msg_parser.py              # Riconoscimento dei messaggi via regex
│   ├── order_registry_manager.py  # Lettura di order_registry.csv e lookup dei ticket
│   └── utils.py                   # Generazione del magic number
├── mt4/
│   └── Crawler_Trading_Signal.mq4 # Expert Advisor per MetaTrader 4
├── tests/                         # Test (in preparazione)
├── config.example.ini             # Template di configurazione
├── requirements.txt               # Dipendenze Python
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
| `LOT_SIZE` | `0.01` | Dimensione del lotto per gli ordini |
| `TIMER_SECONDS` | `10` | Intervallo di lettura del CSV |

## Utilizzo

Avvia il crawler dalla cartella del package (il `config.ini` deve trovarsi nella directory di lavoro):

```bash
cd data_crawler_trading_signal
python main.py
```

Al primo avvio Telethon chiede il numero di telefono e il codice di verifica, poi salva la sessione nel file `<SESSION_NAME>.session` e i login successivi sono automatici.

Il crawler resta in ascolto dei nuovi messaggi del canale e logga l'attività sia su console sia in `logs/crawler.log` (rotazione giornaliera a mezzanotte, 30 giorni di retention).

## Formato dei file CSV

**`trading_signals.csv`** (scritto dal crawler, letto dall'EA):

```
timestamp, order_id, magic_number, message_type, asset, signal_type, entry, sl, tp, comment
2025-10-01 15:30:20, , 48231, placement, EURUSD, BUY LIMIT, 1.12500, 1.08500, 1.20000,
```

**`order_registry.csv`** (scritto dall'EA, letto dal crawler): registra per ogni ordine piazzato `timestamp, asset, signal_type, entry, magic, ticket`.

## Disclaimer

Questo progetto è a scopo personale/didattico. Il trading automatico comporta rischi finanziari significativi: usa l'EA su un conto demo prima di qualsiasi utilizzo reale.
