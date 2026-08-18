#!/usr/bin/env python3
"""
S&P 500 trend/support signal -> Telegram (enhanced)

Signal components (bullish/bearish vote each):
  1. Short-term  : 20 EMA vs 40 EMA        (~1 month)
  2. Medium-term : 50 SMA vs 150 SMA       (~7 months)
  3. Long-term   : 200 SMA slope           (~10 months)
  4. MACD        : histogram rising/falling (momentum shift)
  5. Volume      : 5d avg vol vs 20d avg vol (participation confirming the move)

Plus context (not scored, just displayed):
  - RSI(14) — overbought/oversold flag
  - Golden Cross / Death Cross — 50 SMA crossing 200 SMA in the last 20 sessions
  - Support levels (20 EMA / 50 SMA / 100 SMA) + distance from current price

Reliability:
  - fetch_data() and send_telegram() each retry up to 3x with backoff
  - on unrecoverable failure, best-effort attempt to notify Telegram of the error
    (separate from the main send, so a notify failure doesn't mask the real one)

Credentials:
  Local runs      -> .env file via python-dotenv (never commit .env to git)
  GitHub Actions  -> repo Secrets, injected as real env vars (no .env in CI)
"""

import os
import sys
import time
import traceback
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

SGT = ZoneInfo("Asia/Singapore")

import requests
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()  # no-op if no .env file present (e.g. in CI)

TICKER = os.environ.get("TICKER", "^GSPC")
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "600"))  # ~1.6yr, comfortably covers 200 SMA + slope check

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5  # doubles each attempt: 5s, 10s, 20s


def with_retries(fn, *args, **kwargs):
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_err = e
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
                print(f"[retry] {fn.__name__} attempt {attempt} failed ({e}); retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
    raise last_err


def _fetch_data():
    hist = yf.Ticker(TICKER).history(period=f"{LOOKBACK_DAYS}d", interval="1d", auto_adjust=False)
    if hist.empty:
        raise RuntimeError(f"No data returned for {TICKER}")
    return hist


def fetch_data():
    return with_retries(_fetch_data)


def slope_up(series, lookback=5):
    if len(series) <= lookback:
        return None
    return bool(series.iloc[-1] > series.iloc[-1 - lookback])


def compute_rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_macd(close, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def detect_cross(sma_fast, sma_slow, lookback=20):
    """Returns 'golden', 'death', or None if a crossover happened within `lookback` sessions."""
    if len(sma_fast) <= lookback or len(sma_slow) <= lookback:
        return None
    recent_fast = sma_fast.tail(lookback + 1)
    recent_slow = sma_slow.tail(lookback + 1)
    diff = recent_fast - recent_slow
    sign_changes = (diff.shift(1) * diff) < 0
    if not sign_changes.any():
        return None
    last_change_idx = sign_changes[sign_changes].index[-1]
    return "golden" if diff.loc[last_change_idx] > 0 else "death"


def build_signal(hist):
    close = hist["Close"]
    volume = hist["Volume"]

    ema20 = close.ewm(span=20, adjust=False).mean()
    ema40 = close.ewm(span=40, adjust=False).mean()
    sma50 = close.rolling(50).mean()
    sma100 = close.rolling(100).mean()
    sma150 = close.rolling(150).mean()
    sma200 = close.rolling(200).mean()

    rsi = compute_rsi(close)
    macd_line, macd_signal, macd_hist = compute_macd(close)

    last_close = close.iloc[-1]
    last_date = hist.index[-1].strftime("%Y-%m-%d")

    have_150 = len(close) >= 150
    have_200 = len(close) >= 200

    # --- 5 scored components ---
    short_bullish = bool(ema20.iloc[-1] > ema40.iloc[-1])
    medium_bullish = bool(sma50.iloc[-1] > sma150.iloc[-1]) if have_150 else None
    long_bullish = slope_up(sma200, 10) if have_200 else None
    macd_bullish = bool(macd_hist.iloc[-1] > macd_hist.iloc[-5]) if len(macd_hist) > 5 else None

    vol_5d = volume.tail(5).mean()
    vol_20d = volume.tail(20).mean()
    volume_confirm = bool(vol_5d > vol_20d) if len(volume) >= 20 else None

    components = [short_bullish, medium_bullish, long_bullish, macd_bullish, volume_confirm]
    scored = [c for c in components if c is not None]
    bull_votes = sum(1 for c in scored if c is True)
    total_votes = len(scored)

    if total_votes == 0:
        overall = "N/A"
    else:
        ratio = bull_votes / total_votes
        if bull_votes == total_votes:
            overall = "STRONG BULLISH"
        elif ratio >= 0.6:
            overall = "BULLISH"
        elif ratio > 0.4:
            overall = "MIXED"
        elif bull_votes == 0:
            overall = "STRONG BEARISH"
        else:
            overall = "BEARISH"

    # --- context (not scored) ---
    last_rsi = rsi.iloc[-1] if not rsi.isna().all() else None
    if last_rsi is None or last_rsi != last_rsi:  # NaN check
        rsi_label = "n/a"
    elif last_rsi >= 70:
        rsi_label = f"{last_rsi:.0f} (Overbought)"
    elif last_rsi <= 30:
        rsi_label = f"{last_rsi:.0f} (Oversold)"
    else:
        rsi_label = f"{last_rsi:.0f} (Neutral)"

    cross = detect_cross(sma50, sma200) if have_200 else None
    cross_label = {"golden": "Golden Cross (last 20d)", "death": "Death Cross (last 20d)", None: "None recent"}[cross]

    lookback_high_window = close.tail(min(len(close), 252))
    rolling_high = lookback_high_window.max()
    drawdown_pct = (last_close / rolling_high - 1) * 100

    support_levels = {
        "20 EMA": ema20.iloc[-1],
        "50 SMA": sma50.iloc[-1],
        "100 SMA": sma100.iloc[-1] if len(close) >= 100 else None,
    }

    return {
        "date": last_date,
        "ticker": TICKER,
        "last_close": last_close,
        "overall": overall,
        "bull_votes": bull_votes,
        "total_votes": total_votes,
        "drawdown_pct": drawdown_pct,
        "short_bullish": short_bullish,
        "medium_bullish": medium_bullish,
        "long_bullish": long_bullish,
        "macd_bullish": macd_bullish,
        "volume_confirm": volume_confirm,
        "rsi_label": rsi_label,
        "cross_label": cross_label,
        "support_levels": support_levels,
    }


def trend_word(x):
    if x is True:
        return "Up"
    if x is False:
        return "Down"
    return "n/a"


def yes_no(x):
    if x is True:
        return "Yes"
    if x is False:
        return "No"
    return "n/a"


def build_message(sig):
    lines = []
    sgt_date = datetime.now(timezone.utc).astimezone(SGT).strftime("%Y-%m-%d")
    lines.append(f"S&P 500 — {sgt_date}")
    lines.append(f"Close: {sig['last_close']:,.2f}  ({sig['drawdown_pct']:+.2f}% from 52w high)")
    lines.append(f"Signal: {sig['overall']} ({sig['bull_votes']}/{sig['total_votes']} bullish)")
    lines.append("")
    lines.append(f"Short-term (~1mo): {trend_word(sig['short_bullish'])}")
    lines.append(f"Medium-term (~7mo): {trend_word(sig['medium_bullish'])}")
    lines.append(f"Long-term (~10mo): {trend_word(sig['long_bullish'])}")
    lines.append(f"MACD momentum: {trend_word(sig['macd_bullish'])}")
    lines.append(f"Volume confirming: {yes_no(sig['volume_confirm'])}")
    lines.append("")
    lines.append(f"RSI(14): {sig['rsi_label']}")
    lines.append(f"Cross: {sig['cross_label']}")
    lines.append("")
    support_vals = [v for v in sig["support_levels"].values() if v is not None]
    lines.append("Support: " + " / ".join(f"{v:,.0f}" for v in support_vals))
    return "\n".join(lines)


def _send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set (.env locally, Secrets in CI)")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=20)
    if not resp.ok:
        raise RuntimeError(f"Telegram API error {resp.status_code}: {resp.text}")
    return resp.json()


def send_telegram(text):
    return with_retries(_send_telegram, text)


def notify_failure(err):
    """Best-effort: try once (no retry loop) to tell Telegram the run failed."""
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        short_err = str(err)[:300]
        _send_telegram(f"S&P 500 signal FAILED to run: {short_err}")
    except Exception as notify_err:
        print(f"[warn] also failed to send failure notice: {notify_err}", file=sys.stderr)


def main():
    hist = fetch_data()
    sig = build_signal(hist)
    message = build_message(sig)
    print(message)
    send_telegram(message)
    print(f"\nSent at {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        traceback.print_exc()
        notify_failure(e)
        sys.exit(1)