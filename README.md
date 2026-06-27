# UNO Telegram Bot

2-4 player UNO, played in a group chat. Hands are sent privately via DM
with tappable inline-keyboard buttons — no typing card names.

## How it works
- Lobby + turn announcements: in the **group**.
- Your hand: in your **DM** with the bot, as buttons. Playable cards get a ✅.

## Setup

1. Get a bot token from [@BotFather](https://t.me/BotFather) on Telegram.
2. Copy `.env.example` to `.env` and paste your token:
   ```
   cp .env.example .env
   ```
3. Run with Docker:
   ```
   docker compose up -d --build
   ```
   Or locally without Docker:
   ```
   pip install -r requirements.txt --break-system-packages
   export BOT_TOKEN=xxxx
   python bot.py
   ```

## Mobile-only deployment (no PC, no terminal) — Render free tier

Render's free Web Service needs no credit card. Steps, all doable from a phone browser:

1. **Get this code onto GitHub** (mobile browser, no git needed):
   - Create a new repo at github.com (e.g. `uno-bot`)
   - Use "Add file → Upload files" and upload every file from this folder
     (`bot.py`, `game.py`, `requirements.txt`, `render.yaml`, `Dockerfile`, etc.)
   - Commit

2. **Connect to Render**:
   - Sign up at render.com (GitHub login is easiest)
   - "New +" → "Web Service" → pick your `uno-bot` repo
   - It should auto-detect `render.yaml` and pre-fill the build/start commands.
     If not, set manually: Build Command `pip install -r requirements.txt`,
     Start Command `python bot.py`
   - Choose the **Free** plan
   - Add environment variable `BOT_TOKEN` = your token from BotFather
   - Deploy

3. **Keep it from sleeping** (free Web Services sleep after ~15 min idle):
   - Sign up free at uptimerobot.com
   - Add a new monitor → HTTP(s) → paste your Render service's URL
     (something like `https://uno-bot-xxxx.onrender.com`)
   - Set check interval to 5 minutes
   - This pings the bot's keep-alive endpoint so it never goes to sleep

That's it — bot runs 24/7 in the cloud, no PC or terminal needed at any point.
(If you ever do get back to your PC/Linux box, the Docker route above is the
more "proper" long-term home for this.)

## Playing

In your group:
- `/newgame` — host creates a lobby
- Each player DMs the bot `/start` once (required so the bot can message them privately)
- `/join` — join the lobby (2-4 players)
- `/startgame` — host starts; everyone gets 7 cards in DM
- `/status` — check whose turn it is / card counts
- `/endgame` — cancel/end the current game

In your DM, when it's your turn:
- Tap a ✅ card to play it (wilds will then ask you to pick a color)
- Tap "🃏 Draw card" if you have no legal play
- Tap "🚨 UNO!" when you're down to 1 card (cosmetic call-out for now — no
  draw-2 penalty for forgetting, see Known simplifications below)

## Rules implemented
- Standard 108-card deck, 7 cards dealt each
- Skip, Reverse, Draw Two, Wild, Wild Draw Four all work
- Reverse acts as Skip in 2-player games
- Draw pile reshuffles from the discard pile if it runs out
- First to empty their hand wins, game ends immediately

## Known simplifications (easy to extend later if you want)
- No stacking of Draw Two/Draw Four onto each other
- Wild Draw Four can be played even if you have a matching color card
  (official house rule debate — easy to add a check in `game.py` if you want strict rules)
- No penalty for forgetting to call UNO
- Game state is in-memory only — restarting the bot wipes any game in progress
  (fine for casual play; say if you want SQLite persistence added)

## Files
- `game.py` — pure game logic (deck, rules, turns) — no Telegram code, easy to unit test
- `bot.py` — Telegram wiring (commands, buttons, DMs)
