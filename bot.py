#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Breakout Bot v3. 
Добавлено: отключение фильтра MACD на 1 триггер после стоп-лосса.
"""

import json
import os
import smtplib
import ssl
import subprocess
import sys
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
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def commit_files_to_repo(files: list) -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true": return
    try:
        subprocess.run(["git", "config", "user.name", "breakout-bot"], check=False, capture_output=True)
        subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=False, capture_output=True)
        for f in files:
            subprocess.run(["git", "add", f], check=False, capture_output=True)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], check=False)
        if diff.returncode != 0:
            subprocess.run(["git", "commit", "-m", f"bot state update [{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}] [skip ci]"], check=False, capture_output=True)
            subprocess.run(["git", "push"], check=False, capture_output=True)
    except Exception as e:
        log(f"Ошибка при коммите: {e}")

def send_telegram(message: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not (token and chat_id):
        log("Telegram не настроен — пропускаем")
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=10)
        log("Telegram-уведомление отправлено")
    except Exception as e:
        log(f"Не удалось отправить в Telegram: {e}")

def get_klines(symbol, interval, limit=100):
    r = requests.get(BINANCE_KLINE_URL, params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Binance HTTP {r.status_code}")
    return r.json()

def calculate_macd_signal(klines):
    closes = [float(k[4]) for k in klines]
    ema12 = [closes[0]]
    alpha12 = 2 / (12 + 1)
    for c in closes[1:]:
        ema12.append(c * alpha12 + ema12[-1] * (1 - alpha12))
    ema26 = [closes[0]]
    alpha26 = 2 / (26 + 1)
    for c in closes[1:]:
        ema26.append(c * alpha26 + ema26[-1] * (1 - alpha26))
    macd_line = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
    signal = [macd_line[0]]
    alpha9 = 2 / (9 + 1)
    for m in macd_line[1:]:
        signal.append(m * alpha9 + signal[-1] * (1 - alpha9))
    return signal[-1]

def main():
    cfg = load_json(CONFIG_PATH, {"enabled": False})
    if not cfg.get("enabled"):
        log("Бот выключен. Выходим.")
        return

    state = load_json(STATE_PATH, {})
    if "active_trades" not in state: state["active_trades"] = []
    if "status" not in state: state["status"] = "WAITING_NEXT_TRIGGER"
    if "macd_filter" not in state: state["macd_filter"] = True
    
    log(f"Текущий статус из state.json: {state['status']}")
    log(f"Активных сделок: {len(state['active_trades'])}")
    
    now = datetime.now(timezone.utc).timestamp() * 1000
    klines_4h = get_klines(SYMBOL, TF_4H, limit=100)
    closed_4h = [k for k in klines_4h if k[6] < now]
    
    if len(closed_4h) < 2:
        log(f"Мало закрытых 4h свечей ({len(closed_4h)} шт.). Ждем следующего запуска.")
        return

    last_candle = closed_4h[-1]
    close_time = datetime.fromtimestamp(last_candle[6]/1000, tz=timezone.utc)
    high = float(last_candle[2])
    low = float(last_candle[3])
    close = float(last_candle[4])

    log(f"Последняя закрытая 4h Свеча: {close_time:%Y-%m-%d %H:%M UTC} | H: {high} | L: {low} | C: {close}")

    # --- 1. ТРЕЙЛИНГ-СТОП (00:00) ---
    if close_time.hour == 0 and state["active_trades"]:
        log("Закрытие дневной свечи. Проверяем трейлинг-стопы.")
        klines_1d = get_klines(SYMBOL, TF_1D, limit=2)
        closed_1d = [k for k in klines_1d if k[6] < now]
        if closed_1d:
            daily_low = float(closed_1d[-1][3])
            daily_trailing_sl = round(daily_low * 0.998, 2)
            
            for trade in state["active_trades"]:
                if trade.get("breakeven", False): continue
                
                entry = trade["entry_price"]
                current_sl = trade["stop_loss"]
                
                if daily_trailing_sl >= entry:
                    trade["stop_loss"] = entry
                    trade["breakeven"] = True
                    log(f"Сделка от {trade['date']} переведена в безубыток (SL: {entry})")
                    send_telegram(f"🛡️ Сделка от {trade['date']} переведена в БЕЗУБЫТОК!\nСтоп-лосс: {entry}")
                elif daily_trailing_sl > current_sl:
                    trade["stop_loss"] = daily_trailing_sl
                    log(f"Трейлинг-стоп для сделки от {trade['date']} обновлен: {daily_trailing_sl}")
                    send_telegram(f"📈 Трейлинг-стоп для сделки от {trade['date']} обновлен!\nНовый SL: {daily_trailing_sl}")
            
            save_json(STATE_PATH, state)
            commit_files_to_repo(["bot_state.json"])

    # --- 2. СТОП-ЛОССЫ ---
    for trade in list(state["active_trades"]):
        if low <= trade["stop_loss"]:
            log(f"СТОП-ЛОСС по сделке от {trade['date']}!")
            send_telegram(f"🔴 СТОП-ЛОСС по сделке от {trade['date']} сработал на {trade['stop_loss']}.")
            state["active_trades"].remove(trade)
            
            # ВЫКЛЮЧАЕМ ФИЛЬТР MACD
            state["macd_filter"] = False
            log("Фильтр MACD отключен на 1 следующий триггер.")
            
            save_json(STATE_PATH, state)
            commit_files_to_repo(["bot_state.json"])

    # --- 3. ТРИГГЕР (16:00) ---
    if close_time.hour == 16:
        macd_signal = calculate_macd_signal(closed_4h)
        
        if state["macd_filter"]:
            log(f"Триггер 16:00. Фильтр MACD ВКЛЮЧЕН. Signal = {macd_signal:.4f}")
            condition_met = macd_signal < 0
        else:
            log(f"Триггер 16:00. Фильтр MACD ОТКЛЮЧЕН (после стопа). Ставим триггер без проверки.")
            condition_met = True

        if condition_met:
            state["status"] = "WAITING_BREAKOUT"
            state["trigger_high"] = high
            state["trigger_date"] = close_time.strftime("%Y-%m-%d")
            save_json(STATE_PATH, state)
            
            msg = f"🔔 Новый триггер (16:00 UTC)\nУровень (High): {high}\n"
            if state["macd_filter"]: msg += f"MACD Signal: {macd_signal:.4f} (< 0)\n"
            else: msg += f"MACD фильтр пропущен (после стопа).\n"
            msg += "Ждем закрытия выше."
            send_telegram(msg)
            commit_files_to_repo(["bot_state.json"])
        else:
            log(f"MACD Signal >= 0 ({macd_signal:.4f}). Триггер отменен.")
            send_telegram(f"⚠️ Триггер 16:00 отменен.\nMACD Signal = {macd_signal:.4f} (должен быть < 0).")
            state["status"] = "WAITING_NEXT_TRIGGER"
            save_json(STATE_PATH, state)
            commit_files_to_repo(["bot_state.json"])
        return

    # --- 4. ЛОГИКА ВХОДА ---
    if state["status"] == "WAITING_BREAKOUT":
        if close > state["trigger_high"]:
            log(f"ПРОБОЙ! Close {close} > {state['trigger_high']}")
            state["status"] = "WAITING_RETEST"
            save_json(STATE_PATH, state)
            send_telegram(f"🚀 ПРОБОЙ!\nСвеча закрылась выше {state['trigger_high']}.\nЖдем ретеста к уровню.")
            commit_files_to_repo(["bot_state.json"])
        else:
            log(f"Пробоя нет. Close {close} <= {state['trigger_high']}. Ждем.")

    elif state["status"] == "WAITING_RETEST":
        if low <= state["trigger_high"]:
            stop_loss = round(state["trigger_high"] * 0.98, 2)
            
            new_trade = {
                "date": state["trigger_date"],
                "entry_price": state["trigger_high"],
                "stop_loss": stop_loss,
                "breakeven": False
            }
            state["active_trades"].append(new_trade)
            state["status"] = "WAITING_NEXT_TRIGGER"
            
            # ВКЛЮЧАЕМ ФИЛЬТР MACD ОБРАТНО
            state["macd_filter"] = True 
            log("Сделка открыта. Фильтр MACD включен.")
            
            del state["trigger_high"]
            del state["trigger_date"]
            
            save_json(STATE_PATH, state)
            send_telegram(f"🟢 СДЕЛКА БАЙ!\nЦена вернулась к уровню {new_trade['entry_price']}.\nСтоп-лосс установлен на {stop_loss} (-2%).")
            commit_files_to_repo(["bot_state.json"])
        else:
            log(f"Ретеста нет. Low {low} > {state['trigger_high']}. Ждем.")

if __name__ == "__main__":
    main()
