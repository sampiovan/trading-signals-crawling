import os
import csv

from config import load_config


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
        print("File order_registry.csv non trovato in:", registry_path)
        ORDER_REGISTRY = registry
        return registry
    try:
        with open(registry_path, 'r', newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                magic = row.get('magic', '').strip()
                if magic:
                    registry[magic] = row
    except Exception as e:
        print("Errore nel caricare order_registry.csv:", e)

    ORDER_REGISTRY = registry
    return registry


def get_order_ticket(asset, entry, signal_type, tol=0.0002):
    """
    Cerca nel registro globale un ordine che corrisponda ai valori indicati nel segnale,
    usando le chiavi specificate (asset, entry e signal_type).
    Se viene trovato un record, restituisce la tupla (order_id, magic_number).
    """
    global ORDER_REGISTRY

    target_asset = asset.strip().upper()
    target_signal = signal_type.strip().upper()

    try:
        target_entry = float(entry)
    except Exception:
        target_entry = 0.0

    for magic, record in ORDER_REGISTRY.items():
        record_asset = record.get('asset', '').strip().upper()
        record_signal = record.get('signal_type', '').strip().upper()

        try:
            record_entry = float(record.get('entry', 0))
        except Exception:
            record_entry = 0.0

        # Confronta asset, entry e signal_type
        if record_asset == target_asset and abs(record_entry - target_entry) < tol:#and record_signal == target_signal:
            order_id = record.get('ticket', '')
            magic_number = record.get('magic', '')
            print(f"Trovato ordine: asset {target_asset}, entry {target_entry}, ticket {order_id}, magic {magic_number}")
            return order_id, magic_number
    return None, None
