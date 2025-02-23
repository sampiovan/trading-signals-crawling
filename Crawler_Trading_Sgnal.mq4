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
    timestamp, message_type, asset, signal_type, entry, sl, tp, extra
    2023-10-01 15:30:20,placement,CAD/CHF,BUY LIMIT,0.63600,0.58000,0.68000,
    2023-10-01 15:35:00,open,CAD/CHF,BUY,0.63500,,,
    ...
*/

//------------ Input: Nome del file e parametri di default -----------
sinput string   CSV_FILENAME   = "trading_signals.csv"; // deve stare in MQL4/Files/
sinput double   LOT_SIZE       = 0.01;                  // lotto di default
sinput int      TIMER_SECONDS  = 30;                    // ogni quanti secondi controllare il CSV

//------------ Variabili globali / statiche --------------------------
datetime g_lastProcessed = 0; // memorizza l'ultima data/ora processata con successo


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


//------------ Struttura per i segnali -------------------------------
struct SignalInfo {
   string timestamp;      // "2023-10-01 15:30:20"
   string message_type;   // "placement", "open", "modify", "close", "cancel"
   string asset;          // "CADCHF"
   string signal_type;    // "BUY LIMIT", "SELL STOP", "BUY", "SELL", ...
   double entry;          // prezzo di entrata
   double sl;             // stop loss
   double tp;             // take profit
   string extra;          // campi extra
};

//------------ Funzione di parsing di una singola riga CSV -----------
bool ParseCsvLine(string line, SignalInfo &info) {
   // Dividiamo la riga in 8 colonne
   string parts[];
   int count = StringSplit(line, ',', parts);
   if(count < 8)
      return false;
   
   info.timestamp     = MyStringTrim(parts[0]);
   info.message_type  = MyStringTrim(parts[1]);
   info.asset         = MyStringTrim(parts[2]);
   info.signal_type   = MyStringTrim(parts[3]);
   info.entry         = StrToDouble(MyStringTrim(parts[4]));
   info.sl            = StrToDouble(MyStringTrim(parts[5]));
   info.tp            = StrToDouble(MyStringTrim(parts[6]));
   info.extra         = MyStringTrim(parts[7]);
   return true;
}

//------------ Converte "2023-10-01 15:30:20" in datetime ------------
datetime ConvertToDateTime(string datetimeStr) {
   // Esempio di datetimeStr: "2023-10-01 15:30:20"
   // MQL4 non ha un parser built-in molto flessibile, quindi lo facciamo manualmente
   // Formato: YYYY-MM-DD HH:MM:SS
   string datePart = StringSubstr(datetimeStr, 0, 10);  // "2023-10-01"
   string timePart = StringSubstr(datetimeStr, 11, 8); // "15:30:20"

   // Splittiamo la parte data
   string dateElems[];
   StringSplit(datePart, '-', dateElems);
   if(ArraySize(dateElems) < 3) return 0;

   int yyyy = StrToInteger(dateElems[0]);
   int mm   = StrToInteger(dateElems[1]);
   int dd   = StrToInteger(dateElems[2]);

   // Splittiamo la parte tempo
   string timeElems[];
   StringSplit(timePart, ':', timeElems);
   if(ArraySize(timeElems) < 3) return 0;

   int HH = StrToInteger(timeElems[0]);
   int MI = StrToInteger(timeElems[1]);
   int SS = StrToInteger(timeElems[2]);

   return StrToTime(StringFormat("%04d.%02d.%02d %02d:%02d:%02d", yyyy, mm, dd, HH, MI, SS));
}

//-------------- Gestione dei vari tipi di segnali ---------------------

// Esempio: piazzamento di un ordine "placement"
void DoPlacement(const SignalInfo &info) {
   // Distinzione tra BUY LIMIT, SELL LIMIT, BUY STOP, SELL STOP, BUY, SELL
   int cmd;
   double price = info.entry;

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

   // Esempio di invio ordine
   // Per ordini a mercato (BUY/SELL) 'price' si ignora e si mette 0.0
   // Per ordini pendenti (BUY LIMIT, SELL LIMIT, BUY STOP, SELL STOP) serve 'price'.
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
      "EA CSV Trader - Placement", // Comment
      0,            // Magic number
      0,            // Expiration
      clrBlue       // Arrow color
   );
   if(ticket<0)
      Print("Errore OrderSend placement: ", GetLastError());
   else
      Print("Ordine inviato con successo, ticket=", ticket);
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

// Esempio: "modify" => trovo l'ordine esistente e ne modifico prezzo/SL/TP
void DoModify(const SignalInfo &info) {
   // In un caso reale dovresti identificare l'ordine da modificare,
   // ad esempio scorrendo OrdersTotal() e cercando un commento o un MagicNumber
   // o in base al symbol e al cmd. Qui facciamo un esempio semplificato.

   bool found = false;
   for(int i=OrdersTotal()-1; i>=0; i--)
   {
      if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
      {
         if(OrderSymbol()==info.asset)
         {
            // Esempio: modifichiamo lo SL/TP
            double price = OrderOpenPrice();
            double sl = (info.sl>0) ? info.sl : OrderStopLoss();
            double tp = (info.tp>0) ? info.tp : OrderTakeProfit();

            bool ok = OrderModify(OrderTicket(), price, sl, tp, 0, clrBlue);
            if(!ok)
               Print("Errore OrderModify: ", GetLastError());
            else
               Print("Ordine modificato con successo, ticket=", OrderTicket());
            found = true;
            break;
         }
      }
   }
   if(!found)
      Print("Nessun ordine da modificare trovato per ", info.asset);
}

// Esempio: "close" => chiudere un ordine a mercato su un determinato asset
void DoClose(const SignalInfo &info) {
   bool found = false;
   for(int i=OrdersTotal()-1; i>=0; i--)
   {
      if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
      {
         if(OrderSymbol()==info.asset && OrderType()<=OP_SELL) // buy or sell
         {
            double lots = OrderLots();
            double bid = MarketInfo(info.asset, MODE_BID);
            double ask = MarketInfo(info.asset, MODE_ASK);
            double price = (OrderType()==OP_BUY) ? bid : ask;

            bool ok = OrderClose(OrderTicket(), lots, price, 3, clrBlue);
            if(!ok)
               Print("Errore OrderClose: ", GetLastError());
            else
               Print("Ordine chiuso, ticket=", OrderTicket());
            found = true;
         }
      }
   }
   if(!found)
      Print("Nessun ordine da chiudere trovato per ", info.asset);
}

// Esempio: "cancel" => annullare un ordine pendente
void DoCancel(const SignalInfo &info) {
   bool found = false;
   for(int i=OrdersTotal()-1; i>=0; i--)
   {
      if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
      {
         if(OrderSymbol()==info.asset && OrderType()>=OP_BUYLIMIT)
         {
            // Significa che è un pending order
            bool ok = OrderDelete(OrderTicket());
            if(!ok)
               Print("Errore OrderDelete: ", GetLastError());
            else
               Print("Ordine pendente cancellato, ticket=", OrderTicket());
            found = true;
         }
      }
   }
   if(!found)
      Print("Nessun ordine pendente da cancellare trovato per ", info.asset);
}

//-------------- Esegue l'azione in base al message_type ----------------
void HandleSignal(const SignalInfo &info) {
   // Esempio di logica di dispatch
   if(info.message_type=="placement")  DoPlacement(info);
   else if(info.message_type=="open")  DoOpen(info);
   else if(info.message_type=="modify") DoModify(info);
   else if(info.message_type=="close")  DoClose(info);
   else if(info.message_type=="cancel") DoCancel(info);
   else Print("Tipo di messaggio non gestito: ", info.message_type);
}

//+------------------------------------------------------------------+
//| Expert initialization function: settiamo un timer                |
//+------------------------------------------------------------------+
int OnInit() {
   Print("EA Crawler_Trading_Signal avviato");
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
   // Apriamo il file in lettura
   int fileHandle = FileOpen(CSV_FILENAME, FILE_CSV|FILE_READ|FILE_ANSI);
   if(fileHandle<0)
   {
      Print("Impossibile aprire il file CSV: ", CSV_FILENAME);
      return;
   }

   // Leggiamo riga per riga
   while(!FileIsEnding(fileHandle))
   {
      string line = FileReadString(fileHandle);
      // Evitiamo righe vuote
      if(StringLen(line)<2) 
         continue;
   
      // Parsea la riga
      SignalInfo info;
      if(!ParseCsvLine(line, info))
         continue; // skip riga mal formattata

      // Confrontiamo il timestamp
      datetime dt = ConvertToDateTime(info.timestamp);
      if(dt==0) 
         continue; // formattazione timestamp errata

      // Se il timestamp è maggiore dell'ultimo processato, gestiamo il segnale
      if(dt>g_lastProcessed)
      {
         HandleSignal(info);
         g_lastProcessed = dt;
      }
   }
   FileClose(fileHandle);
}

//+------------------------------------------------------------------+
//| Expert tick function:                                            |
//+------------------------------------------------------------------+
void OnTick() {
   // Mostra un messaggio sul grafico che indica che l'EA è attivo
   string status = "EA Crawler Trading Signal Attivo\n";
   status += "Ultimo segnale processato: ";
   if(g_lastProcessed > 0)
      status += TimeToString(g_lastProcessed, TIME_DATE|TIME_MINUTES);
   else
      status += "Nessun segnale ancora processato";

   Comment(status);
}
//+------------------------------------------------------------------+
