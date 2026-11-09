#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Price Alert Bot (Bybit Futures, multi-ticker, 4h, «выше уровня», одноразовый)
=============================================================================
Логика:
  - За 30 минут до закрытия 4h-свечи workflow стартует.
  - Скрипт спит до момента «за 10 минут до закрытия».
  - Берёт текущую цену с публичного Bybit /v5/market/tickers (category=linear).
  - Для каждого включённого алерта: если price > above — шлёт письмо/ТГ
    и ставит enabled=false (алерт одноразовый, пока руками не включат).
"""

import json
import os
import smtplib
import ssl
import subprocess
import sys
import time
from datetime import datetime, timezone
from email.header import Header
from email.mime.text import MIMEText
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "price_config.json"
STATE_PATH = BASE_DIR / "price_state.json"

# --- ИЗМЕНЕНО: Bybit Futures (Linear) API ---
BYBIT_TICKERS_URL = "https://api.bybit.com/v5/market/tickers?category=linear"

TF_SECONDS = {"4h": 4 * 3600}

# За сколько секунд до закрытия делаем финальную проверку (10 минут)
TARGET_BEFORE_CLOSE_SEC = 10 * 60
# Верхняя граница сна (если workflow запустился слишком рано — пропускаем)
MAX_SLEEP_SEC = 24 * 3600


def log(msg: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    print(f"[{stamp}] {msg}", flush=True)


def fail(msg: str) -> None:
    log(f"ОШИБКА: {msg}")
    sys.exit(1)


def load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"Не удалось прочитать {path.name}: {e} — использую пустой")
        return default


def save_json(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def commit_files_to_repo(files: list) -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    try:
        subprocess.run(["git", "config", "user.name", "price-alert-bot"],
                       check=False, capture_output=True)
        subprocess.run(["git", "config", "user.email",
                        "41898282+github-actions[bot]@users.noreply.github.com"],
                       check=False, capture_output=True)
        for f in files:
            subprocess.run(["git", "add", f], check=False, capture_output=True)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"],
                              check=False)
        if diff.returncode == 0:
            log("Файлы не изменились — коммит не нужен")
            return
        subprocess.run(["git", "commit", "-m",
                        f"price alert state [{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}] [skip ci]"],
                       check=False, capture_output=True)
        res = subprocess.run(["git", "push"], check=False,
                             capture_output=True, text=True)
        if res.returncode == 0:
            log("Изменения запушены в репозиторий")
        else:
            log(f"git push не удался: {res.stderr.strip()[:200]}")
    except Exception as e:
        log(f"Ошибка при коммите: {e}")


def to_bybit_symbol(ticker: str) -> str:
    """Преобразует BTC-USD в BTCUSDT (стандарт для linear фьючерсов Bybit)"""
    t = ticker.strip().upper()
    if t.endswith("-USD"):
        return t[:-4] + "USDT"
    t = t.replace("-", "").replace("/", "")
    if t.endswith(("USDT", "USDC", "BTC", "ETH")):
        return t
    return t + "USDT"


def next_close_utc(tf: str) -> datetime:
    step = TF_SECONDS[tf]
    now = datetime.now(timezone.utc)
    epoch = int(now.timestamp())
    next_close_epoch = ((epoch // step) + 1) * step
    return datetime.fromtimestamp(next_close_epoch, tz=timezone.utc)


def sleep_until_target(tf: str) -> datetime:
    close_at = next_close_utc(tf)
    target = close_at.timestamp() - TARGET_BEFORE_CLOSE_SEC
    now = time.time()
    delta = target - now
    log(f"Ближайшее закрытие {tf}: {close_at:%Y-%m-%d %H:%M UTC}; "
        f"цель проверки: -{TARGET_BEFORE_CLOSE_SEC} сек; "
        f"ждать {delta:.0f} сек")

    if delta > MAX_SLEEP_SEC:
        log(f"До цели больше {MAX_SLEEP_SEC} сек — пропускаем запуск")
        sys.exit(0)

    while True:
        remaining = target - time.time()
        if remaining <= 0:
            break
        time.sleep(min(10, remaining))
        log(f"  осталось {target - time.time():.0f} сек")

    log(f"Целевое время достигнуто: {datetime.now(timezone.utc):%H:%M:%S UTC}")
    return close_at


def fetch_prices_bybit(symbols: list) -> dict:
    """Запрашивает цены с Bybit Futures (Linear)"""
    log(f"Запрос цен с Bybit Futures (Linear) по {len(symbols)} символам...")
    r = requests.get(BYBIT_TICKERS_URL, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Bybit HTTP {r.status_code}: {r.text[:150]}")
    
    data = r.json()
    if data.get("retCode") != 0:
        raise RuntimeError(f"Bybit API Error: {data.get('retMsg')}")

    # Структура Bybit: result.list -> [ {symbol, lastPrice, ...}, ... ]
    tickers_list = data.get("result", {}).get("list", [])
    by_symbol = {}
    for item in tickers_list:
        sym = item.get("symbol")
        price_str = item.get("lastPrice")
        if sym and price_str:
            by_symbol[sym] = float(price_str)

    result = {}
    for s in symbols:
        if s in by_symbol:
            result[s] = by_symbol[s]
        else:
            log(f"  Внимание: {s} нет в ответе Bybit (возможно, это не фьючерс)")
    return result


def fmt_price(price: float) -> str:
    if price >= 1:
        return f"{price:.2f}"
    if price >= 0.01:
        return f"{price:.4f}"
    return f"{price:.8f}".rstrip("0").rstrip(".")


def send_email(subject: str, body: str) -> None:
    email_to = os.environ.get("EMAIL_TO", "").strip()
    email_user = os.environ.get("EMAIL_USER", "").strip()
    email_pass = os.environ.get("EMAIL_APP_PASSWORD", "").replace(" ", "")
    if not (email_to and email_user and email_pass):
        raise RuntimeError("Не заданы EMAIL_TO / EMAIL_USER / EMAIL_APP_PASSWORD")
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = email_user
    msg["To"] = email_to
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
        s.login(email_user, email_pass)
        s.send_message(msg)


def send_telegram(message: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not (token and chat_id):
        log("Telegram не настроен — пропускаем")
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message}
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            log("Telegram-уведомление отправлено")
        else:
            log(f"Telegram вернул ошибку {r.status_code}: {r.text[:150]}")
    except Exception as e:
        log(f"Не удалось отправить в Telegram: {e}")


def build_email(fired: list, close_at: datetime) -> tuple:
    n = len(fired)
    if n == 1:
        f = fired[0]
        subject = f"[PRICE] {f['ticker']} > {fmt_price(f['above'])} ({f['tf']})"
    else:
        tickers = ", ".join(f["ticker"] for f in fired[:4])
        if n > 4:
            tickers += f", … (+{n - 4})"
        subject = f"[PRICE] {n} алертов: {tickers}"

    lines = []
    lines.append(f"Сработало алертов: {n}")
    lines.append(f"Закрытие свечи:    {close_at:%Y-%m-%d %H:%M UTC}")
    lines.append("")
    lines.append("=" * 60)
    for f in fired:
        lines.append("")
        lines.append(f"{f['ticker']} ({f['tf']})")
        lines.append("-" * 60)
        lines.append(f"  Условие:  цена > {fmt_price(f['above'])}")
        lines.append(f"  Текущая:  {fmt_price(f['price'])}")
        lines.append(f"  Алерт «{f['id']}» теперь ВЫКЛЮЧЕН (одноразовый).")
    lines.append("")
    lines.append("=" * 60)
    lines.append("— Price Alert Bot (Bybit Futures, одноразовый, 4h)")
    return subject, "\n".join(lines)


def main() -> None:
    cfg = load_json(CONFIG_PATH, {"timeframe": "4h", "alerts": []})
    tf = cfg.get("timeframe", "4h")
    if tf not in TF_SECONDS:
        fail(f"Поддерживается только timeframe из {list(TF_SECONDS)}")

    alerts = cfg.get("alerts", [])
    enabled = [a for a in alerts if a.get("enabled")]
    if not enabled:
        log("Нет включённых алертов — выходим")
        return

    close_at = sleep_until_target(tf)
    candle_key = close_at.strftime("%Y-%m-%d %H:%M:%S+00:00")

    symbols = {to_bybit_symbol(a["ticker"]) for a in enabled}
    try:
        prices = fetch_prices_bybit(sorted(symbols))
    except Exception as e:
        fail(f"Не удалось получить цены: {e}")

    state = load_json(STATE_PATH, {})
    fired = []
    for a in enabled:
        aid = a["id"]
        ticker = a["ticker"]
        above = float(a["above"])
        sym = to_bybit_symbol(ticker)
        price = prices.get(sym)
        if price is None:
            log(f"{aid}: нет цены для {sym} — пропуск")
            continue

        if state.get(aid) == candle_key:
            log(f"{aid}: уже сработал на свече {candle_key} — пропуск")
            continue

        log(f"{aid}: {ticker} цена {fmt_price(price)} vs above {fmt_price(above)}")
        if price > above:
            log(f"  ✅ СРАБОТАЛ")
            fired.append({
                "id": aid,
                "ticker": ticker,
                "tf": tf,
                "above": above,
                "price": price,
            })
            state[aid] = candle_key
            a["enabled"] = False

    if not fired:
        log("Сработавших алертов нет — письмо не отправляем")
        save_json(STATE_PATH, state)
        return

    subject, body = build_email(fired, close_at)
    
    try:
        send_email(subject, body)
        log(f"EMAIL ОТПРАВЛЕН: {subject}")
    except Exception as e:
        log(f"Не удалось отправить email: {e}")
    
    send_telegram(f"🔔 {subject}\n\n{body}")

    save_json(STATE_PATH, state)
    save_json(CONFIG_PATH, cfg)
    commit_files_to_repo(["price_state.json", "price_config.json"])


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        log(f"Непредвиденная ошибка: {e}")
        sys.exit(1)
