#!/usr/bin/env python3
"""
Chandelier Exit alert bot for Telegram (free‑tier friendly)
-----------------------------------------------------------
Symbol:  ETHUSDT  (Binance spot)
Timeframes monitored:
  • 1‑minute  – Japanese candles (ATR ×4)
  • 1‑minute  – Heikin‑Ashi    (ATR ×4)
  • 3‑minute  – Japanese       (ATR ×4)
  • 5‑minute  – Japanese       (ATR ×4)
  • 15‑minute – Japanese       (ATR ×3)

The bot streams live klines from Binance, recreates the Chandelier Exit logic exactly
as in the Pine Script you provided (ATR period 22, dynamic stop adjustment, colour
flip detection) and sends a Telegram message **the moment the candle closes and the
trend flips from long 🟢 to short 🔴, or vice‑versa.**

Environment variables required:
  BINANCE_KEY     – Binance API key (read‑only permissions are fine)
  BINANCE_SECRET  – Binance API secret
  TG_TOKEN        – Telegram bot token (from @BotFather)
  TG_CHAT         – your chat ID (positive for personal chat, negative for channel)

Dependencies:
  python‑binance
  pandas
  ta  (technical analysis)
  httpx  (async HTTP for Telegram API)

$ python -m venv venv && source venv/bin/activate
(venv) $ pip install python-binance pandas ta httpx
(venv) $ export BINANCE_KEY="…" BINANCE_SECRET="…" TG_TOKEN="…" TG_CHAT="…"
(venv) $ python chandelier_alert_bot.py

Run it on your laptop, a Raspberry Pi, or any free container host (Render, Fly.io,
Railway, etc.). The process is fully async and lightweight (<50 MB RAM, <0.1 vCPU).
"""

import os
from dotenv import load_dotenv
load_dotenv()
import asyncio
import logging
import datetime as dt
from typing import List, Dict

import pandas as pd
from ta.volatility import AverageTrueRange
from binance import AsyncClient, BinanceSocketManager
import httpx

# ────────────────────────── Configuration ──────────────────────────
SYMBOL = "ETHUSDT"
ATR_PERIOD = 22        # fixed per user spec
HISTORY_LIMIT = 200    # candles kept in memory per stream (needs ≥ ATR_PERIOD)

STREAMS: List[Dict] = [
    {"interval": "1m",  "heikin": False, "atr_mult": 4.0},  # Japanese
    {"interval": "1m",  "heikin": True,  "atr_mult": 4.0},  # Heikin‑Ashi
    {"interval": "3m",  "heikin": False, "atr_mult": 4.0},
    {"interval": "5m",  "heikin": False, "atr_mult": 4.0},
    {"interval": "15m", "heikin": False, "atr_mult": 3.0},
]

# ─────────────────────────── Telegram helper ───────────────────────────
BINANCE_KEY    = os.getenv("BINANCE_KEY")
BINANCE_SECRET = os.getenv("BINANCE_SECRET")
TG_TOKEN       = os.getenv("TG_TOKEN")
TG_CHAT        = os.getenv("TG_CHAT")

if not all([BINANCE_KEY, BINANCE_SECRET, TG_TOKEN, TG_CHAT]):
    raise RuntimeError("Please set BINANCE_KEY, BINANCE_SECRET, TG_TOKEN and TG_CHAT env vars.")

async def send_telegram(text: str) -> None:
    """Async POST to Telegram Bot API."""
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    async with httpx.AsyncClient() as client:
        try:
            await client.post(url, json={"chat_id": TG_CHAT, "text": text})
        except Exception as exc:
            logging.error("Telegram send failed: %s", exc)

# ──────────────────────────── TA utilities ────────────────────────────

def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """Return a new DF with Heikin‑Ashi OHLC columns (open/high/low/close)."""
    ha = df.copy()
    ha_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4.0
    ha_open = [(df["open"].iloc[0] + df["close"].iloc[0]) / 2.0]
    for i in range(1, len(df)):
        ha_open.append((ha_open[i - 1] + ha_close.iloc[i - 1]) / 2.0)
    ha["open"] = ha_open
    ha["close"] = ha_close
    ha["high"] = pd.concat([ha["open"], ha_close, df["high"]], axis=1).max(axis=1)
    ha["low"]  = pd.concat([ha["open"], ha_close, df["low"]], axis=1).min(axis=1)
    return ha[["open", "high", "low", "close"]]


def compute_trend(df: pd.DataFrame, atr_mult: float) -> pd.Series:
    """Return a Series with Chandelier Exit trend direction (+1 long / ‑1 short)."""
    h, l, c = df["high"], df["low"], df["close"]
    atr = AverageTrueRange(high=h, low=l, close=c, window=ATR_PERIOD).average_true_range().bfill()

    long_stop = c.rolling(ATR_PERIOD).max() - atr_mult * atr
    short_stop = c.rolling(ATR_PERIOD).min() + atr_mult * atr

    long_adj = long_stop.copy()
    short_adj = short_stop.copy()
    trend = [1]  # start long by convention

    for i in range(1, len(df)):
        prev_long, prev_short = long_adj.iloc[i - 1], short_adj.iloc[i - 1]
        # Dynamic stop adjustment (same as Pine)
        long_adj.iloc[i]  = max(long_stop.iloc[i], prev_long)  if c.iloc[i - 1] > prev_long  else long_stop.iloc[i]
        short_adj.iloc[i] = min(short_stop.iloc[i], prev_short) if c.iloc[i - 1] < prev_short else short_stop.iloc[i]
        # Direction update
        if c.iloc[i] > prev_short:
            trend.append(1)
        elif c.iloc[i] < prev_long:
            trend.append(-1)
        else:
            trend.append(trend[i - 1])

    return pd.Series(trend, index=df.index, dtype=int)

# ───────────────────────────── Stream class ────────────────────────────
class Stream:
    def __init__(self, client: AsyncClient, symbol: str, interval: str, heikin: bool, atr_mult: float):
        self.client = client
        self.symbol = symbol
        self.interval = interval
        self.heikin = heikin
        self.atr_mult = atr_mult
        self.df: pd.DataFrame | None = None
        self.last_direction: int | None = None
        self.last_alert_direction = None  # Recordar la última señal enviada (1 o -1)

    # ───── Initial history ─────
    async def bootstrap(self) -> None:
        kl = await self.client.get_klines(symbol=self.symbol, interval=self.interval, limit=HISTORY_LIMIT)
        self.df = self._klines_to_df(kl)
        if self.heikin:
            self.df[["open", "high", "low", "close"]] = heikin_ashi(self.df)
        self.last_direction = compute_trend(self.df, self.atr_mult).iloc[-1]
        logging.info("Bootstrapped %s %s (%s) – initial dir: %s", self.symbol, self.interval, "HA" if self.heikin else "Jap", self.last_direction)

    @staticmethod
    def _klines_to_df(klines) -> pd.DataFrame:
        cols = [
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "qav", "n_trades", "taker_base", "taker_quote", "ignore",
        ]
        df = pd.DataFrame(klines, columns=cols, dtype=float)
        df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
        return df[["close_time", "open", "high", "low", "close"]]

    # ───── Handle live kline close ─────
    async def on_kline(self, msg: Dict) -> None:
        k = msg["k"]
        if not k["x"]:  # only act on closed candles
            return

        row = {
            "close_time": dt.datetime.fromtimestamp(k["T"] / 1_000),
            "open": float(k["o"]),
            "high": float(k["h"]),
            "low": float(k["l"]),
            "close": float(k["c"]),
        }
        self.df = pd.concat([self.df, pd.DataFrame([row])]).tail(HISTORY_LIMIT)

        if self.heikin:
            self.df[["open", "high", "low", "close"]] = heikin_ashi(self.df)

        trend = compute_trend(self.df, self.atr_mult)
        curr_dir, prev_dir = trend.iloc[-1], trend.iloc[-2]

        # Evitar alertas repetidas para mismo cambio
        if curr_dir != prev_dir and curr_dir != self.last_alert_direction:
            direction_text = "🟢 LONG" if curr_dir == 1 else "🔴 SHORT"
            price = row["close"]
            kind = "Heikin Ashi" if self.heikin else "japonesa"
            txt = f"Chandelier Exit {direction_text} en {self.symbol} ({self.interval}, {kind}) – Cierre: {price:.2f}"
            await send_telegram(txt)
            logging.info("Alert sent: %s", txt)
            self.last_alert_direction = curr_dir  # actualizar sólo si envía alerta

        self.last_direction = curr_dir

# ─────────────────────────── Socket worker ────────────────────────────
async def stream_worker(stream: Stream):
    bsm = BinanceSocketManager(stream.client)
    async with bsm.kline_socket(stream.symbol, stream.interval) as sock:
        while True:
            msg = await sock.recv()
            if msg is None:
                break
            await stream.on_kline(msg)

# ─────────────────────────────── main() ────────────────────────────────
async def main():
    logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)

    client = await AsyncClient.create(BINANCE_KEY, BINANCE_SECRET)
    streams: List[Stream] = []
    for cfg in STREAMS:
        st = Stream(client, SYMBOL, cfg["interval"], cfg["heikin"], cfg["atr_mult"])
        await st.bootstrap()
        streams.append(st)

    tasks = [asyncio.create_task(stream_worker(st)) for st in streams]
    logging.info("Bot started – monitoring %d streams…", len(tasks))
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped by user")
