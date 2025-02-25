import os
import csv
import configparser


# Variabile globale per il registro degli ordini (indicizzato per "magic")
ORDER_REGISTRY = {}

config = configparser.ConfigParser()
config.read('config.ini')

csv_order_registry = 'order_registry.csv'
mt4_files_folder = config['paths']['MT4_FILES_FOLDER']
# Percorso del file order_registry.csv
ORDER_REGISTRY_PATH = os.path.join(mt4_files_folder, csv_order_registry)

"""
Carica il file CSV order_registry.csv e aggiorna il dizionario globale
in cui la chiave è il magic number e il valore è un record (dict)
con i dati: timestamp, asset, signal_type, magic, ticket.
"""
def load_order_registry():
    global ORDER_REGISTRY
    registry = {}
    
    if not os.path.exists(ORDER_REGISTRY_PATH):
        print("File order_registry.csv non trovato in:", ORDER_REGISTRY_PATH)
        ORDER_REGISTRY = registry
        return registry
    try:
        with open(ORDER_REGISTRY_PATH, 'r', newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                magic = row.get('magic', '').strip()
                if magic:
                    registry[magic] = row
    except Exception as e:
        print("Errore nel caricare order_registry.csv:", e)
    
    ORDER_REGISTRY = registry
    return registry


"""
Cerca nel registro globale un ordine che corrisponda ai valori indicati nel segnale,
usando le chiavi specificate (asset, entry e signal_type).
Se viene trovato un record, aggiorna il segnale con 'order_id' e 'magic_number'.
"""
def get_order_ticket(asset, entry, signal_type, tol=0.0002):
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
