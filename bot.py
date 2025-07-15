# Chandelier Exit alert bot – ligero, 100 % idéntico a TradingView
# Multi-símbolo – velas japonesas y Heikin Ashi – Alertas a Discord

import os, asyncio, logging, datetime as dt
from collections import deque
from dataclasses import dataclass
from typing import List, Dict, Deque, Optional

from dotenv import load_dotenv; load_dotenv()
from binance import AsyncClient, BinanceSocketManager
import httpx
from zoneinfo import ZoneInfo

# ─────────── Configuración ───────────

ATR_PERIOD    = 22
HISTORY_LIMIT = 200

CONFIGS = [
    {"symbol": "ETHUSDT", "interval": "5m",  "atr_mult": 4.0, "use_heikin": True},
    {"symbol": "ETHUSDT", "interval": "15m", "atr_mult": 3.0, "use_heikin": False},
    {"symbol": "ETHUSDT", "interval": "1h",  "atr_mult": 3.0, "use_heikin": False},
    {"symbol": "ETHUSDT", "interval": "4h",  "atr_mult": 4.0, "use_heikin": False},
    {"symbol": "ETHUSDT", "interval": "4h",  "atr_mult": 4.0, "use_heikin": True},
    {"symbol": "ETHUSDT", "interval": "1d",  "atr_mult": 4.0, "use_heikin": False},
    {"symbol": "ETHUSDT", "interval": "1d",  "atr_mult": 4.0, "use_heikin": True},

    {"symbol": "BTCUSDT", "interval": "4h",  "atr_mult": 4.0, "use_heikin": False},
    {"symbol": "BTCUSDT", "interval": "4h",  "atr_mult": 4.0, "use_heikin": True},
    {"symbol": "BTCUSDT", "interval": "1d",  "atr_mult": 4.0, "use_heikin": False},
    {"symbol": "BTCUSDT", "interval": "1d",  "atr_mult": 4.0, "use_heikin": True},
]

BINANCE_KEY         = os.getenv("BINANCE_KEY")
BINANCE_SECRET      = os.getenv("BINANCE_SECRET")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
LOCAL_TZ            = ZoneInfo("America/Argentina/Cordoba")

if not all([BINANCE_KEY, BINANCE_SECRET, DISCORD_WEBHOOK_URL]):
    raise RuntimeError("Configura BINANCE_KEY, BINANCE_SECRET y DISCORD_WEBHOOK_URL en tu archivo .env")

async def send_discord(txt: str) -> None:
    async with httpx.AsyncClient() as c:
        await c.post(DISCORD_WEBHOOK_URL, json={"content": txt})

@dataclass
class Candle:
    time:  int
    open:  float
    high:  float
    low:   float
    close: float

def compute_trend(candles: List[Candle], atr_mult: float) -> List[int]:
    n = len(candles)
    closes = [c.close for c in candles]
    highs  = [c.high  for c in candles]
    lows   = [c.low   for c in candles]

    tr, atr = [], [None] * n
    for i in range(n):
        prev_close = closes[i - 1] if i else closes[0]
        tr_val = max(
            highs[i] - lows[i],
            abs(highs[i] - prev_close),
            abs(lows[i] - prev_close),
        )
        tr.append(tr_val)
        if i == ATR_PERIOD - 1:
            atr[i] = sum(tr[:ATR_PERIOD]) / ATR_PERIOD
        elif i >= ATR_PERIOD:
            atr[i] = (atr[i - 1] * (ATR_PERIOD - 1) + tr[i]) / ATR_PERIOD

    long_stop, short_stop = [0.0] * n, [0.0] * n
    for i in range(ATR_PERIOD - 1, n):
        hi_closes = max(closes[i - ATR_PERIOD + 1 : i + 1])
        lo_closes = min(closes[i - ATR_PERIOD + 1 : i + 1])
        long_stop[i]  = hi_closes - atr_mult * atr[i]
        short_stop[i] = lo_closes + atr_mult * atr[i]

    long_adj, short_adj = long_stop[:], short_stop[:]
    trend = [1]
    for i in range(1, n):
        prev_long, prev_short = long_adj[i - 1], short_adj[i - 1]
        long_adj[i]  = max(long_stop[i],  prev_long)  if closes[i - 1] > prev_long  else long_stop[i]
        short_adj[i] = min(short_stop[i], prev_short) if closes[i - 1] < prev_short else short_stop[i]
        if closes[i] > prev_short:
            trend.append(1)
        elif closes[i] < prev_long:
            trend.append(-1)
        else:
            trend.append(trend[i - 1])
    return trend

def convert_to_heikin_ashi(candles: List[Candle]) -> List[Candle]:
    ha_candles = []
    for i, c in enumerate(candles):
        if i == 0:
            ha_open = (c.open + c.close) / 2
        else:
            prev = ha_candles[-1]
            ha_open = (prev.open + prev.close) / 2
        ha_close = (c.open + c.high + c.low + c.close) / 4
        ha_high = max(c.high, ha_open, ha_close)
        ha_low  = min(c.low, ha_open, ha_close)
        ha_candles.append(Candle(c.time, ha_open, ha_high, ha_low, ha_close))
    return ha_candles

class Stream:
    def __init__(self, client: AsyncClient, symbol: str, interval: str, atr_mult: float, use_heikin: bool = False):
        self.client, self.symbol, self.interval, self.atr_mult, self.use_heikin = client, symbol, interval, atr_mult, use_heikin
        self.candles: Deque[Candle] = deque(maxlen=HISTORY_LIMIT)
        self.last_alert: Optional[int] = None

    async def bootstrap(self) -> None:
        kl = await self.client.get_klines(symbol=self.symbol, interval=self.interval, limit=HISTORY_LIMIT)
        for k in kl:
            self.candles.append(Candle(int(k[6]), float(k[1]), float(k[2]), float(k[3]), float(k[4])))
        logging.info("Boot %s – %s (%d velas) – %s", self.symbol, self.interval, len(self.candles), "H" if self.use_heikin else "J")

    async def on_kline(self, msg: Dict) -> None:
        k = msg["k"]
        if not k["x"]:
            return
        self.candles.append(Candle(k["T"], float(k["o"]), float(k["h"]), float(k["l"]), float(k["c"])))
        candles = list(self.candles)
        if self.use_heikin:
            candles = convert_to_heikin_ashi(candles)

        trend = compute_trend(candles, self.atr_mult)
        if len(trend) < 2:
            return

        prev_dir, curr_dir = trend[-2], trend[-1]
        if curr_dir != prev_dir and curr_dir != self.last_alert:
            base_symbol = self.symbol.replace("USDT", "")
            candle = candles[-1]
            price = candle.close
            hora_local = dt.datetime.fromtimestamp(candle.time / 1000, LOCAL_TZ).strftime("%H:%M")
            tipo_vela = "HA" if self.use_heikin else f"J{int(self.atr_mult)}"

            # Mensaje personalizado para alertas
            if self.atr_mult == 4.0 and self.interval in ["4h", "1d"]:
                msg = f"{'🟢🟢' if curr_dir == 1 else '🔴🔴'} {base_symbol} {self.interval} ({tipo_vela}) {'🚀' if curr_dir == 1 else '🔻'} ${price:.2f} 🕒 {hora_local}"
            else:
                emoji = "🟩" if curr_dir == 1 else "🟥"
                icon  = "📈" if curr_dir == 1 else "📉"
                msg = f"{emoji} {base_symbol} {self.interval} ({tipo_vela}) {icon} ${price:.2f} 🕒 {hora_local}"

            await send_discord(msg)
            logging.info("%s", msg)
            self.last_alert = curr_dir

# Reconexión automática en caso de error
async def worker(stream: Stream):
    bsm = BinanceSocketManager(stream.client)
    while True:
        try:
            async with bsm.kline_socket(stream.symbol, stream.interval) as sock:
                logging.info("Conectado a %s %s", stream.symbol, stream.interval)
                while True:
                    msg = await sock.recv()
                    if msg is None:
                        raise ConnectionError("WebSocket cerrado inesperadamente")
                    await stream.on_kline(msg)
        except Exception as e:
            logging.error("🔁 Reconectando %s %s tras error: %s", stream.symbol, stream.interval, str(e))
            await asyncio.sleep(5)

async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    client = await AsyncClient.create(BINANCE_KEY, BINANCE_SECRET)

    streams = [Stream(client, cfg["symbol"], cfg["interval"], cfg["atr_mult"], cfg["use_heikin"]) for cfg in CONFIGS]
    for s in streams:
        await s.bootstrap()

    await asyncio.gather(*[asyncio.create_task(worker(s)) for s in streams])

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nDetenido por el usuario")
