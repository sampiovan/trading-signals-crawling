//+------------------------------------------------------------------+
//|                                       Crawler_Trading_Signal.mq4 |
//|                                  Copyright 2025, MetaQuotes Ltd. |
//|                                                                  |
//+------------------------------------------------------------------+
#property copyright ""  //"Copyright 2025, MetaQuotes Ltd."
#property link      ""
#property version   "1.00"
#property strict

//====================================================================
//  EA che legge un CSV periodicamente e apre/gestisce ordini in base
//  ai segnali. Usa OnTimer per controllare periodicamente il file.
//====================================================================

/*  Struttura del CSV (esempio):
    timestamp, order_id, magic_number, message_type, asset, signal_type, entry, sl, tp, comment
    2023-10-01 15:30:20,placement,CAD/CHF,BUY LIMIT,0.63600,0.58000,0.68000,
    2023-10-01 15:35:00,open,CAD/CHF,BUY,0.63500,,,
    ...
*/

//----- Input: Nome del file e parametri di default ------------------
sinput string   CSV_FILENAME   = "trading_signals.csv"; // deve stare in MQL4/Files/
sinput double   LOT_SIZE       = 0.01;                  // lotto di default
sinput int      TIMER_SECONDS  = 10;                    // ogni quanti secondi controllare il CSV

//----- Nome del file per il registro degli ordini
#define ORDER_REGISTRY_FILENAME "order_registry.csv"

//----- Nome del file di stato (righe del CSV già processate)
#define STATE_FILENAME "crawler_ea_state.txt"

//----- Variabili globali / statiche ---------------------------------
// Numero di righe di segnale (header esclusa) già processate.
// Persistito su STATE_FILENAME per sopravvivere ai riavvii dell'EA:
// senza persistenza, a ogni riavvio il CSV verrebbe rieseguito
// dall'inizio, duplicando tutti gli ordini.
int g_processedLines = 0;

// Definizione della struttura dei segnali
struct SignalInfo {
   string timestamp;      // "2023-10-01 15:30:20"
   string order_id;       // #453892372
   string magic_number;   // 12345
   string message_type;   // "placement", "open", "modify", "close", "cancel"
   string asset;          // "CADCHF"
   string signal_type;    // "BUY LIMIT", "SELL STOP", "BUY", "SELL", ...
   double entry;          // prezzo di entrata
   double sl;             // stop loss
   double tp;             // take profit
   string comment;        // comment
};

//--------------------------------------------------------------------
// Funzioni ausiliarie per la manipolazione delle stringhe
//--------------------------------------------------------------------

// Funzione per rimuovere spazi bianchi, tab e newline da una stringa
string MyStringTrim(const string s) {
   int len = StringLen(s);
   int start = 0;
   int end = len - 1;
   
   // Rimuove spazi iniziali
   while(start < len)
   {
      int c = StringGetCharacter(s, start);
      if(c != 32 && c != 9 && c != 10 && c != 13) // 32=spazio, 9=tab, 10=\n, 13=\r
         break;
      start++;
   }
   
   // Rimuove spazi finali
   while(end >= start)
   {
      int c = StringGetCharacter(s, end);
      if(c != 32 && c != 9 && c != 10 && c != 13)
         break;
      end--;
   }
   
   if(start > end)
      return "";
   return StringSubstr(s, start, end - start + 1);
}

// Funzione per convertire una stringa in maiuscolo
string MyStringUpper(string s) {
   string result = "";
   int len = StringLen(s);
   for(int i = 0; i < len; i++)
   {
      // Otteniamo il codice del carattere
      int c = StringGetCharacter(s, i);
      // Se il carattere è minuscolo (ASCII 97-'a' a 122-'z'), convertiamolo in maiuscolo
      if(c >= 97 && c <= 122)
         c = c - 32;
      // Converte il codice in stringa e lo concatenamo al risultato
      result = result + CharToString(c);
   }
   return result;
}


//--------------------------------------------------------------------
// Funzione per convertire una stringa timestamp ("YYYY-MM-DD HH:MM:SS") in datetime
//--------------------------------------------------------------------
datetime ConvertToDateTime(string datetimeStr) {
   // Sostituisce tutti i "-" con "."
   StringReplace(datetimeStr, "-", ".");

   return StrToTime(datetimeStr);
}


//--------------------------------------------------------------------
// Funzione per parsare una riga del CSV dei segnali
//--------------------------------------------------------------------
bool ParseCsvLine(string line, SignalInfo &info) {
   // Dividiamo la riga in 10 colonne
   string parts[];
   int count = StringSplit(line, ',', parts);
   
   if(count < 10)
      return false;
   info.timestamp     = MyStringTrim(parts[0]);
   info.order_id      = MyStringTrim(parts[1]);
   info.magic_number  = MyStringTrim(parts[2]);
   info.message_type  = MyStringTrim(parts[3]);
   info.asset         = MyStringTrim(parts[4]);
   info.signal_type   = MyStringTrim(parts[5]);
   info.entry         = StrToDouble(MyStringTrim(parts[6]));
   info.sl            = StrToDouble(MyStringTrim(parts[7]));
   info.tp            = StrToDouble(MyStringTrim(parts[8]));
   info.comment       = MyStringTrim(parts[9]);
   return true;
}


//------------------------------------------------------------------
// Funzione per scrivere il ticket dell'ordine in un file CSV di registro
//------------------------------------------------------------------
void LogOrderTicket(int ticket, const SignalInfo &info) {
   // FILE_READ|FILE_WRITE: apre senza troncare. FILE_WRITE da solo azzererebbe
   // il file a ogni ordine, perdendo tutte le righe del registro precedenti.
   int handle = FileOpen(ORDER_REGISTRY_FILENAME, FILE_CSV | FILE_READ | FILE_WRITE | FILE_ANSI);
   if(handle < 0) {
      Print("Errore nell'aprire il file ", ORDER_REGISTRY_FILENAME, ": ", GetLastError());
      return;
   }
   
   // Verifica se il file è nuovo (dimensione 0) per scrivere l'header
   bool new_file = false;
   if(FileSize(handle) == 0)
      new_file = true;
      
   if(new_file) {
      // Scrivi l'header: timestamp, asset, signal_type, entry, magic, ticket
      string header = "timestamp,asset,signal_type,entry,magic,ticket";
      FileWriteString(handle, header + "\n");
   }
   
   // Posiziona il puntatore alla fine del file
   FileSeek(handle, 0, SEEK_END);
   
   // Formatta il timestamp attuale
   string timeStr = TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS);
   
   // Costruisci la riga da scrivere, includendo anche il prezzo d'entrata (entry) e il magic number
   string line = timeStr + "," + info.asset + "," + info.signal_type + "," +
                 DoubleToString(info.entry, 5) + "," + info.magic_number + "," + IntegerToString(ticket);
   
   FileWriteString(handle, line + "\n");
   FileClose(handle);
   Print("Registrato ticket nel file: ", ticket);
}



//------------------------------------------------------------------
// Funzioni per l'esecuzione degli ordini
//------------------------------------------------------------------

// Esempio: piazzamento di un ordine "placement"
void DoPlacement(const SignalInfo &info) {
   // Distinzione tra BUY LIMIT, SELL LIMIT, BUY STOP, SELL STOP, BUY, SELL
   int cmd;
   string st = MyStringUpper(MyStringTrim(info.signal_type));
   
   if(st=="BUY LIMIT")       cmd = OP_BUYLIMIT;
   else if(st=="SELL LIMIT") cmd = OP_SELLLIMIT;
   else if(st=="BUY STOP")   cmd = OP_BUYSTOP;
   else if(st=="SELL STOP")  cmd = OP_SELLSTOP;
   else if(st=="BUY")        cmd = OP_BUY;
   else if(st=="SELL")       cmd = OP_SELL;
   else {
      Print("Tipo di segnale non riconosciuto: ", info.signal_type);
      return;
   }

   // Per ordini pendenti (BUY LIMIT, SELL LIMIT, BUY STOP, SELL STOP) serve 'price'.
   double price = info.entry;
   // Per ordini a mercato (BUY/SELL) 'price' si ignora e si mette 0.0
   if(cmd==OP_BUY || cmd==OP_SELL)
      price = 0.0;

   int ticket = OrderSend(
      info.asset,   // Symbol
      cmd,          // Operation
      LOT_SIZE,     // Volume
      price,        // Price (0 se esecuzione a mercato)
      3,            // Slippage
      info.sl,      // StopLoss
      info.tp,      // TakeProfit
      "Placement", // Comment
      0,            // Magic number
      0,            // Expiration
      clrBlue       // Arrow color
   );
   if(ticket < 0)
      Print("Errore OrderSend placement: ", GetLastError());
   else {
      Print("Ordine inviato con successo, ticket=", ticket);
      LogOrderTicket(ticket, info);
   }
}

// Esempio: "open" => potremmo trattarlo come un ordine a mercato
void DoOpen(const SignalInfo &info) {
   // Se info.signal_type == "BUY" => OP_BUY
   // Se info.signal_type == "SELL" => OP_SELL
   int cmd = -1;
   string st = MyStringUpper(info.signal_type);
   if(st=="BUY")  cmd = OP_BUY;
   if(st=="SELL") cmd = OP_SELL;

   if(cmd<0){
      Print("DoOpen: segnale non riconosciuto: ", info.signal_type);
      return;
   }

   int ticket = OrderSend(
      info.asset,
      cmd,
      LOT_SIZE,
      0.0,         // a mercato
      3,
      info.sl,
      info.tp,
      "EA CSV Trader - Open",
      0,0,clrBlue
   );
   if(ticket<0)
      Print("Errore OrderSend open: ", GetLastError());
   else
      Print("Ordine APERTO, ticket=", ticket);
}


// Parametri modificabili in un ordine:
// - Per ordini a mercato (OP_BUY, OP_SELL): Stop Loss e Take Profit.
// - Per ordini pendenti (OP_BUYLIMIT, OP_SELLLIMIT, OP_BUYSTOP, OP_SELLSTOP):
//   il prezzo (entry), Stop Loss, Take Profit e l'expiration (se necessario).
// Non è possibile modificare: il simbolo, il volume, il tipo d'ordine, il Magic Number, il commento.
void DoModify(const SignalInfo &info) {
   // Converte il ticket (info.order_id) in intero
   int ticket = StrToInteger(info.order_id);
   
   // Seleziona l'ordine tramite il ticket
   if(!OrderSelect(ticket, SELECT_BY_TICKET, MODE_TRADES)) {
      Print("Ordine con ticket ", info.order_id, " non selezionato: ", GetLastError());
      return;
   }
   
   // Determina il nuovo prezzo:
   // - Per ordini pendenti, si modifica il prezzo (usando il valore info.entry).
   // - Per ordini a mercato, il prezzo non viene modificato (si usa l'OrderOpenPrice()).
   double newPrice;
   int orderType = OrderType();
   if(orderType == OP_BUYLIMIT || orderType == OP_SELLLIMIT ||
      orderType == OP_BUYSTOP  || orderType == OP_SELLSTOP)
      newPrice = StrToDouble(info.entry);
   else
      newPrice = OrderOpenPrice();  // Per ordini a mercato, il prezzo non è modificabile
   
   // Modifica dello Stop Loss e del Take Profit:
   double newSL = (info.sl > 0) ? info.sl : OrderStopLoss();
   double newTP = (info.tp > 0) ? info.tp : OrderTakeProfit();
   
   // Per ordini pendenti potresti voler gestire anche l'expiration; qui lo impostiamo a 0 (nessuna scadenza)
   datetime expiration = 0;
   
   bool ok = OrderModify(ticket, newPrice, newSL, newTP, expiration, clrBlue);
   if(!ok)
      Print("Errore OrderModify per ticket ", info.order_id, ": ", GetLastError());
   else
      Print("Ordine modificato con successo, ticket=", info.order_id);
}


// Chiude un ordine a mercato utilizzando direttamente
// il ticket fornito in info.order_id
void DoClose(const SignalInfo &info) {
   // Converte il ticket (info.order_id) in intero
   int ticket = StrToInteger(info.order_id);
   
   // Seleziona l'ordine tramite il ticket
   if(!OrderSelect(ticket, SELECT_BY_TICKET, MODE_TRADES)) {
      Print("Errore: ordine con ticket ", info.order_id, " non selezionato: ", GetLastError());
      return;
   }
   
   // Verifica che l'ordine sia un ordine a mercato (OP_BUY o OP_SELL)
   int orderType = OrderType();
   if(orderType > OP_SELL) {
      Print("Errore: l'ordine con ticket ", info.order_id, " non è un ordine a mercato e non può essere chiuso.");
      return;
   }
   
   double lots = OrderLots();
   double price;
   // Per ordini BUY si chiude con il Bid, per ordini SELL con l'Ask
   if(orderType == OP_BUY)
      price = MarketInfo(info.asset, MODE_BID);
   else
      price = MarketInfo(info.asset, MODE_ASK);
   
   // Il parametro "3" in OrderClose() indica lo slippage massimo in punti
   // accettabile per la chiusura dell'ordine.
   bool ok = OrderClose(ticket, lots, price, 3, clrBlue);
   if(!ok)
      Print("Errore OrderClose per ticket ", info.order_id, ": ", GetLastError());
   else
      Print("Ordine chiuso con successo, ticket=", info.order_id);
}


// Cancella un ordine pendente usando il ticket presente in info.order_id
void DoCancel(const SignalInfo &info) {
   // Converte l'order_id (info.order_id) in intero
   int ticket = StrToInteger(info.order_id);
   
   // Seleziona l'ordine tramite il ticket
   if(!OrderSelect(ticket, SELECT_BY_TICKET, MODE_TRADES)) {
      Print("Errore: ordine con ticket ", info.order_id, " non selezionato: ", GetLastError());
      return;
   }
   
   // Verifica che l'ordine sia un ordine pendente (OP_BUYLIMIT, OP_SELLLIMIT, OP_BUYSTOP, OP_SELLSTOP)
   int orderType = OrderType();
   if(orderType < OP_BUYLIMIT) {
      Print("Errore: l'ordine con ticket ", info.order_id, " non è un ordine pendente e non può essere cancellato.");
      return;
   }
   
   // Cancella l'ordine pendente
   if(!OrderDelete(ticket))
      Print("Errore OrderDelete per ticket ", info.order_id, ": ", GetLastError());
   else
      Print("Ordine pendente cancellato con successo, ticket=", info.order_id);
}


//--------------------------------------------------------------------
// Funzione per gestire il segnale in base al message_type
//--------------------------------------------------------------------
void HandleSignal(const SignalInfo &info) {
   // Logica di dispatch
   if(info.message_type=="placement")  DoPlacement(info);
   else if(info.message_type=="open")  DoOpen(info);
   else if(info.message_type=="modify") DoModify(info);
   else if(info.message_type=="close")  DoClose(info);
   else if(info.message_type=="cancel") DoCancel(info);
   else Print("Tipo di messaggio non gestito: ", info.message_type);
}


//------------------------------------------------------------------
// Persistenza dello stato: numero di righe di segnale già processate
//------------------------------------------------------------------
int LoadProcessedLines() {
   int handle = FileOpen(STATE_FILENAME, FILE_READ|FILE_TXT|FILE_ANSI);
   if(handle < 0)
      return 0; // primo avvio: nessuno stato salvato
   string content = FileReadString(handle);
   FileClose(handle);
   int value = StrToInteger(MyStringTrim(content));
   if(value < 0)
      value = 0;
   return value;
}

void SaveProcessedLines(int count) {
   int handle = FileOpen(STATE_FILENAME, FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(handle < 0) {
      Print("Errore nel salvare lo stato su ", STATE_FILENAME, ": ", GetLastError());
      return;
   }
   FileWriteString(handle, IntegerToString(count));
   FileClose(handle);
}


//------------------------------------------------------------------
// Funzione per leggere il CSV e processare i segnali
//------------------------------------------------------------------
void ProcessCsv() {
   // Apriamo il file in lettura
   int fileHandle = FileOpen(CSV_FILENAME, FILE_CSV|FILE_READ|FILE_ANSI);
   if(fileHandle < 0)
   {
      Print("Impossibile aprire il file CSV: ", CSV_FILENAME);
      return;
   }

   // Carichiamo tutte le righe non vuote in memoria: serve conoscere il
   // totale PRIMA di processare, per rilevare un file ricreato/ruotato.
   string lines[];
   int total = 0;
   while(!FileIsEnding(fileHandle))
   {
      string line = FileReadString(fileHandle);
      if(StringLen(line) < 2)
         continue; // evitiamo righe vuote
      ArrayResize(lines, total + 1);
      lines[total] = line;
      total++;
   }
   FileClose(fileHandle);

   if(total == 0)
      return;

   // lines[0] è l'header: le righe di segnale sono total-1
   int signalCount = total - 1;

   // File con meno righe dello stato salvato: il CSV è stato ricreato o
   // ruotato manualmente. Ripartiamo da zero (le righe presenti sono nuove).
   if(signalCount < g_processedLines) {
      Print("ATTENZIONE: ", CSV_FILENAME, " ha ", signalCount,
            " righe ma lo stato indica ", g_processedLines,
            " già processate: file ricreato/ruotato, riparto da zero.");
      g_processedLines = 0;
      SaveProcessedLines(g_processedLines);
   }

   // Processa solo le righe successive all'ultima già gestita.
   // Il confronto per posizione (e non per timestamp) evita di perdere
   // segnali emessi nello stesso secondo.
   for(int i = 1 + g_processedLines; i < total; i++)
   {
      SignalInfo info;
      bool parsed = ParseCsvLine(lines[i], info) && ConvertToDateTime(info.timestamp) != 0;

      if(!parsed) {
         // Se è l'ULTIMA riga potrebbe essere una scrittura parziale del
         // crawler ancora in corso: non avanzare, riprova al prossimo timer.
         if(i == total - 1)
            return;
         Print("Riga malformata saltata: ", lines[i]);
         g_processedLines = i;
         SaveProcessedLines(g_processedLines);
         continue;
      }

      HandleSignal(info);
      g_processedLines = i;
      SaveProcessedLines(g_processedLines);
   }
}


//+------------------------------------------------------------------+
//| Expert initialization function: settiamo un timer                |
//+------------------------------------------------------------------+
int OnInit() {
   Print("EA Crawler_Trading_Signal avviato");
   g_processedLines = LoadProcessedLines();
   Print("Stato caricato: ", g_processedLines, " righe già processate");
   EventSetTimer(TIMER_SECONDS);
   return(INIT_SUCCEEDED);
}


//+------------------------------------------------------------------+
//| Expert deinitialization function: kill timer                     |
//+------------------------------------------------------------------+
void OnDeinit(const int reason) {
   EventKillTimer();
   Print("EA Crawler_Trading_Signal terminato");
}


//+------------------------------------------------------------------+
//| Expert timer function: leggiamo il CSV e gestiamo i nuovi segnali|
//+------------------------------------------------------------------+
void OnTimer() {
   ProcessCsv();
}

//+------------------------------------------------------------------+
//| Expert tick function:                                            |
//+------------------------------------------------------------------+
void OnTick() {
   // Mostra un messaggio sul grafico che indica che l'EA è attivo
   string status = "EA Crawler Trading Signal Attivo\n";
   status += "Segnali processati: ";
   if(g_processedLines > 0)
      status += IntegerToString(g_processedLines);
   else
      status += "nessuno";

   Comment(status);
}
//+------------------------------------------------------------------+
