"""
Alpaca-Integration: LLM-gesteuerte US-Aktien-Strategie.

Ablauf je Scan (alle 15 Min während Marktzeiten):
  1. Technische Indikatoren aus Alpaca-Bars berechnen (RSI, EMA20/50, BB)
  2. Sentiment-Block laden (Fear&Greed, Polymarket, Tavily-News)
  3. LLM-Entscheidung → buy / sell / hold
  4. Position öffnen oder schließen
"""
import asyncio
import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
from alpaca.data import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from app.config import (
    ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_PAPER, ALPACA_STAKE_USD,
    OPENROUTER_API_KEY, OPENROUTER_MODEL,
)

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# Watchlist: liquide US-Aktien + Makro-sensitive ETFs
WATCHLIST: list[str] = [
    "SPY",   # S&P 500
    "QQQ",   # Nasdaq
    "GLD",   # Gold
    "AAPL",  # Apple
    "MSFT",  # Microsoft
    "NVDA",  # Nvidia
    "TSLA",  # Tesla
    "XLF",   # Financials (Fed-sensitiv)
    "USO",   # Öl
]

BUY_CONFIDENCE  = 0.72
SELL_CONFIDENCE = 0.72
MAX_POSITIONS   = 3

SYSTEM_PROMPT = """Du bist ein erfahrener US-Aktien-Trader. Erkenne TRENDUMKEHRPUNKTE und MOMENTUM-ÄNDERUNGEN.

Antworte NUR mit validem JSON:
{"signal": "buy" | "sell" | "hold", "confidence": 0.0-1.0, "reason": "kurze Begründung"}

KAUF nur bei:
- RSI war unter 38, dreht jetzt aufwärts (min. 2 Kerzen)
- EMA20 kreuzt EMA50 von unten nach oben
- Preis berührt unteres Bollinger-Band und schließt darüber zurück
- Bullische Kerzenumkehr nach Abwärtsbewegung

VERKAUF nur bei:
- RSI war über 65, dreht jetzt abwärts
- EMA20 kreuzt EMA50 von oben nach unten
- Preis schließt unterhalb des oberen Bollinger-Bands zurück

Außerdem: Berücksichtige den Marktkontext (Fear&Greed, Polymarket, News) aus dem Prompt.
Confidence unter 0.70: immer "hold".
"""


# ── Indikatoren ───────────────────────────────────────────────────────────────

def _calc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    delta    = df["close"].diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    df["rsi"]    = 100 - (100 / (1 + gain.ewm(com=13, adjust=False).mean()
                                  / loss.ewm(com=13, adjust=False).mean()))
    df["ema20"]  = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"]  = df["close"].ewm(span=50, adjust=False).mean()
    roll         = df["close"].rolling(20)
    df["bb_mid"] = roll.mean()
    df["bb_upper"] = df["bb_mid"] + 2 * roll.std()
    df["bb_lower"] = df["bb_mid"] - 2 * roll.std()
    return df


# ── LLM ──────────────────────────────────────────────────────────────────────

def _build_prompt(symbol: str, df: pd.DataFrame, sentiment_block: str,
                  position: dict | None) -> str:
    last   = df.tail(50)
    latest = last.iloc[-1]

    candles = []
    for _, row in last.iterrows():
        candles.append(
            f"{row['timestamp'].strftime('%Y-%m-%d %H:%M')} | "
            f"O:{row['open']:.2f} H:{row['high']:.2f} L:{row['low']:.2f} C:{row['close']:.2f} "
            f"V:{row['volume']:.0f} | "
            f"RSI:{row['rsi']:.1f} EMA20:{row['ema20']:.2f} EMA50:{row['ema50']:.2f} "
            f"BB_u:{row['bb_upper']:.2f} BB_l:{row['bb_lower']:.2f}"
        )

    slope_20  = latest["close"] - last.iloc[-20]["close"]
    trend_dir = "aufwärts" if slope_20 > 0 else "abwärts"
    rsi_slope = latest["rsi"] - last.iloc[-3]["rsi"]
    rsi_dir   = "steigend" if rsi_slope > 0 else "fallend"
    ema_slope = latest["ema50"] - last.iloc[-6]["ema50"]

    pos_ctx = ""
    if position:
        profit_pct = (latest["close"] - float(position["avg_entry_price"])) / float(position["avg_entry_price"]) * 100
        sign = "+" if profit_pct >= 0 else ""
        pos_ctx = (
            f"\nOFFENE POSITION: Einstieg ${float(position['avg_entry_price']):.2f} | "
            f"Aktuell {sign}{profit_pct:.2f}% | Qty: {position['qty']} | "
            f"Bewerte ob VERKAUFT werden soll."
        )

    return (
        f"Symbol: {symbol} | Timeframe: 15m | {datetime.now(ET).strftime('%Y-%m-%d %H:%M')} ET\n"
        f"Trend (20 Kerzen): {trend_dir} | RSI: {rsi_dir} ({latest['rsi']:.1f}) | "
        f"EMA20 {'>' if latest['ema20'] > latest['ema50'] else '<'} EMA50 | "
        f"EMA50-Slope: {'aufwärts' if ema_slope > 0 else 'abwärts'}"
        f"{pos_ctx}\n\n"
        f"{sentiment_block}\n\n"
        f"Letzte 50 Kerzen (älteste zuerst):\n"
        + "\n".join(candles)
        + "\n\nUmkehrsignal vorhanden? Deine Entscheidung:"
    )


async def _call_llm(prompt: str) -> dict:
    try:
        from app.services.llm_client import chat
        from app.config import LLM_CHEAP_MODEL
        raw = (await chat(
            [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": prompt}],
            model=LLM_CHEAP_MODEL, temperature=0.1, max_tokens=150, timeout=20.0,
        )).strip()
        import json
        return json.loads(raw)
    except Exception as e:
        logger.warning("LLM call failed: %s", e)
        return {"signal": "hold", "confidence": 0.0, "reason": "LLM error"}


# ── Alpaca API ────────────────────────────────────────────────────────────────

def _get_clients():
    trading = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=ALPACA_PAPER)
    data    = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
    return trading, data


def is_market_open() -> bool:
    try:
        trading, _ = _get_clients()
        clock = trading.get_clock()
        return clock.is_open
    except Exception:
        return False


def _get_bars(data_client, symbol: str) -> pd.DataFrame | None:
    try:
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Minute * 15,
            limit=100,
        )
        bars = data_client.get_stock_bars(req)
        df   = bars.df
        if hasattr(df.index, "levels"):
            df = df.xs(symbol, level=0) if symbol in df.index.get_level_values(0) else df
        df = df.reset_index()
        if "timestamp" not in df.columns:
            df = df.rename(columns={df.columns[0]: "timestamp"})
        if len(df) < 60:
            return None
        return _calc_indicators(df)
    except Exception as e:
        logger.warning("Bars fetch failed for %s: %s", symbol, e)
        return None


def _get_positions(trading_client) -> dict[str, dict]:
    try:
        positions = trading_client.get_all_positions()
        return {p.symbol: {"qty": p.qty, "avg_entry_price": p.avg_entry_price,
                            "market_value": p.market_value, "unrealized_pl": p.unrealized_pl,
                            "unrealized_plpc": p.unrealized_plpc}
                for p in positions}
    except Exception:
        return {}


async def _place_order(trading_client, symbol: str, side: OrderSide, usd: float) -> bool:
    try:
        # Preis ermitteln für Qty-Berechnung
        data_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
        req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Minute, limit=1)
        bars = data_client.get_stock_bars(req)
        df = bars.df
        if hasattr(df.index, "levels"):
            df = df.xs(symbol, level=0)
        price = float(df["close"].iloc[-1])
        qty   = max(1, int(usd / price))

        order_req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.DAY,
        )
        trading_client.submit_order(order_req)
        logger.info("Order placed: %s %s x%d @ ~$%.2f", side.value, symbol, qty, price)
        return True
    except Exception as e:
        logger.warning("Order failed for %s: %s", symbol, e)
        return False


async def _close_position(trading_client, symbol: str) -> bool:
    try:
        trading_client.close_position(symbol)
        logger.info("Position closed: %s", symbol)
        return True
    except Exception as e:
        logger.warning("Close position failed for %s: %s", symbol, e)
        return False


# ── Haupt-Scan ────────────────────────────────────────────────────────────────

async def run_alpaca_scan(notify_fn=None) -> str:
    """
    Scannt alle WATCHLIST-Symbole, trifft LLM-Entscheidungen, führt Trades aus.
    notify_fn(text): optionaler Telegram-Callback für Trade-Meldungen.
    Returns: Zusammenfassung als Text.
    """
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        return "⚠️ Alpaca API-Key nicht konfiguriert."

    if not is_market_open():
        return "🕐 US-Markt ist aktuell geschlossen."

    try:
        from app.services.sentiment import build_sentiment_block, should_block_entry
    except ImportError:
        # Fallback wenn sentiment-Modul nicht verfügbar
        def build_sentiment_block(pair): return ""
        def should_block_entry(pair): return False, ""

    trading_client, data_client = _get_clients()
    positions = _get_positions(trading_client)
    open_count = len(positions)
    actions = []

    for symbol in WATCHLIST:
        df = _get_bars(data_client, symbol)
        if df is None:
            continue

        latest    = df.iloc[-1]
        ema_slope = latest["ema50"] - df.iloc[-6]["ema50"]
        position  = positions.get(symbol)

        # Exit-Check für offene Positionen
        if position:
            sentiment_block = build_sentiment_block(symbol)
            prompt  = _build_prompt(symbol, df, sentiment_block, position)
            result  = await _call_llm(prompt)
            signal  = result.get("signal", "hold")
            conf    = float(result.get("confidence", 0.0))
            reason  = result.get("reason", "")
            logger.info("[Alpaca] %s (offen) → %s (conf=%.2f) | %s", symbol, signal, conf, reason)

            if signal == "sell" and conf >= SELL_CONFIDENCE:
                ok = await _close_position(trading_client, symbol)
                if ok:
                    pl = float(position["unrealized_pl"])
                    pl_pct = float(position["unrealized_plpc"]) * 100
                    sign = "+" if pl >= 0 else ""
                    msg = (f"📤 *Alpaca Verkauf*\n`{symbol}` → {sign}{pl_pct:.2f}% "
                           f"({sign}${pl:.2f})\nGrund: {reason}")
                    actions.append(msg)
                    if notify_fn:
                        await notify_fn(msg)
            continue

        # Entry-Check
        if open_count >= MAX_POSITIONS:
            continue
        if ema_slope < 0:
            logger.info("[Alpaca] %s — Entry blockiert (EMA50 abwärts)", symbol)
            continue
        blocked, block_reason = should_block_entry(symbol)
        if blocked:
            logger.info("[Alpaca] %s — Entry blockiert: %s", symbol, block_reason)
            continue

        sentiment_block = build_sentiment_block(symbol)
        prompt  = _build_prompt(symbol, df, sentiment_block, None)
        result  = await _call_llm(prompt)
        signal  = result.get("signal", "hold")
        conf    = float(result.get("confidence", 0.0))
        reason  = result.get("reason", "")
        logger.info("[Alpaca] %s → %s (conf=%.2f) | %s", symbol, signal, conf, reason)

        if signal == "buy" and conf >= BUY_CONFIDENCE:
            ok = await _place_order(trading_client, symbol, OrderSide.BUY, ALPACA_STAKE_USD)
            if ok:
                open_count += 1
                msg = (f"🟢 *Alpaca Kauf*\n`{symbol}` @ ~${latest['close']:.2f}\n"
                       f"Einsatz: ${ALPACA_STAKE_USD:.0f} | Conf: {conf:.2f}\nGrund: {reason}")
                actions.append(msg)
                if notify_fn:
                    await notify_fn(msg)

    if not actions:
        return f"🔍 Alpaca-Scan abgeschlossen — kein Signal ({len(positions)} offene Positionen)."
    return "\n\n".join(actions)


# ── Status-Report ─────────────────────────────────────────────────────────────

async def fetch_alpaca_status() -> str:
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        return "⚠️ Alpaca API-Key nicht konfiguriert.\nBitte `ALPACA_API_KEY` und `ALPACA_SECRET_KEY` in `.env` setzen."

    try:
        trading_client, _ = _get_clients()
        account   = trading_client.get_account()
        positions = _get_positions(trading_client)
        clock     = trading_client.get_clock()

        mode      = "📄 Paper" if ALPACA_PAPER else "💵 Live"
        equity    = float(account.equity)
        cash      = float(account.cash)
        day_pl    = float(account.equity) - float(account.last_equity)
        day_sign  = "+" if day_pl >= 0 else ""
        market_st = "🟢 Offen" if clock.is_open else "🔴 Geschlossen"
        now       = datetime.now(ET).strftime("%d.%m.%Y %H:%M ET")

        lines = [
            f"📊 *Alpaca — {now}*",
            f"Modus: {mode} | Markt: {market_st}\n",
            f"💰 *Konto*",
            f"Equity: `${equity:,.2f}`  |  Cash: `${cash:,.2f}`",
            f"Tages-P&L: `{day_sign}${day_pl:,.2f}`\n",
            f"📈 *Offene Positionen: {len(positions)}*",
        ]

        for sym, pos in positions.items():
            pl_pct = float(pos["unrealized_plpc"]) * 100
            pl_abs = float(pos["unrealized_pl"])
            icon   = "✅" if pl_pct >= 0 else "❌"
            sign   = "+" if pl_pct >= 0 else ""
            lines.append(f"  {icon} `{sym}`: `{sign}{pl_pct:.2f}%` ({sign}${pl_abs:.2f})")

        return "\n".join(lines)

    except Exception as e:
        logger.warning("Alpaca Status fehlgeschlagen: %s", e)
        return f"⚠️ Alpaca nicht erreichbar: {e}"
