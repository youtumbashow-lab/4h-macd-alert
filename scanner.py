import requests
import json
import os
import time
from datetime import datetime, timezone

API_URL = "https://blockstream.info/api"
STATS_FILE = "stats.json"
HISTORY_FILE = "history.json"
WALLETS_FILE = "wallets_history.json"

# ВАШИ 31 АДРЕС
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
    "bc1qukqenm2t85dhdta9glqehllglxznsu4qyxn079",
    "39ar1t39jDKT8Rahf7q9ucNEmanVSRdG9Z",
    "3GaFPkeKdDcCft6oKzB4XJuPJDUmTHE8cm",
    "16vYYR5uHkdoRW7XbVzrwjyXvVwWokpEao"
]

CATEGORIES = [1, 5, 10, 20, 50, 100, 500, 1000]

def safe_get_json(url):
    """Безопасный запрос к API с проверкой формата."""
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, (dict, list)):
                return data
        return None
    except:
        return None

def safe_get_text(url):
    """Безопасный запрос текста (для хэша блока)."""
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return r.text
        return None
    except:
        return None

def scan_network_stats():
    print("Сканируем общую статистику сети...")
    
    tip_str = safe_get_text(f"{API_URL}/blocks/tip/height")
    if not tip_str:
        print("Не удалось получить высоту блока. Пропускаем.")
        return
        
    tip = int(tip_str)
    start = tip - 6
    
    stats = {str(cat): 0 for cat in CATEGORIES}
    
    # Сканируем каждый 3-й блок
    for height in range(tip, start, -1):
        try:
            block_hash = safe_get_text(f"{API_URL}/block-height/{height}")
            if not block_hash:
                continue
                
            txids = safe_get_json(f"{API_URL}/block/{block_hash}/txids")
            if not isinstance(txids, list):
                continue
                
            for txid in txids[:100]:
                tx = safe_get_json(f"{API_URL}/tx/{txid}")
                if not isinstance(tx, dict) or 'vout' not in tx:
                    continue
                    
                val = sum(vout.get('value', 0) for vout in tx.get('vout', []))
                btc = val / 100000000
                for cat in CATEGORIES:
                    if btc >= cat:
                        stats[str(cat)] += 1
        except Exception as e:
            print(f"Ошибка в блоке {height}: {e}")

    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    updated_at = datetime.now(timezone.utc).isoformat()

    # 1. Сохраняем текущую статистику (для плиток сверху)
    with open(STATS_FILE, "w") as f:
        json.dump({"updated_at": updated_at, "stats": stats}, f)

    # 2. Сохраняем в историю
    history = []
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r') as f:
            history = json.load(f)

    history = [h for h in history if h['date'] != today_str]
    history.append({
        "date": today_str,
        "stats": stats
    })
    
    history.sort(key=lambda x: x['date'], reverse=True)
    history = history[:30]
    
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f)
    
    print(f"Статистика сохранена. Дней в истории: {len(history)}")

def scan_wallets():
    print("Сканируем ваши адреса...")
    history = []
    if os.path.exists(WALLETS_FILE):
        with open(WALLETS_FILE, 'r') as f:
            history = json.load(f)
            
    seen_txids = {tx['txid'] for tx in history}
    
    for addr in WALLETS:
        try:
            txs = safe_get_json(f"{API_URL}/address/{addr}/txs")
            if not isinstance(txs, list):
                print(f"Ошибка при проверке {addr[:10]}: API вернул не список")
                continue
                
            for tx in txs:
                # Проверка, что это словарь
                if not isinstance(tx, dict):
                    continue
                    
                txid = tx.get('txid')
                if not txid or txid in seen_txids:
                    continue
                    
                received = sum(vout.get('value', 0) for vout in tx.get('vout', []) if vout.get('scriptpubkey_address') == addr)
                sent = sum(vin.get('prevout', {}).get('value', 0) for vin in tx.get('vin', []) if vin.get('prevout', {}).get('scriptpubkey_address') == addr)
                
                total = received if received > 0 else sent
                direction = "IN" if received > 0 else "OUT"
                
                if total > 0:
                    history.append({
                        'txid': txid,
                        'time_str': datetime.fromtimestamp(tx.get('status', {}).get('block_time', 0), timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
                        'address': addr,
                        'value': total,
                        'direction': direction
                    })
                    seen_txids.add(txid)
            time.sleep(0.5)
        except Exception as e:
            print(f"Ошибка при проверке {addr[:10]}: {e}")

    history.sort(key=lambda x: x['time_str'], reverse=True)
    with open(WALLETS_FILE, 'w') as f:
        json.dump(history[:500], f)
    print("Сканирование адресов завершено.")

if __name__ == "__main__":
    scan_network_stats()
    scan_wallets()
