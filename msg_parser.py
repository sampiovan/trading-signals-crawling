import re


# ----- Funzione per analizzare i messaggi e estrarre i segnali con espressioni regolari -----
def parse_message(message_text):
	"""
	Funzione principale che prova a riconoscere il tipo di messaggio
	chiamando in sequenza le funzioni dedicate.
	"""
	parsers = [
		parse_order_placement,
		parse_order_open,
		parse_order_modify,
		parse_order_close,
		parse_order_cancel
	]
	
	for parser in parsers:
		result = parser(message_text)
		if result:
			return result
	
	return None


"""
Riconosce un messaggio di piazzamento ordine.
Esempio:
	"📈BUY LIMIT  EUR/USD
	 Prezzo 1.12500  (di apertura)
	 
	 Stop Loss   🔴 1.08500
	 
	 Take Profit  🟢  1.20000"
"""
def parse_order_placement(text):
	pattern = re.compile(
		r"(?i)^(?:\S+\s*)?(BUY LIMIT|SELL LIMIT|BUY STOP|SELL STOP|BUY|SELL)\s+([A-Z]{3}/[A-Z]{3}).*?Prezzo\s+([\d\.]+).*?(?:di\s+apertura).*?Stop Loss\s*[^\d]*([\d\.]+).*?Take Profit\s*[^\d]*([\d\.]+)",
		re.DOTALL
	)
	match = pattern.search(text)
	if match:
		return {
			'message_type': 'placement',
			'signal_type': match.group(1).upper(),
			'asset': match.group(2).upper().replace("/", ""),
			'entry': match.group(3),
			'sl': match.group(4),
			'tp': match.group(5),
			'extra': ''
		}
	
	return None


"""
Riconosce un messaggio di apertura ordine.
Esempio:
	"Ordine Buy  EUR/USD    Aperto 
	Prezzo di ingresso  1.12500"
"""
def parse_order_open(text):
	pattern = re.compile(
		r"(?i)Ordine\s+(BUY|SELL)\s+([A-Z]{3}/[A-Z]{3}).*?Aperto.*?Prezzo di ingresso\s+([\d\.]+)",
		re.DOTALL
	)
	match = pattern.search(text)
	if match:
		return {
			'message_type': 'open',
			'signal_type': match.group(1).upper(),
			'asset': match.group(2).upper().replace("/", ""),
			'entry': match.group(3),
			'sl': '',
			'tp': '',
			'extra': ''
		}
	
	return None


"""
Riconosce un messaggio di modifica ordine.
Esempio:
	"(BUY LIMIT EUR/USD) - MODIFICARE IL PREZZO DI INGRESSO DA 1.12500 A  1.13000  mantenendo uguale Stop loss e Take Profit 👍✅"
"""
def parse_order_modify(text):
	pattern = re.compile(
		r"(?i)\((BUY LIMIT|SELL LIMIT|BUY STOP|SELL STOP)\s+([A-Z]{3}/[A-Z]{3})\).*?MODIFICARE IL PREZZO DI INGRESSO DA\s+([\d\.]+)\s+A\s+([\d\.]+)",
		re.DOTALL
	)
	match = pattern.search(text)
	if match:
		return {
			'message_type': 'modify',
			'signal_type': match.group(1).upper(),
			'asset': match.group(2).upper().replace("/", ""),
			'entry': match.group(4),  # Nuovo prezzo
			'sl': '',
			'tp': '',
			'extra': f"Modifica: da {match.group(3)} a {match.group(4)}"
		}
	
	return None


"""
Riconosce un messaggio di chiusura ordine.
Esempio:
	"📊EUR/USD

	CHIUDERE MANUALMENTE UNA POSIZIONE IN PROFITTO SU EUR/USD  (1.12500)  ✅✅✅"
"""
def parse_order_close(text):
	pattern = re.compile(
		r"(?i)[\s\S]*([A-Z]{3}/[A-Z]{3}).*?CHIUDERE.*?\(([\d\.]+)\)",
		re.DOTALL
	)
	match = pattern.search(text)
	if match:
		return {
			'message_type': 'close',
			'asset': match.group(1).upper().replace("/", ""),
			'signal_type': '',  # Non specificato
			'entry': match.group(2),  # Prezzo di chiusura
			'sl': '',
			'tp': '',
			'extra': 'Chiusura manuale'
		}
	
	return None


"""
Riconosce un messaggio di annullamento ordine.
Esempio:
	"ANNULLARE BUY LIMIT EUR/USD ... (1.12500)✅"
"""
def parse_order_cancel(text):
	pattern = re.compile(
		r"(?i)ANNULLARE\s+(BUY LIMIT|SELL LIMIT|BUY STOP|SELL STOP)\s+([A-Z]{3}/[A-Z]{3}).*?\(([\d\.]+)",
		re.DOTALL
	)
	match = pattern.search(text)
	if match:
		return {
			'message_type': 'cancel',
			'signal_type': match.group(1).upper(),
			'asset': match.group(2).upper().replace("/", ""),
			'entry': match.group(3),  # Prezzo indicato, se utile
			'sl': '',
			'tp': '',
			'extra': 'Annullamento ordine'
		}

	return None