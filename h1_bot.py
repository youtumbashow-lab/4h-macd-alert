#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
H1-бот «2 свечи Buy (H1)».

Отдельный скрипт. Своё состояние: h1_bot_state.json.
Свой конфиг: h1_bot_config.json ({"enabled": true/false}).

Мастер-флаг: работает только если bot_config.json.enabled == true
             И h1_bot_config.json.enabled == true.
"""

import json, os, smtplib, ssl, subprocess, sys
from datetime import datetime, timezone
from email.header import Header
from email.mime.text import MIMEText
from pathlib import Path
import requests

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "h1_bot_config.json"
STATE_PATH = BASE_DIR / "h1_bot_state.json"
MASTER_CONFIG_PATH = BASE_DIR / "bot_config.json"   # мастер-флаг

BINANCE_KLINE_URL = "https://data-api.binance.vision/api/v3/klines"
SYMBOL = "BTCUSDT"
TF_4H = "4h"
TF_1H = "1h"

MACD_FAST = 12
MACD_SLOW = 46
MACD_SIGNAL = 9
MAX_H1_ACTIVE = 3


def log(msg: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    print(f"[{stamp}] [H1] {msg}", flush=True)


def load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def commit_files_to_repo(files: list) -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    try:
        subprocess.run(["git", "config", "user.name", "breakout-bot"], check=False, capture_output=True)
        subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=False, capture_output=True)
        for f in files:
            subprocess.run(["git", "add", f], check=False, capture_output=True)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], check=False)
        if diff.returncode != 0:
            subprocess.run(["git", "commit", "-m", f"h1 bot state update [{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}] [skip ci]"], check=False, capture_output=True)
            subprocess.run(["git", "push"], check=False, capture_output=True)
    except Exception as e:
        log(f"Ошибка при коммите: {e}")


def send_telegram(message: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not (token and chat_id):
        return log("Telegram не настроен")
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=10)
        log("Telegram отправлен")
    except Exception as e:
        log(f"Ошибка ТГ: {e}")


def send_email(subject: str, body: str) -> None:
    email_to = os.environ.get("EMAIL_TO", "").strip()
    email_user = os.environ.get("EMAIL_USER", "").strip()
    email_pass = os.environ.get("EMAIL_APP_PASSWORD", "").replace(" ", "")
    if not (email_to and email_user and email_pass):
        return log("Email не настроен")
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
    except Exception as e:
        log(f"Ошибка Email: {e}")


def notify(subject: str, body: str) -> None:
    send_telegram(body)
    send_email(subject, body)


def get_klines(symbol, interval, limit=100):
    r = requests.get(BINANCE_KLINE_URL, params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Binance HTTP {r.status_code}")
    return r.json()


def _ema(values, period):
    alpha = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * alpha + out[-1] * (1 - alpha))
    return out


def calculate_macd_signal(klines):
    """signal line (EMA9 от MACD-линии) по 12/46/9."""
    closes = [float(k[4]) for k in klines]
    ema_fast = _ema(closes, MACD_FAST)
    ema_slow = _ema(closes, MACD_SLOW)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal = _ema(macd_line, MACD_SIGNAL)
    return signal[-1]


def _open_ms(k): return int(k[0])
def _close_ms(k): return int(k[6])
def _is_green(k): return float(k[4]) > float(k[1])
def _date_from_ms(ms): return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()


def main():
    # --- Мастер-флаг + под-флаг ---
    master = load_json(MASTER_CONFIG_PATH, {"enabled": False})
    if not master.get("enabled"):
        return log("Мастер-бот выключен (bot_config.json.enabled=false)")

    cfg = load_json(CONFIG_PATH, {"enabled": False})
    if not cfg.get("enabled"):
        return log("H1-бот выключен (h1_bot_config.json.enabled=false)")

    state = load_json(STATE_PATH, {})
    state.setdefault("h1_active_trades", [])
    state.setdefault("h1_buy_signal_sent_date", "")
    state.setdefault("h1_trade_opened_date", "")
    state.setdefault("h1_pending_pair", None)

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    now_sec = now_ms / 1000
    today_utc = datetime.now(timezone.utc).date()
    today_str = today_utc.isoformat()

    dirty = False

    # --- MACD по 4h (единый фильтр для H1) ---
    try:
        klines_4h = get_klines(SYMBOL, TF_4H, limit=200)
    except Exception as e:
        return log(f"Не удалось получить 4h для MACD: {e}")
    closed_4h = [k for k in klines_4h if _close_ms(k) < now_ms]
    if len(closed_4h) < MACD_SLOW + MACD_SIGNAL:
        return log("Мало закрытых 4h-свечей для MACD")
    macd_signal = calculate_macd_signal(closed_4h)
    macd_ok = macd_signal < 0
    log(f"MACD signal(4h)={macd_signal:.2f}, macd_ok={macd_ok}")

    # --- H1-свечи текущего дня ---
    try:
        klines_1h = get_klines(SYMBOL, TF_1H, limit=48)
    except Exception as e:
        return log(f"Не удалось получить 1h: {e}")
    
    # ВАЖНО: берем все свечи за сегодня, ВКЛЮЧАЯ текущую формирующуюся
    today_1h = [k for k in klines_1h if _date_from_ms(_open_ms(k)) == today_utc]
    # А здесь только закрытые (нужны для ведения сделок)
    closed_1h = [k for k in today_1h if _close_ms(k) < now_ms]

    # ============================================================
    # 1. Поиск паттерна + уведомление
    # ============================================================
    if (len(state["h1_active_trades"]) < MAX_H1_ACTIVE
            and state["h1_buy_signal_sent_date"] != today_str
            and macd_ok
            and len(today_1h) >= 2):

        pair = None
        for i in range(1, len(today_1h)):
            a, b = today_1h[i - 1], today_1h[i]
            if _is_green(a) and _is_green(b) and _open_ms(b) == _close_ms(a):
                pair = (a, b)
                break

        if pair:
            a, b = pair
            b_close_sec = _close_ms(b) / 1000
            dt = b_close_sec - now_sec
            
            # Отправляем, если осталось <= 15 минут, ИЛИ если свеча уже закрылась (cron опоздал)
            if dt <= 15 * 60:
                notify("2 свечи Buy (H1)", "⏰ 2 свечи Buy (H1)")
                state["h1_buy_signal_sent_date"] = today_str
                state["h1_pending_pair"] = {
                    "a_open": _open_ms(a),
                    "b_open": _open_ms(b),
                    "entry": float(b[4]),
                    "sl_a": float(a[3]),
                    "sl_b": float(b[3]),   # промежуточный (если B еще формируется)
                }
                log("⏰ 2 свечи Buy (H1) — уведомление отправлено")
                dirty = True

    # ============================================================
    # 2. Открытие сделки после закрытия B
    # ============================================================
    pending = state.get("h1_pending_pair")
    if pending:
        b_closed = None
        for k in closed_1h:
            if _open_ms(k) == pending["b_open"]:
                b_closed = k
                break

        if b_closed is not None:
            if _is_green(b_closed):
                if (state["h1_trade_opened_date"] != today_str
                        and len(state["h1_active_trades"]) < MAX_H1_ACTIVE):
                    low_a = float(pending["sl_a"])
                    low_b = float(b_closed[3])
                    sl = min(low_a, low_b)          # SL = min(low(A), low(B))
                    new_h1 = {
                        "date": _date_from_ms(pending["b_open"]).isoformat(),
                        "entry_price": float(b_closed[4]),
                        "stop_loss": sl,
                        "closed_50": False,
                        "_entry_b_open": pending["b_open"],
                    }
                    state["h1_active_trades"].append(new_h1)
                    state["h1_trade_opened_date"] = today_str
                    msg = f"🟢 H1 BUY entry={new_h1['entry_price']}, SL={new_h1['stop_loss']}"
                    notify("H1 BUY", msg)
                    log(msg)
                    dirty = True
                else:
                    log("H1: сделка сегодня уже открыта или лимит 3 — пропуск")
            else:
                log("H1: вторая свеча закрылась не зелёной — сделку не открываем")
            state["h1_pending_pair"] = None
            dirty = True

    # ============================================================
    # 3. Ведение открытых H1-сделок по закрытым H1
    # ============================================================
    if state["h1_active_trades"] and closed_1h:
        for t in list(state["h1_active_trades"]):
            trade_date = t["date"]
            entry_b_open = t.get("_entry_b_open")

            for k in closed_1h:
                k_open = _open_ms(k)
                k_date = _date_from_ms(k_open).isoformat()
                if k_date < trade_date:
                    continue
                if entry_b_open is not None and k_open == entry_b_open:
                    continue   # пропускаем свечу B

                k_low = float(k[3])
                k_close = float(k[4])

                # 1) SL
                if k_low <= t["stop_loss"]:
                    msg = f"🔴 СТОП-ЛОСС (H1) {t['date']} на {t['stop_loss']}"
                    notify("СТОП-ЛОСС (H1)", msg)
                    if t in state["h1_active_trades"]:
                        state["h1_active_trades"].remove(t)
                    dirty = True
                    break

                # 2) 50%
                if k_close < t["entry_price"] and not t.get("closed_50", False):
                    t["closed_50"] = True
                    msg = f"✂️ Закрыто 50% сделки (H1) {t['date']} (close < entry)"
                    notify("Закрыто 50% (H1)", msg)
                    dirty = True

                # 3) закрыта
                elif k_close < t["entry_price"] and t.get("closed_50", False):
                    msg = f"🔴 Сделка закрыта (H1) {t['date']}"
                    notify("Сделка закрыта (H1)", msg)
                    if t in state["h1_active_trades"]:
                        state["h1_active_trades"].remove(t)
                    dirty = True
                    break

    # ============================================================
    # Сохранение + коммит
    # ============================================================
    if dirty:
        save_json(STATE_PATH, state)
        commit_files_to_repo(["h1_bot_state.json"])
    else:
        log("Изменений нет — state не пересохраняем")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        log(f"Непредвиденная ошибка: {e}")
        sys.exit(1)
