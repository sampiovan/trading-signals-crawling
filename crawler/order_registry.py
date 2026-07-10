import os
import csv
import logging

from config import load_config

logger = logging.getLogger(__name__)


# Variabile globale per il registro degli ordini (indicizzato per "magic")
ORDER_REGISTRY = {}

# Nome del file CSV di registro scritto dall'EA
ORDER_REGISTRY_FILENAME = 'order_registry.csv'


def get_registry_path():
    """Costruisce il percorso di order_registry.csv nella cartella Files di MT4."""
    config = load_config()
    return os.path.join(config['paths']['MT4_FILES_FOLDER'], ORDER_REGISTRY_FILENAME)


def load_order_registry():
    """
    Carica il file CSV order_registry.csv e aggiorna il dizionario globale
    in cui la chiave è il magic number e il valore è un record (dict)
    con i dati: timestamp, asset, signal_type, entry, magic, ticket.
    """
    global ORDER_REGISTRY
    registry = {}
    registry_path = get_registry_path()

    if not os.path.exists(registry_path):
        logger.warning(f"File order_registry.csv non trovato in: {registry_path}")
        ORDER_REGISTRY = registry
        return registry
    try:
        with open(registry_path, 'r', newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                magic = row.get('magic', '').strip()
                if magic:
                    registry[magic] = row
    except Exception:
        logger.exception("Errore nel caricare order_registry.csv")

    ORDER_REGISTRY = registry
    return registry


def pip_size(asset):
    """
    Dimensione del pip per l'asset: 0.01 per le coppie quotate in JPY,
    0.0001 per tutte le altre. Una tolleranza assoluta unica sarebbe
    troppo stretta sulle coppie JPY (che quotano ~150) e troppo larga
    su altre.
    """
    if asset.strip().upper().endswith('JPY'):
        return 0.01
    return 0.0001


def get_order_ticket(asset, entry, signal_type, tol_pips=2):
    """
    Cerca nel registro globale l'ordine che meglio corrisponde ai valori
    del segnale: stesso asset, stesso signal_type (se indicato nel
    segnale) ed entry entro tol_pips pip. Tra i candidati viene scelto
    quello con l'entry più vicina, non il primo trovato.
    Restituisce la tupla (order_id, magic_number), o (None, None).
    """
    global ORDER_REGISTRY

    target_asset = asset.strip().upper()
    target_signal = signal_type.strip().upper()

    try:
        target_entry = float(entry)
    except Exception:
        target_entry = 0.0

    tol = tol_pips * pip_size(target_asset)

    best_record = None
    best_distance = None
    for magic, record in ORDER_REGISTRY.items():
        record_asset = record.get('asset', '').strip().upper()
        record_signal = record.get('signal_type', '').strip().upper()

        try:
            record_entry = float(record.get('entry', 0))
        except Exception:
            record_entry = 0.0

        if record_asset != target_asset:
            continue
        # I messaggi di chiusura non indicano il tipo: confronta solo se presente
        if target_signal and record_signal != target_signal:
            continue

        distance = abs(record_entry - target_entry)
        if distance < tol and (best_distance is None or distance < best_distance):
            best_record = record
            best_distance = distance

    if best_record:
        order_id = best_record.get('ticket', '')
        magic_number = best_record.get('magic', '')
        logger.info(f"Trovato ordine: asset {target_asset}, entry {target_entry}, ticket {order_id}, magic {magic_number}")
        return order_id, magic_number
    return None, None
