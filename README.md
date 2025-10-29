<img width="1080" height="360" alt="image" src="https://github.com/user-attachments/assets/95b2d0e8-9a54-4e8b-aaf2-2222f9f60c38" />


This repository contains a Telegram bot that aggregates football betting odds from multiple bookmakers (via [The Odds API](https://the-odds-api.com)) and presents them in an intuitive flow: league → match day → match. For each upcoming fixture in the English Premier League, La Liga, and the Bundesliga, we compute implied probabilities, average them across sources, and highlight the statistically strongest outcome.

> **Polymarket eligibility:** Only users with a verified Polymarket account and at least one completed transaction should be allowed to access the bot’s insights. Gate-keeping must be enforced in the production deployment before exposing odds or recommendations.

### Prerequisites

1. **Python 3.10+**  
2. **Dependencies** listed in `requirements.txt`  
3. **The Odds API key** – create/rotate in your dashboard and keep it private  
4. **Telegram bot token** – obtained from [@polymarketFootballBot](@polymarket_Football_Bot)

### Quick Start (CLI demo)

```bash
cd betting-aggregator
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export THE_ODDS_API_KEY="your-odds-api-key"

# Fetch all supported leagues
python -m src.main

# Limit to specific leagues
python -m src.main --leagues epl la_liga

# Only show fixtures with active Polymarket markets
python -m src.main --polymarket-only
```

### Run the Telegram Bot

```bash
export THE_ODDS_API_KEY="your-odds-api-key"
export TELEGRAM_BOT_TOKEN="your-telegram-bot-token"

python -m src.bot
```

In Telegram, open your bot and use `/epl`, `/laliga`, or `/bundesliga` to browse match days and fixtures. Production instances must verify the caller’s Polymarket status before responding with odds.

### Output

- League, fixture, and kick-off time  
- Per-bookmaker implied probabilities (home / draw / away)  
- Average probabilities across sources  
- Highlighted recommendation with a confidence delta (difference between the top two averages)

### Notes

- Odds are pulled from the `h2h` (match winner) market, converted to implied probabilities, margin-normalised, and cached in memory.  
- Keep API keys out of the repository—store them in environment variables or a secret manager.  
- Respect The Odds API rate limits and quota associated with your plan.  
- Match-day grouping relies on the `commence_time` field provided by The Odds API.  
- Polymarket integration uses the public `/api/markets` endpoint; update `src/polymarket.py` if their response format changes.

### Roadmap / Future Work

1. **Polymarket verification** – integrate Polymarket authentication/transaction checks so only eligible bettors can access the bot.  
2. **Arbitrage module** – compare polymarket odds with other bookmakers and alert users whenever Polymarket deviates from consensus, enabling quicker hedging or early cash-outs.  
3. **Historical analytics** – persist snapshots, expose trends (volatility, spreads, consensus shifts), and surface them in the bot.  
4. **Alerting & watchlists** – allow users to subscribe to matches/teams and receive push notifications on significant line moves.  
5. **Infrastructure hardening** – add observability, background workers, and smarter rate-limiting before scaling to production traffic.
