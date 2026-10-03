#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MACD Alert Bot (multi-ticker, кастомные ТФ через ресемплинг)
===========================================================
Сигнал на последней ЗАКРЫТОЙ свече при ОДНОВРЕМЕННОМ выполнении двух условий:

  GREEN: смена тёмно-зелёной -> светлозелёной  (hist > 0: рост сменился падением)
         И сигнальная линия signal (период 9) >= 190

  RED:   смена тёмно-красной -> светло-красной  (hist < 0: падение сменилось ростом)
         И сигнальная линия signal (период 9) <= -160

Особенности:
  - несколько тикеров (массив в config.json), по каждому независимый анализ;
  - ТФ 4h получается ресемплингом часовых свечей (yfinance не отдаёт 4h);
  - незакрытая свеча отбрасывается — сигнал не перерисуется;
  - дедупликация на тикер через state.json (ключ: "TICKER_TF");
  - источник данных — Yahoo Finance (yfinance).

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
import yfinance as yf

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "state.json"

# ==== ПАРАМЕТРЫ СИГНАЛА (намертво, не в config.json) ====
SIGNAL_LEN = 9          # длина сигнальной линии MACD
SIGNAL_GREEN_MIN = 190.0   # signal(9) >= 190  для GREEN
SIGNAL_RED_MAX = -160.0    # signal(9) <= -160 для RED
# =======================================================

# длительность таймфрейма в секундах
TF_SECONDS = {
    "1m": 60, "2m": 120, "5m": 300, "15m": 900, "30m": 1800,
    "60m": 3600, "90m": 5400, "1h": 3600, "2h": 7200, "4h": 14400,
    "6h": 21600, "8h": 28800, "12h": 43200, "1d": 86400,
    "3d": 259200, "1w": 604800,
}

# какой интервал реально запрашивать у Yahoo под каждый ТФ
# (yfinance не поддерживает 2h/4h/6h/8h/12h -> берём 1h и ресемплим)
SOURCE_INTERVAL = {
    "1m": "1m", "2m": "2m", "5m": "5m", "15m": "15m", "30m": "30m",
    "60m": "60m", "90m": "90m", "1h": "1h",
    "2h": "1h", "4h": "1h", "6h": "1h", "8h": "1h", "12h": "1h",
    "1d": "1d", "3d": "1d", "1w": "1wk",
}

# сколько истории запрашивать у Yahoo под каждый ТФ
PERIOD_BY_TF = {
    "1m": "1d", "2m": "5d", "5m": "5d", "15m": "1mo", "30m": "1mo",
    "60m": "1mo", "90m": "3mo", "1h": "1mo", "2h": "3mo", "4h": "3mo",
    "6h": "3mo", "8h": "6mo", "12h": "6mo", "1d": "1y", "3d": "1y", "1w": "2y",
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
    tf = str(cfg.get("timeframe", "1h"))
    if tf not in TF_SECONDS:
        fail(f"Неизвестный timeframe '{tf}'. Допустимые: {', '.join(TF_SECONDS)}")
    return cfg


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
    """Загружает свечи. Для ТФ, которых нет в yfinance (2h/4h/...),
    качает 1h и ресемплирует до нужного ТФ."""
    period = PERIOD_BY_TF.get(timeframe, "1mo")
    src = SOURCE_INTERVAL.get(timeframe, timeframe)
    log(f"Загрузка {ticker} {timeframe} (период {period}, источник {src})...")
    df = yf.download(ticker, period=period, interval=src,
                     progress=False, auto_adjust=False)
    if df is None or df.empty:
        raise RuntimeError(f"Нет данных по {ticker} ({timeframe}) — проверьте тикер")
    if isinstance(df.columns, pd.MultiIndex):  # новые версии yfinance
        df.columns = df.columns.get_level_values(0)
    # приводим индекс к UTC — иначе ресемплинг и drop_unclosed работают
    # в разных часовых поясах
    if df.index.tzinfo is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    if src != timeframe:
        df = resample_to_tf(df, timeframe)
    return df


def resample_to_tf(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Ресемплинг младших свечей (например 1h) в старший ТФ (например 4h).
    Бары привязаны к 00:00 UTC, как на TradingView для крипты."""
    tf = pd.Timedelta(seconds=TF_SECONDS[timeframe])
    out = pd.DataFrame({
        "Open":   df["Open"].resample(tf, origin="epoch").first(),
        "High":   df["High"].resample(tf, origin="epoch").max(),
        "Low":    df["Low"].resample(tf, origin="epoch").min(),
        "Close":  df["Close"].resample(tf, origin="epoch").last(),
        "Volume": df["Volume"].resample(tf, origin="epoch").sum(),
    }).dropna(subset=["Close"])
    return out


def drop_unclosed(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Если последняя свеча ещё не закрылась (по времени), отбрасываем её."""
    tf = pd.Timedelta(seconds=TF_SECONDS[timeframe])
    last_open = df.index[-1]
    if last_open.tzinfo is None:
        last_open = last_open.tz_localize("UTC")
    now = pd.Timestamp.now(tz="UTC")
    if now < last_open + tf:
        log(f"Последняя свеча ({timeframe}) ещё не закрылась — пропускаем её")
        return df.iloc[:-1]
    return df


def add_macd(df: pd.DataFrame, fast: int, slow: int, signal_len: int) -> pd.DataFrame:
    close = df["Close"]
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    sig_line = macd_line.ewm(span=signal_len, adjust=False).mean()
    out = df.copy()
    out["macd"] = macd_line
    out["signal"] = sig_line
    out["hist"] = macd_line - sig_line
    return out


def hist_color(hist: float, hist_prev: float) -> str:
    if hist > 0 and hist < hist_prev:   # выше нуля и падает
        return "light_green"
    if hist > 0:                        # выше нуля и растёт
        return "dark_green"
    if hist < 0 and hist > hist_prev:   # ниже нуля и растёт
        return "light_red"
    return "dark_red"                   # ниже нуля и падает


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
    """Анализ одного тикера. Ошибки по одному тикеру не роняют остальные."""
    df = fetch_data(ticker, tf)
    df = drop_unclosed(df, tf)

    need = slow + SIGNAL_LEN + 5
    if len(df) < need:
        log(f"{ticker}: слишком мало закрытых свечей: {len(df)} "
            f"(нужно минимум {need}) — пропуск")
        return

    df = add_macd(df, fast, slow, SIGNAL_LEN)

    curr = df.iloc[-1]
    prev = df.iloc[-2]
    before = df.iloc[-3]

    c_curr = hist_color(float(curr["hist"]), float(prev["hist"]))
    c_prev = hist_color(float(prev["hist"]), float(before["hist"]))

    candle_time = str(df.index[-1])  # вида 2026-10-03 12:00:00+00:00
    price = float(curr["Close"])
    hist_val = float(curr["hist"])
    sig_val = float(curr["signal"])

    log(f"{ticker} {tf} | закрытая свеча {candle_time} | цена {price:.2f} | "
        f"signal({SIGNAL_LEN}) {sig_val:.2f} | hist {hist_val:.4f} | цвет: {c_curr}")

    # смена цвета (тёмная -> светлая) + фильтр по сигнальной линии
    flip_green = (c_prev == "dark_green") and (c_curr == "light_green")
    flip_red = (c_prev == "dark_red") and (c_curr == "light_red")
    green_signal = flip_green and (sig_val >= SIGNAL_GREEN_MIN)
    red_signal = flip_red and (sig_val <= SIGNAL_RED_MAX)

    if not (green_signal or red_signal):
        if flip_green:
            log(f"  Смена тёмно-зелёной -> светлозелёная есть, но signal "
                f"{sig_val:.2f} < {SIGNAL_GREEN_MIN:.2f} — фильтр не пройден.")
        elif flip_red:
            log(f"  Смена тёмно-красной -> светло-красная есть, но signal "
                f"{sig_val:.2f} > {SIGNAL_RED_MAX:.2f} — фильтр не пройден.")
        else:
            log("  Сигнала нет.")
        return

    # защита от повторной отправки по той же свече — КЛЮЧ НА ТИКЕР
    state_key = f"{ticker}_{tf}"
    if state.get(state_key) == candle_time:
        log(f"  Сигнал по свече {candle_time} уже отправлялся — пропуск.")
        return

    direction = "GREEN" if green_signal else "RED"
    subject = f"[{direction}] MACD {ticker} ({tf})"
    body = (
        f"Сигнал MACD по вашей логике.\n\n"
        f"Тикер:         {ticker}\n"
        f"Таймфрейм:     {tf}\n"
        f"Свеча (UTC):   {candle_time}\n"
        f"Цена закрытия: {price:.2f}\n"
        f"Смена:         {COLOR_NAMES[c_prev]} -> {COLOR_NAMES[c_curr]}\n"
        f"signal({SIGNAL_LEN}):     {sig_val:.4f}\n"
        f"hist:          {hist_val:.6f}\n\n"
        f"— MACD Alert Bot"
    )

    send_email(subject, body)

    state[state_key] = candle_time
    state[f"{state_key}_last"] = {
        "direction": direction,
        "time_utc": candle_time,
        "price": price,
        "signal": sig_val,
        "hist": hist_val,
    }
    log(f"  EMAIL ОТПРАВЛЕН: {subject}")


def main() -> None:
    cfg = load_config()

    # тикер может быть строкой или массивом
    tickers = cfg.get("ticker", "BTC-USD")
    if isinstance(tickers, str):
        tickers = [tickers]

    tf = str(cfg.get("timeframe", "1h"))
    fast = int(cfg.get("macd_fast", cfg.get("fast", 12)))
    slow = int(cfg.get("macd_slow", cfg.get("slow", 26)))

    log(f"Тикеров в обработке: {len(tickers)} | ТФ: {tf} | "
        f"MACD({fast},{slow},{SIGNAL_LEN}) | "
        f"пороги: GREEN>={SIGNAL_GREEN_MIN}, RED<={SIGNAL_RED_MAX}")

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
