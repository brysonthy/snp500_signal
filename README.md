# S\&P 500 Signal Engine

Python-based S\&P 500 trend and technical signal engine that analyzes daily market data and sends multi-factor bullish/bearish signals to Telegram.

## Features

* 5-factor signal scoring: EMA, SMA, MACD and volume
* RSI, Golden/Death Cross, drawdown and support levels
* Telegram notifications with retry handling
* Local `.env` and GitHub Actions Secrets support

## Signal Engine

The engine evaluates 5 independent components, with each contributing one bullish or bearish vote.

|Component|Method|Purpose|
|-|-|-|
|Short-term|20 EMA vs 40 EMA|Identifies short-term trend|
|Medium-term|50 SMA vs 150 SMA|Identifies medium-term trend|
|Long-term|200 SMA slope|Identifies long-term trend direction|
|MACD|Histogram momentum|Detects momentum changes|
|Volume|5-day vs 20-day average|Checks participation/confirmation|

## Signal Classification

The bullish votes determine the overall signal:

|Result|Condition|
|-|-|
|STRONG BULLISH|5/5 bullish|
|BULLISH|≥60% bullish|
|MIXED|>40% and <60% bullish|
|BEARISH|≤40% bullish|
|STRONG BEARISH|0/5 bullish|

The signal is intended as a market-trend indicator, not a standalone trading recommendation.

## Tech Stack

Python · yfinance · pandas · Requests · Telegram Bot API · GitHub Actions

## Example Telegram Output

```text
S\&P 500 — 2026-08-18
Close: 6,411.37 (-1.25% from 52w high)
Signal: BULLISH (4/5 bullish)

Short-term (\~1mo): Up
Medium-term (\~7mo): Up
Long-term (\~10mo): Up
MACD momentum: Down
Volume confirming: Yes

RSI(14): 62 (Neutral)
Cross: None recent

Support: 6,250 / 6,180 / 5,980
```

## Architecture

```text
Yahoo Finance
     │
     ▼
Daily S\&P 500 Data
     │
     ▼
Technical Analysis
     │
     ├── 20/40 EMA
     ├── 50/150 SMA
     ├── 200 SMA Slope
     ├── MACD
     └── Volume
     │
     ▼
Signal Engine
     │
     ├── Bullish/Bearish Score
     ├── RSI
     ├── Cross Detection
     └── Support Levels
     │
     ▼
Telegram Notification
```

## Run Locally

Clone and run the repository:

```bash
git clone https://github.com/<username>/snp500\_signal.git
cd snp500\_signal-main
pip install -r requirements.txt
python sp500\_signal.py
```

### Configuration

Create a `.env` file:

```env
TELEGRAM\_BOT\_TOKEN=your\_bot\_token
TELEGRAM\_CHAT\_ID=your\_chat\_id
```

## GitHub Actions

For automated execution, configure these repository secrets:

```text
TELEGRAM\_BOT\_TOKEN
TELEGRAM\_CHAT\_ID
```

GitHub Actions can then run the signal engine on a schedule without exposing credentials in the source code.

### Example Workflow

```yaml
name: S\&P 500 Signal

on:
  schedule:
    - cron: "0 22 \* \* 1-5"
  workflow\_dispatch:

jobs:
  signal:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - run: pip install -r requirements.txt
      - run: python sp500\_signal.py
        env:
          TELEGRAM\_BOT\_TOKEN: ${{ secrets.TELEGRAM\_BOT\_TOKEN }}
          TELEGRAM\_CHAT\_ID: ${{ secrets.TELEGRAM\_CHAT\_ID }}
```

The cron schedule should be adjusted depending on the desired market-data availability and timezone.

## Reliability

External services can occasionally fail, so the application includes retry handling.

### Data Fetching

Yahoo Finance requests are retried up to 3 times using exponential backoff:

```text
Attempt 1 → failure
     ↓ 5s
Attempt 2 → failure
     ↓ 10s
Attempt 3 → failure
```

### Telegram

Telegram API requests use the same retry mechanism.

If the entire pipeline fails, the application makes a best-effort failure notification to Telegram and exits with status code `1`.

## Configuration Options

|Environment Variable|Default|Description|
|-|-|-|
|`TICKER`|`^GSPC`|Yahoo Finance ticker|
|`LOOKBACK\_DAYS`|`600`|Historical daily data to retrieve|
|`TELEGRAM\_BOT\_TOKEN`|—|Telegram bot token|
|`TELEGRAM\_CHAT\_ID`|—|Telegram destination|

## Project Structure

```text
sp500-signal-engine/
├── sp500\_signal.py
├── requirements.txt
├── .env.example
├── .gitignore
└── .github/
    └── workflows/
        └── signal.yml
```

## Disclaimer

For educational and research purposes only. Signals are rule-based and are not financial advice.

