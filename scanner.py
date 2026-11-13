import os
import json
import requests
from datetime import datetime, timedelta, timezone

MIN_BTC = 1.0
MIN_SATOSHI = int(MIN_BTC * 100_000_000)
HOURS_BACK = 24
BLOCKCHAIR_URL = "https://api.blockchair.com/bitcoin/transactions"
OUTPUT_FILE = "onchain_stats.json"

def fetch_transactions(start_time_iso):
    all_txs = []
    offset = 0
    max_pages = 20  # Ограничим, чтобы не превысить лимиты бесплатного API
    
    while offset < max_pages * 100:
        params = {
            "q": f"output_total({MIN_SATOSHI}..),time({start_time_iso}..)",
            "s": "time(desc)",
            "limit": 100,
            "offset": offset
        }
        
        try:
            r = requests.get(BLOCKCHAIR_URL, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"Ошибка API: {e}")
            break
        
        txs = data.get("data") or []
        if not txs:
            break
        
        all_txs.extend(txs)
        
        if len(txs) < 100:
            break
            
        offset += 100
    
    return all_txs

def main():
    print(f"Сбор транзакций >= {MIN_BTC} BTC за {HOURS_BACK}ч...")
    start_time = datetime.now(timezone.utc) - timedelta(hours=HOURS_BACK)
    start_iso = start_time.strftime("%Y-%m-%d %H:%M:%S")
    
    txs = fetch_transactions(start_iso)
    total = len(txs)
    
    # Заглушки для бирж и топ-кошельков (пока не подключен список адресов)
    stats = {
        "total_tx_1btc": total,
        "exchange_in": 0,
        "exchange_out": 0,
        "top_wallets_tx": 0,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "period_hours": HOURS_BACK
    }
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    
    print(f"Готово! Найдено транзакций: {total}")

if __name__ == "__main__":
    main()
