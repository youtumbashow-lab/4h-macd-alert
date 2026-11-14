import requests
import json
import os
from datetime import datetime, timezone

API_URL = "https://blockstream.info/api"
HISTORY_FILE = "onchain_history.json"
MIN_SATOSHI = 100000000  # 1 BTC
BLOCKS_TO_SCAN = 6       # Примерно 1 час (6 блоков * 10 мин)

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r') as f:
            return json.load(f)
    return []

def save_history(data):
    # Оставляем только транзакции за последние 24 часа, чтобы файл не разрастался
    now = datetime.now(timezone.utc).timestamp()
    filtered = [tx for tx in data if now - tx['timestamp'] < 86400]
    
    # Сортируем по времени (новые сверху)
    filtered.sort(key=lambda x: x['timestamp'], reverse=True)
    
    with open(HISTORY_FILE, 'w') as f:
        json.dump(filtered, f)

def scan_blocks():
    print("Получаем высоту последнего блока...")
    tip_height = int(requests.get(f"{API_URL}/blocks/tip/height").text)
    
    history = load_history()
    seen_txids = {tx['txid'] for tx in history} # Чтобы не дублировать
    
    new_found = 0
    
    for height in range(tip_height, tip_height - BLOCKS_TO_SCAN, -1):
        print(f"Сканируем блок {height}...")
        block_hash = requests.get(f"{API_URL}/block-height/{height}").text
        txids = requests.get(f"{API_URL}/block/{block_hash}/txids").json()
        
        # Берем первые 100 транзакций из блока (для скорости)
        for txid in txids[:100]:
            if txid in seen_txids:
                continue
                
            tx_data = requests.get(f"{API_URL}/tx/{txid}").json()
            
            # Считаем сумму выходов
            total_out = sum(out['value'] for out in tx_data['vout'])
            
            if total_out >= MIN_SATOSHI:
                tx_time = tx_data['status']['block_time']
                history.append({
                    'txid': txid,
                    'timestamp': tx_time,
                    'value': total_out,
                    'time_str': datetime.fromtimestamp(tx_time, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                })
                new_found += 1
                
    save_history(history)
    print(f"Готово. Добавлено новых транзакций: {new_found}. Всего в истории: {len(history)}")

if __name__ == "__main__":
    scan_blocks()
