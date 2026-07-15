"""
Commenti degli ordini come data-carrier del segnale.

Il commento di ogni ordine/posizione aperto dal crawler è il prezzo di
apertura INVIATO DAL CANALE, arrotondato al pip: "@1.3390" (non-JPY,
4 decimali) o "@145.50" (JPY, 2 decimali). Dopo un taglio della guardia
posizioni, il commento accumula anche la perdita realizzata: "@1.3390 (-120)".

Il prezzo nel commento NON cambia mai al fill (che può avvenire a un
livello diverso) né alla chiusura: è l'identificatore stabile del segnale,
usato dal lookup per ritrovare le posizioni anche dopo una riapertura.

Nota: MT5 limita il commento a ~31 caratteri; questi formati restano
ampiamente sotto il limite.
"""
import re
import logging

logger = logging.getLogger(__name__)

# "@1.3390" oppure "@1.3390 (-120)"
_COMMENT_RE = re.compile(r"^@([\d\.]+)(?:\s*\((-\d+)\))?")


def format_price_comment(asset, entry):
	"""
	Formatta il prezzo del canale come commento "@X.XXXX" (o "@X.XX" per
	le coppie JPY), arrotondato al pip.
	"""
	decimals = 2 if asset.strip().upper().endswith('JPY') else 4
	return f"@{float(entry):.{decimals}f}"


def format_loss_comment(price_str, cum_loss):
	"""
	Commento con perdita realizzata cumulata (interi): "@1.3390 (-120)".
	price_str è il prezzo già formattato (senza '@').
	"""
	return f"@{price_str} (-{int(cum_loss)})"


def parse_comment(comment):
	"""
	Estrae (price_str, perdita_cumulata) da un commento del crawler.
	"@1.3390" -> ("1.3390", 0); "@1.3390 (-120)" -> ("1.3390", 120).
	Restituisce None se il commento non è nel nostro formato (posizioni
	manuali o di altri sistemi).
	"""
	if not comment:
		return None
	match = _COMMENT_RE.match(comment.strip())
	if not match:
		return None
	price_str = match.group(1)
	cum_loss = -int(match.group(2)) if match.group(2) else 0
	return price_str, cum_loss
