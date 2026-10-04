#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MACD + RSI Alert Bot (Binance, multi-ticker, ранний сигнал)
============================================================
Данные — Binance public market data через зеркало data-api.binance.vision
(публичные klines, без ключа, без геоблокировки для облачных IP).

Сигналы на свече за LEAD_MINUTES минут до её закрытия:

  MACD:
    GREEN:  смена тёмно-зелёной -> светлозелёной (hist > 0: рост сменился падением)
            БЕЗ всяких фильтров по signal
    GREEN2: смена светлозелёной -> тёмно-зелёной (hist > 0: падение сменилось ростом)
            БЕЗ всяких фильтров по signal
    RED:    смена тёмно-красной -> светло-красной (hist < 0: падение сменилось ростом)
            И signal(9) < 0

  RSI(14):
    RSI_OVERBOUGHT: RSI > 70
    RSI_OVERSOLD:   RSI < 30

Особенности:
  - несколько тикеров (массив в config.json), по каждому независимый анализ;
  - ТФ напрямую из конфига (Binance поддерживает 4h нативно);
  - анализ за LEAD_MINUTES минут до закрытия; после закрытия повтора не будет
    (дедупликация по "TICKER_TF_DIRECTION" -> время свечи в state.json);
  - ошибка по одному тикеру не роняет остальные.

config.json:
  {
    "ticker": ["BTC-USD", "ETH-USD", ...],
    "timeframe": "4h",
    "fast": 12,
    "slow": 26
  }

Переменные окружения: EMAIL_TO, EMAIL_USER, EMAIL_APP_PASSWORD.
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

import pandas as pd
import requests

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "state.json"

# Публичное зеркало Binance для market data — не блокирует дата-центры.
BINANCE_KLINE_URL = "https://data-api.binance.vision/api/v3/klines"
KLINES_LIMIT = 500  # свечей на запрос (хватит для MACD 26+9+запас и RSI 14)

# таймфрейм -> интервал Binance
BINANCE_INTERVAL = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "8h": "8h",
    "12h": "12h", "1d": "1d", "3d": "3d", "1w": "1w",
}

# длина сигнальной линии MACD
SIGNAL_LEN = 9

# период RSI
RSI_LEN = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30

# анализируем свечу, когда до её закрытия осталось <= LEAD_MINUTES минут
LEAD_MINUTES = 7

# длительность таймфрейма в секундах (для подстраховки таймингов)
TF_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800,
    "12h": 43200, "1d": 86400, "3d": 259200, "1w": 604800,
}

COLOR_NAMES = {
    "light_green": "светлозелёная (выше 0, падает)",
    "dark_green":  "тёмно-зелёная (выше 0, растёт)",
    "light_red":   "светло-красная (ниже 0, растёт)",
    "dark_red":    "тёмно-красная (ниже 0, падает)",
}


def log(msg: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    print(f"[{stamp}] {msg}", flush=True)


def fail(msg: str) -> None:
    log(f"ОШИБКА: {msg}")
    sys.exit(1)


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        fail(f"Не найден config.json рядом со скриптом ({CONFIG_PATH})")
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    tf = str(cfg.get("timeframe", "4h"))
    if tf not in TF_SECONDS:
        fail(f"Неизвестный timeframe '{tf}'. Допустимые: {', '.join(TF_SECONDS)}")
    return cfg


def to_binance_symbol(ticker: str) -> str:
    """'BTC-USD' -> 'BTCUSDT', 'ETH-USDT' -> 'ETHUSDT', 'SOL' -> 'SOLUSDT'."""
    t = ticker.strip().upper()
    if t.endswith("-USD"):
        return t[:-4] + "USDT"
    t = t.replace("-", "").replace("/", "")
    if t.endswith(("USDT", "USDC", "BTC", "ETH")):
        return t
    return t + "USDT"


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def commit_state_to_repo() -> None:
    """В GitHub Actions сохраняем state.json обратно, чтобы при следующем
    запуске бот знал, какие свечи уже обработаны (защита от дублей)."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    try:
        subprocess.run(["git", "config", "user.name", "github-actions"],
                       check=False, capture_output=True)
        subprocess.run(["git", "config", "user.email",
                        "github-actions@users.noreply.github.com"],
                       check=False, capture_output=True)
        subprocess.run(["git", "add", "state.json"],
                       check=False, capture_output=True)
        subprocess.run(["git", "commit", "-m",
                        f"state: сигнал зафиксирован "
                        f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}"],
                       check=False, capture_output=True)
        res = subprocess.run(["git", "push"],
                             check=False, capture_output=True, text=True)
        if res.returncode == 0:
            log("state.json сохранён в репозиторий")
        else:
            log(f"git push не удался (проверьте 'permissions: contents: write' "
                f"в workflow): {res.stderr.strip()[:200]}")
    except Exception as e:
        log(f"Не удалось закоммитить state.json: {e}")


def fetch_data(ticker: str, timeframe: str) -> pd.DataFrame:
    """Свечи с Binance (через публичное зеркало data-api.binance.vision).
    Возвращает DataFrame с колонками Open/High/Low/Close/Volume,
    UTC-индексом по времени открытия и close_time."""
    symbol = to_binance_symbol(ticker)
    interval = BINANCE_INTERVAL.get(timeframe)
    if interval is None:
        raise RuntimeError(f"Binance не поддерживает ТФ '{timeframe}'. "
                           f"Допустимые: {', '.join(BINANCE_INTERVAL)}")
    log(f"Загрузка {ticker} -> {symbol} {timeframe} (Binance, limit {KLINES_LIMIT})...")
    r = requests.get(
        BINANCE_KLINE_URL,
        params={"symbol": symbol, "interval": interval, "limit": KLINES_LIMIT},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Binance вернул HTTP {r.status_code}: {r.text[:150]}")
    rows = r.json()
    if not rows:
        raise RuntimeError(f"Binance не отдал свечи по {symbol}")

    df = pd.DataFrame(rows, columns=[
        "open_time", "Open", "High", "Low", "Close", "Volume",
        "close_time", "qav", "trades", "tbb", "tbq", "ignore",
    ])
    for c in ["Open", "High", "Low", "Close", "Volume"]:
        df[c] = df[c].astype(float)
    df.index = pd.to_datetime(df["open_time"].astype("int64"),
                              unit="ms", utc=True)
    df = df.sort_index()
    df["close_time"] = pd.to_datetime(
        df["close_time"].astype("int64"), unit="ms", utc=True
    )
    return df[["Open", "High", "Low", "Close", "Volume", "close_time"]]


def prepare_candle(df: pd.DataFrame, timeframe: str):
    """Выбираем свечу для анализа по времени закрытия свечи.

    - свеча уже закрылась             -> анализируем её как есть;
    - до закрытия <= LEAD_MINUTES     -> анализируем ФОРМИРУЮЩУЮСЯ свечу;
    - до закрытия больше              -> рано, тикер пропускаем.
    Возвращает DataFrame или None (пропуск)."""
    close_time = df["close_time"].iloc[-1]
    now = pd.Timestamp.now(tz="UTC")
    remaining_min = (close_time - now).total_seconds() / 60.0
    if remaining_min <= 0:
        return df  # свеча закрыта
    if remaining_min > LEAD_MINUTES:
        log(f"До закрытия свечи ещё {remaining_min:.0f} мин "
            f"(> {LEAD_MINUTES}) — рано, пропуск")
        return None
    log(f"До закрытия свечи {remaining_min:.1f} мин — анализируем формирующуюся")
    return df


def add_indicators(df: pd.DataFrame, fast: int, slow: int,
                   signal_len: int, rsi_len: int) -> pd.DataFrame:
    """Добавляет MACD (macd/signal/hist) и RSI(rsi_len)."""
    close = df["Close"]

    # MACD
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    sig_line = macd_line.ewm(span=signal_len, adjust=False).mean()

    # RSI (метод Уайлдера через EWM с alpha=1/len)
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / rsi_len, adjust=False, min_periods=rsi_len).mean()
    avg_loss = loss.ewm(alpha=1.0 / rsi_len, adjust=False, min_periods=rsi_len).mean()
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    # если avg_loss == 0 -> RSI = 100
    rsi = rsi.where(avg_loss != 0, 100.0)

    out = df.copy()
    out["macd"] = macd_line
    out["signal"] = sig_line
    out["hist"] = macd_line - sig_line
    out["rsi"] = rsi
    return out


def hist_color(hist: float, hist_prev: float) -> str:
    if hist > 0 and hist < hist_prev:   # выше нуля и падает
        return "light_green"
    if hist > 0:                        # выше нуля и растёт
        return "dark_green"
    if hist < 0 and hist > hist_prev:   # ниже нуля и растёт
        return "light_red"
    return "dark_red"                   # ниже нуля и падает


def fmt_price(price: float) -> str:
    """Адаптивный формат цены: для дешёвых монет больше знаков."""
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


def process_ticker(ticker: str, tf: str, fast: int, slow: int, state: dict) -> None:
    """Анализ одного тикера. Ошибки не роняют остальные."""
    df = fetch_data(ticker, tf)
    df = prepare_candle(df, tf)
    if df is None:
        return

    need = max(slow + SIGNAL_LEN + 5, RSI_LEN + 5)
    if len(df) < need:
        log(f"{ticker}: слишком мало свечей: {len(df)} "
            f"(нужно минимум {need}) — пропуск")
        return

    df = add_indicators(df, fast, slow, SIGNAL_LEN, RSI_LEN)

    curr = df.iloc[-1]
    prev = df.iloc[-2]
    before = df.iloc[-3]

    c_curr = hist_color(float(curr["hist"]), float(prev["hist"]))
    c_prev = hist_color(float(prev["hist"]), float(before["hist"]))

    candle_time = str(df.index[-1])  # время открытия свечи, UTC
    price = float(curr["Close"])
    hist_val = float(curr["hist"])
    sig_val = float(curr["signal"])
    rsi_val = float(curr["rsi"]) if pd.notna(curr["rsi"]) else float("nan")

    rsi_str = f"{rsi_val:.2f}" if pd.notna(rsi_val) else "n/a"
    log(f"{ticker} {tf} | свеча {candle_time} | цена {fmt_price(price)} | "
        f"signal({SIGNAL_LEN}) {sig_val:.2f} | hist {hist_val:.4f} | "
        f"RSI({RSI_LEN}) {rsi_str} | цвет: {c_curr}")

    # === ЛОГИКА СИГНАЛОВ ===
    # MACD:
    green_signal = (c_prev == "dark_green") and (c_curr == "light_green")
    green2_signal = (c_prev == "light_green") and (c_curr == "dark_green")
    red_signal = ((c_prev == "dark_red") and (c_curr == "light_red")
                  and (sig_val < 0))

    # RSI:
    rsi_overbought = pd.notna(rsi_val) and rsi_val > RSI_OVERBOUGHT
    rsi_oversold = pd.notna(rsi_val) and rsi_val < RSI_OVERSOLD

    # какие сигналы сработали
    fired = []
    if green_signal:
        fired.append("GREEN")
    if green2_signal:
        fired.append("GREEN2")
    if red_signal:
        fired.append("RED")
    if rsi_overbought:
        fired.append("RSI_OVERBOUGHT")
    if rsi_oversold:
        fired.append("RSI_OVERSOLD")

    if not fired:
        # диагностика как раньше
        if c_prev == "dark_red" and c_curr == "light_red" and sig_val >= 0:
            log(f"  Смена тёмно-красной -> светлокрасная есть, но signal "
                f"{sig_val:.2f} >= 0 — фильтр не пройден.")
        else:
            log("  Сигналов нет.")
        return

    # дедупликация: ключ на тикер+ТФ+направление
    for direction in fired:
        state_key = f"{ticker}_{tf}_{direction}"
        if state.get(state_key) == candle_time:
            log(f"  {direction} по свече {candle_time} уже отправлялся — пропуск.")
            continue

        # тексты
        if direction == "GREEN":
            note = "MACD: смена тёмно-зелёной -> светлозелёной (hist > 0, рост сменился падением)"
        elif direction == "GREEN2":
            note = "MACD: смена светлозелёной -> тёмно-зелёной (hist > 0, падение сменилось ростом)"
        elif direction == "RED":
            note = "MACD: смена тёмно-красной -> светло-красной (hist < 0), signal(9) < 0"
        elif direction == "RSI_OVERBOUGHT":
            note = f"RSI({RSI_LEN}) > {RSI_OVERBOUGHT} — перекупленность"
        else:  # RSI_OVERSOLD
            note = f"RSI({RSI_LEN}) < {RSI_OVERSOLD} — перепроданность"

        subject = f"[{direction}] {ticker} ({tf})"
        body = (
            f"Сигнал: {note}\n\n"
            f"Тикер:         {ticker}\n"
            f"Binance:       {to_binance_symbol(ticker)}\n"
            f"Таймфрейм:     {tf}\n"
            f"Свеча (UTC):   {candle_time}\n"
            f"Цена:          {fmt_price(price)}\n"
            f"Цвет MACD:     {COLOR_NAMES[c_curr]}\n"
            f"signal({SIGNAL_LEN}):     {sig_val:.4f}\n"
            f"hist:          {hist_val:.6f}\n"
            f"RSI({RSI_LEN}):       {rsi_str}\n\n"
            f"— MACD/RSI Alert Bot"
        )

        send_email(subject, body)
        state[state_key] = candle_time
        log(f"  EMAIL ОТПРАВЛЕН: {subject}")


def main() -> None:
    cfg = load_config()

    tickers = cfg.get("ticker", "BTC-USD")
    if isinstance(tickers, str):
        tickers = [tickers]

    tf = str(cfg.get("timeframe", "4h"))
    fast = int(cfg.get("macd_fast", cfg.get("fast", 12)))
    slow = int(cfg.get("macd_slow", cfg.get("slow", 26)))

    log(f"Тикеров: {len(tickers)} | ТФ: {tf} | "
        f"MACD({fast},{slow},{SIGNAL_LEN}) + RSI({RSI_LEN}) | "
        f"источник: Binance | ранний сигнал: за {LEAD_MINUTES} мин до закрытия")

    state = load_state()
    any_signal = False

    for ticker in tickers:
        try:
            before_keys = set(state.keys())
            process_ticker(str(ticker).strip(), tf, fast, slow, state)
            if set(state.keys()) != before_keys:
                any_signal = True
        except SystemExit:
            raise
        except Exception as e:
            log(f"{ticker}: ошибка — {e} (переходим к следующему)")
            continue

    if any_signal:
        save_state(state)
        commit_state_to_repo()
    log("Выход.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        log(f"Непредвиденная ошибка: {e}")
        sys.exit(1)
