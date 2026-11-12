#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, os, smtplib, ssl, subprocess, sys
from datetime import datetime, timezone
from email.header import Header
from email.mime.text import MIMEText
from pathlib import Path
import requests

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "bot_config.json"
STATE_PATH = BASE_DIR / "bot_state.json"

BINANCE_KLINE_URL = "https://data-api.binance.vision/api/v3/klines"
SYMBOL = "BTCUSDT" 
TF_4H = "4h"
TF_1D = "1d"

def log(msg: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    print(f"[{stamp}] {msg}", flush=True)

def load_json(path: Path, default: dict) -> dict:
    if not path.exists(): return default
    try:
        with open(path, encoding="utf-8") as f: return json.load(f)
    except Exception: return default

def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def commit_files_to_repo(files: list) -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true": return
    try:
        subprocess.run(["git", "config", "user.name", "breakout-bot"], check=False, capture_output=True)
        subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=False, capture_output=True)
        for f in files: subprocess.run(["git", "add", f], check=False, capture_output=True)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], check=False)
        if diff.returncode != 0:
            subprocess.run(["git", "commit", "-m", f"bot state update [{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}] [skip ci]"], check=False, capture_output=True)
            subprocess.run(["git", "push"], check=False, capture_output=True)
    except Exception as e: log(f"Ошибка при коммите: {e}")

def send_telegram(message: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not (token and chat_id): return log("Telegram не настроен")
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=10)
        log("Telegram отправлен")
    except Exception as e: log(f"Ошибка ТГ: {e}")

def send_email(subject: str, body: str) -> None:
    email_to = os.environ.get("EMAIL_TO", "").strip()
    email_user = os.environ.get("EMAIL_USER", "").strip()
    email_pass = os.environ.get("EMAIL_APP_PASSWORD", "").replace(" ", "")
    if not (email_to and email_user and email_pass): return log("Email не настроен")
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = email_user
        msg["To"] = email_to
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
            s.login(email_user, email_pass)
            s.send_message(msg)
        log("EMAIL отправлен")
    except Exception as e: log(f"Ошибка Email: {e}")

def get_klines(symbol, interval, limit=100):
    r = requests.get(BINANCE_KLINE_URL, params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=30)
    if r.status_code != 200: raise RuntimeError(f"Binance HTTP {r.status_code}")
    return r.json()

def calculate_macd_signal(klines):
    closes = [float(k[4]) for k in klines]
    ema12 = [closes[0]]; alpha12 = 2/(12+1)
    for c in closes[1:]: ema12.append(c * alpha12 + ema12[-1] * (1 - alpha12))
    ema46 = [closes[0]]; alpha46 = 2/(46+1)
    for c in closes[1:]: ema46.append(c * alpha46 + ema46[-1] * (1 - alpha46))
    macd_line = [e12 - e46 for e12, e46 in zip(ema12, ema46)]
    signal = [macd_line[0]]; alpha9 = 2/(9+1)
    for m in macd_line[1:]: signal.append(m * alpha9 + signal[-1] * (1 - alpha9))
    return signal[-1]

def main():
    cfg = load_json(CONFIG_PATH, {"enabled": False})
    if not cfg.get("enabled"): return log("Бот выключен")
    state = load_json(STATE_PATH, {})
    if "active_trades" not in state: state["active_trades"] = []
    if "status" not in state: state["status"] = "WAITING_NEXT_TRIGGER"
    if "macd_filter" not in state: state["macd_filter"] = True
    if "last_trigger_date" not in state: state["last_trigger_date"] = ""
    save_json(STATE_PATH, state)
    
    now = datetime.now(timezone.utc).timestamp() * 1000
    klines_4h = get_klines(SYMBOL, TF_4H, limit=100)
    closed_4h = [k for k in klines_4h if k[6] < now]
    if len(closed_4h) < 2: return log("Мало свечей")
    
    last_candle = closed_4h[-1]
    open_time = datetime.fromtimestamp(last_candle[0]/1000, tz=timezone.utc)
    close_time = datetime.fromtimestamp(last_candle[6]/1000, tz=timezone.utc)
    high, low, close = float(last_candle[2]), float(last_candle[3]), float(last_candle[4])
    log(f"Свеча (Открытие: {open_time:%H:%M}, Закрытие: {close_time:%H:%M}) | H: {high} | L: {low} | C: {close}")

    # 1. ТРЕЙЛИНГ
    if close_time.hour == 0 and state["active_trades"]:
        klines_1d = get_klines(SYMBOL, TF_1D, limit=2)
        closed_1d = [k for k in klines_1d if k[6] < now]
        if closed_1d:
            daily_low = float(closed_1d[-1][3])
            new_sl = round(daily_low * 0.998, 2)
            for trade in state["active_trades"]:
                if trade.get("breakeven", False): continue
                entry, current_sl = trade["entry_price"], trade["stop_loss"]
                if new_sl >= entry:
                    trade["stop_loss"], trade["breakeven"] = entry, True
                    msg = f"🛡️ Безубыток по сделке {trade['date']}: {entry}"
                    send_telegram(msg); send_email("Безубыток", msg)
                elif new_sl > current_sl:
                    trade["stop_loss"] = new_sl
                    msg = f"📈 Трейлинг-стоп {trade['date']}: {new_sl}"
                    send_telegram(msg); send_email("Трейлинг", msg)
            save_json(STATE_PATH, state)

    # 2. СТОП
    for trade in list(state["active_trades"]):
        if low <= trade["stop_loss"]:
            msg = f"🔴 СТОП-ЛОСС {trade['date']} на {trade['stop_loss']}"
            send_telegram(msg); send_email("СТОП-ЛОСС", msg)
            state["active_trades"].remove(trade)
            state["macd_filter"] = False
            save_json(STATE_PATH, state)
            commit_files_to_repo(["bot_state.json"])

    # 3. ТРИГГЕР (Свеча, открывшаяся в 12:00, закрывается в 16:00)
    is_trigger_candle = (open_time.hour == 12)
    trigger_date_str = open_time.strftime("%Y-%m-%d")
    
    if is_trigger_candle and state["last_trigger_date"] != trigger_date_str:
        state["last_trigger_date"] = trigger_date_str
        
        macd_signal = calculate_macd_signal(closed_4h)
        condition_met = state["macd_filter"] and (macd_signal < 0) or not state["macd_filter"]
        
        if condition_met:
            state["status"], state["trigger_high"], state["trigger_date"] = "WAITING_BREAKOUT", high, trigger_date_str
            save_json(STATE_PATH, state)
            msg = f"🔔 Триггер {trigger_date_str}: {high}\nMACD: {macd_signal:.4f}"
            send_telegram(msg); send_email("Триггер", msg)
        else:
            state["status"] = "WAITING_NEXT_TRIGGER"
            msg = f"⚠️ Триггер {trigger_date_str} отменен. MACD: {macd_signal:.4f}"
            send_telegram(msg); send_email("Отмена", msg)
            save_json(STATE_PATH, state)
        return

    # 4. ВХОД
    if state["status"] == "WAITING_BREAKOUT" and close > state["trigger_high"]:
        state["status"] = "WAITING_RETEST"
        save_json(STATE_PATH, state)
        msg = f"🚀 ПРОБОЙ! {state['trigger_high']}"
        send_telegram(msg); send_email("Пробой", msg)

    elif state["status"] == "WAITING_RETEST" and low <= state["trigger_high"]:
        new_trade = {"date": state["trigger_date"], "entry_price": state["trigger_high"], "stop_loss": round(state["trigger_high"]*0.98, 2), "breakeven": False}
        state["active_trades"].append(new_trade)
        state["status"], state["macd_filter"] = "WAITING_NEXT_TRIGGER", True
        msg = f"🟢 БАЙ {new_trade['entry_price']}, SL {new_trade['stop_loss']}"
        send_telegram(msg); send_email("БАЙ", msg)
        del state["trigger_high"], state["trigger_date"]
        save_json(STATE_PATH, state)
        commit_files_to_repo(["bot_state.json"])

if __name__ == "__main__":
    main()
