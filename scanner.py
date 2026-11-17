import requests
import json
import os
import time
from datetime import datetime, timezone

# ИСПОЛЬЗУЕМ ДРУГОЕ API ДЛЯ БЛОКОВ
API_URL_BLOCKCHAIN = "https://blockchain.info"
# ОСТАВЛЯЕМ BLOCKSTREAM ДЛЯ КОШЕЛЬКОВ
API_URL_BLOCKSTREAM = "https://blockstream.info/api"

STATS_FILE = "stats.json"
HISTORY_FILE = "history.json"
WALLETS_FILE = "wallets_history.json"

WALLETS = [
    "bc1q03yqge67aelutnxad3vkuhed5swsmllhuzspylpj4v6u3pgzpffq8wv3vg",
    "39CnKMu9KWvFAabLJ5BYghVfSqHgC2VShe",
    "bc1qjpmyeu3d6zqd6eux5ygqhpzxr3my78z5akrq4q",
    "18o27z9uXKoUJpeNtGiznH3Xm7F2YtmqKy",
    "3HX6egtUnvcNk7UMbtHmUxMT2CJUNoucVU",
    "bc1qrnvp5ljc3r8q5j4razfng87ttr59vudc2nzp8j6t84l8w2cmq83shsma8u",
    "bc1qpp93lkhpr9kdtfjcju5xp9cnal3hwcyauppu96",
    "3EjB4R4CG7egAspzLxcGJHy2EP8E4WjyFW",
    "bc1qul68d3qeqhxa2kq8yrs0l85awu8v7hp2l3pdag",
    "bc1q7whpypflx9qt04dxuztjz2ngmzujvkwy4cge7k",
    "bc1qk6x9ghxzp4kdxtxmn9shanw3zd7hq30sjs5rsa",
    "3EH763ZebaBs3cRHq4marau9RapZNkyzsF",
    "3HhbSpPhKsw4t7ond5Mv9oVZBtpQKFu5cx",
    "bc1qsjk8x6f8j9rcagpjweysx5rxwjl3tks3m84xk3",
    "34ryUknRsda7w9yXqzjLhgRPZLPV43rpsd",
    "bc1qqcgcf4m8cdsn67685lxkdlkqjwv6s5g9z9tpmh",
    "bc1qx4v26cmj9905xrq6qddg0vuvnchkuyww0s79w4",
    "bc1qrj7yuf5jvj4wgsgnj6e3pqml2pw67gqlaczls5",
    "bc1qaprw7m27tmm6f6f8s4aur7aer3n7hr9vucnmw4",
    "3PZ6abUkYCzghNbLbuSovCMFMhdjSxn2WG",
    "bc1q5amfej06quzav3vxq4cy9nkalklmjs543n649x",
    "34iPiWYZyn3d99WgiSzF5UPsir7ix3j4Xe",
    "bc1qyt5gsrxp553v9fuwk8gugvspefamdphvf4xwup",
    "38rFtDdFpXc4y6XPbSnNd2UvveEt5Xms2E",
    "bc1qxuqwdxvvufta6wxdwxp2jut6gkyhtuvjhw8awkzuvw6ac76rznvsuv2796",
    "bc1qvckrfwcppqkkx83nn0j3k5vlc6pa5a65huykdcnhypf6qtjjweus6zdp2s",
    "14FEEMRhaUwMbhf2rA1cFXmS1Zuk9nc9eq",
    "bc1qukqenm2t85dhdta9glqehllglxznsu4qyxn790",
    "39ar1t39jDKT8Rahf7q9ucNEmanVSRdG9Z",
    "3GaFPkeKdDcCft6oKzB4XJuPJDUmTHE8cm",
    "16vYYR5uHkdoRW7XbVzrwjyXvVwWokpEao"
]

CATEGORIES = [1, 5, 10, 20, 50, 100, 500, 1000]

def safe_get_json(url):
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            return r.json()
        return None
    except:
        return None

def scan_network_stats():
    print("Сканируем общую статистику сети (Blockchain.com)...")
    
    latest_block = safe_get_json(f"{API_URL_BLOCKCHAIN}/latestblock")
    if not latest_block:
        print("Не удалось получить высоту блока.")
        return
        
    tip = latest_block['height']
    start = tip - 144  # 24 часа (144 блока)
    
    stats = {str(cat): 0 for cat in CATEGORIES}
    
    for height in range(tip, start, -1):
        print(f"Сканируем блок {height}...")
        try:
            block_data = safe_get_json(f"{API_URL_BLOCKCHAIN}/block/{height}?format=json")
            if not block_data or 'tx' not in block_data:
                continue
                
            for tx in block_data['tx']:
                val = sum(out.get('value', 0) for out in tx.get('out', []))
                btc = val / 100000000
                for cat in CATEGORIES:
                    if btc >= cat:
                        stats[str(cat)] += 1
                        
        except Exception as e:
            print(f"Ошибка в блоке {height}: {e}")

    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    updated_at = datetime.now(timezone.utc).isoformat()

    with open(STATS_FILE, "w") as f:
        json.dump({"updated_at": updated_at, "stats": stats}, f)

    history = []
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r') as f:
            history = json.load(f)

    history = [h for h in history if h['date'] != today_str]
    history.append({"date": today_str, "stats": stats})
    history.sort(key=lambda x: x['date'], reverse=True)
    
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history[:30], f)
    
    print(f"Статистика сохранена. Дней в истории: {len(history)}")

def scan_wallets():
    print("Сканируем ваши адреса (Blockstream)...")
    history = []
    if os.path.exists(WALLETS_FILE):
        with open(WALLETS_FILE, 'r') as f:
            history = json.load(f)
            
    seen_txids = {tx['txid'] for tx in history}
    now_ts = datetime.now(timezone.utc).timestamp()
    
    for addr in WALLETS:
        try:
            addr_info = safe_get_json(f"{API_URL_BLOCKSTREAM}/address/{addr}")
            current_balance = 0
            if isinstance(addr_info, dict):
                funded = addr_info.get('chain_stats', {}).get('funded_txo_sum', 0)
                spent = addr_info.get('chain_stats', {}).get('spent_txo_sum', 0)
                current_balance = funded - spent

            txs = safe_get_json(f"{API_URL_BLOCKSTREAM}/address/{addr}/txs")
            if not isinstance(txs, list):
                continue
            
            txs.sort(key=lambda x: x.get('status', {}).get('block_time', 0))
                
            for tx in txs:
                if not isinstance(tx, dict):
                    continue
                    
                txid = tx.get('txid')
                if not txid or txid in seen_txids:
                    continue
                
                block_time = tx.get('status', {}).get('block_time', 0)
                if not block_time or (now_ts - block_time > 86400):
                    continue
                    
                received = sum(vout.get('value', 0) for vout in tx.get('vout', []) if vout.get('scriptpubkey_address') == addr)
                sent = sum(vin.get('prevout', {}).get('value', 0) for vin in tx.get('vin', []) if vin.get('prevout', {}).get('scriptpubkey_address') == addr)
                
                total = received if received > 0 else sent
                direction = "IN" if received > 0 else "OUT"
                
                is_alert = False
                if direction == "IN" and total >= 100000000000 and current_balance - total <= 0:
                    is_alert = True
                
                # ФИЛЬТР: Показываем только транзакции от 1 BTC (100 000 000 сатоши)
                if total >= 100000000:
                    history.append({
                        'txid': txid,
                        'time_str': datetime.fromtimestamp(block_time, timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
                        'address': addr,
                        'balance': current_balance,
                        'value': total,
                        'direction': direction,
                        'alert_1000': is_alert
                    })
                    seen_txids.add(txid)
            time.sleep(0.5)
        except Exception as e:
            print(f"Ошибка при проверке {addr[:10]}: {e}")

    # ОЧИСТКА: Удаляем всё, что старше 24 часов или меньше 1 BTC
    cleaned_history = []
    for tx in history:
        tx_time = datetime.strptime(tx['time_str'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).timestamp()
        if (now_ts - tx_time <= 86400) and (tx['value'] >= 100000000):
            cleaned_history.append(tx)

    cleaned_history.sort(key=lambda x: x['time_str'], reverse=True)
    
    with open(WALLETS_FILE, 'w') as f:
        json.dump(cleaned_history[:500], f)
    print("Сканирование адресов завершено.")

if __name__ == "__main__":
    scan_network_stats()
    scan_wallets()
