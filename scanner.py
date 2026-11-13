import os
import json
import requests
from datetime import datetime, timedelta, timezone

MIN_BTC = 1.0
MIN_SATOSHI = int(MIN_BTC * 100_000_000)
HOURS_BACK = 24
BLOCKCHAIR_URL = "https://api.blockchair.com/bitcoin/transactions"
ADDRESSES_FILE = "addresses.json"
OUTPUT_FILE = "onchain_stats.json"

def load_address_lists():
    if not os.path.exists(ADDRESSES_FILE):
        print(f"Файл {ADDRESSES_FILE} не найден!")
        return set(), set()
    
    with open(ADDRESSES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    exchanges = set(data.get("exchanges", []))
    top_wallets = set(data.get("top_wallets", []))
    print(f"Загружено: Биржи={len(exchanges)}, Топ-200={len(top_wallets)}")
    return exchanges, top_wallets

def get_transaction_details(tx_hash):
    """Получает детали транзакции (входы и выходы) для классификации."""
    url = f"https://api.blockchair.com/bitcoin/dashboards/transaction/{tx_hash}"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json().get("data", {}).get(tx_hash)
        if not data:
            return None
        
        # Собираем все адреса, участвующие в транзакции
        addresses = set()
        
        # Входы (Inputs)
        for inp in data.get("inputs", []):
            if inp.get("recipient"):
                addresses.add(inp["recipient"])
                
        # Выходы (Outputs)
        for out in data.get("outputs", []):
            if out.get("recipient"):
                addresses.add(out["recipient"])
                
        return addresses
    except Exception as e:
        print(f"Ошибка получения деталей {tx_hash}: {e}")
        return None

def main():
    print(f"Сбор транзакций >= {MIN_BTC} BTC за {HOURS_BACK}ч...")
    start_time = datetime.now(timezone.utc) - timedelta(hours=HOURS_BACK)
    start_iso = start_time.strftime("%Y-%m-%d %H:%M:%S")
    
    exchange_addrs, top_wallet_addrs = load_address_lists()
    
    # 1. Получаем список крупных транзакций
    params = {
        "q": f"output_total({MIN_SATOSHI}..),time({start_iso}..)",
        "s": "time(desc)",
        "limit": 100
    }
    
    try:
        r = requests.get(BLOCKCHAIR_URL, params=params, timeout=30)
        txs = r.json().get("data") or []
    except Exception as e:
        print(f"Ошибка API: {e}")
        return
    
    total = len(txs)
    print(f"Найдено {total} транзакций. Начинаем классификацию...")
    
    exchange_in = 0
    exchange_out = 0
    top_wallets_tx = 0
    
    # 2. Проверяем каждую транзакцию (ограничим 20-ю для теста, чтобы не спамить API)
    for tx in txs[:20]:
        tx_hash = tx["hash"]
        # Получаем адреса, участвующие в транзакции
        involved_addresses = get_transaction_details(tx_hash)
        
        if not involved_addresses:
            continue
            
        # Проверяем пересечения со списками
        if involved_addresses & exchange_addrs:
            # Упрощённо: если есть адрес биржи, считаем что это взаимодействие с биржей.
            # Точное направление (in/out) требует более сложной логики.
            exchange_in += 1
            exchange_out += 1
            
        if involved_addresses & top_wallet_addrs:
            top_wallets_tx += 1
            
        # Небольшая пауза, чтобы не превысить лимиты Blockchair
        import time
        time.sleep(0.5)

    # Масштабируем результат для всех транзакций (очень грубая оценка, но для демо сойдёт)
    if total > 20:
        multiplier = total / 20
        exchange_in = int(exchange_in * multiplier)
        exchange_out = int(exchange_out * multiplier)
        top_wallets_tx = int(top_wallets_tx * multiplier)

    stats = {
        "total_tx_1btc": total,
        "exchange_in": exchange_in,
        "exchange_out": exchange_out,
        "top_wallets_tx": top_wallets_tx,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "period_hours": HOURS_BACK
    }
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    
    print(f"Готово! {stats}")

if __name__ == "__main__":
    main()
