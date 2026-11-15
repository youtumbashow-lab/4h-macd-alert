import requests
import json
import os
import time
from datetime import datetime, timezone

API_URL = "https://blockstream.info/api"
HISTORY_FILE = "wallets_history.json"
BALANCE_CACHE = "balance_cache.json"

# ВАШИ АДРЕСА
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

def load_json(filename, default):
    if os.path.exists(filename):
        with open(filename, 'r') as f:
            return json.load(f)
    return default

def save_json(filename, data):
    with open(filename, 'w') as f:
        json.dump(data, f)

def get_balance(address):
    try:
        r = requests.get(f"{API_URL}/address/{address}")
        if r.status_code == 200:
            data = r.json()
            funded = data.get('chain_stats', {}).get('funded_txo_sum', 0)
            spent = data.get('chain_stats', {}).get('spent_txo_sum', 0)
            return funded - spent
        return 0
    except:
        return 0

def get_transactions(address):
    try:
        # Получаем последние 10 транзакций (для экономии запросов)
        r = requests.get(f"{API_URL}/address/{address}/txs")
        if r.status_code == 200:
            return r.json()
        return []
    except:
        return []

def main():
    print("Начинаем сканирование кошельков...")
    
    history = load_json(HISTORY_FILE, [])
    balance_cache = load_json(BALANCE_CACHE, {})
    seen_txids = {tx['txid'] for tx in history}
    
    for addr in WALLETS:
        print(f"Проверка {addr[:10]}...")
        
        # 1. Получаем текущий баланс
        current_balance = get_balance(addr)
        
        # Проверка на пополнение "пустого" адреса (> 1000 BTC)
        # Считаем адрес пустым, если в кэше баланс был 0
        prev_balance = balance_cache.get(addr, 0)
        
        # 2. Получаем транзакции
        txs = get_transactions(addr)
        
        for tx in txs:
            txid = tx['txid']
            if txid in seen_txids:
                continue
                
            # Считаем сумму ВХОДЯЩИХ на наш адрес
            received = 0
            for vout in tx.get('vout', []):
                if vout.get('scriptpubkey_address') == addr:
                    received += vout.get('value', 0)
            
            # Считаем сумму ИСХОДЯЩИХ с нашего адреса
            sent = 0
            for vin in tx.get('vin', []):
                prevout = vin.get('prevout', {})
                if prevout.get('scriptpubkey_address') == addr:
                    sent += prevout.get('value', 0)

            total = received if received > 0 else sent
            direction = "IN" if received > 0 else "OUT"
            
            if total > 0:
                history.append({
                    'txid': txid,
                    'timestamp': tx['status'].get('block_time', 0),
                    'time_str': datetime.fromtimestamp(tx['status'].get('block_time', 0), timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
                    'address': addr,
                    'value': total,
                    'direction': direction,
                    'balance_before': prev_balance, # Для проверки условия
                    'alert_1000': (prev_balance == 0 and direction == "IN" and total >= 100000000000) # > 1000 BTC в сатоши
                })
                seen_txids.add(txid)
        
        balance_cache[addr] = current_balance
        time.sleep(1) # Пауза, чтобы не превысить лимит Blockstream
    
    # Сортируем историю (новые сверху) и обрезаем до 500 записей
    history.sort(key=lambda x: x['timestamp'], reverse=True)
    history = history[:500]
    
    save_json(HISTORY_FILE, history)
    save_json(BALANCE_CACHE, balance_cache)
    print(f"Готово. Всего транзакций в истории: {len(history)}")

if __name__ == "__main__":
    main()
