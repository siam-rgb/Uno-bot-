"""
UNO Telegram bot.
- Lobby + turn announcements happen in the GROUP chat.
- Each player's hand is shown privately via DM with inline-keyboard buttons.

Setup:
  1. pip install -r requirements.txt
  2. export BOT_TOKEN=xxxx   (or put it in a .env file, see .env.example)
  3. python bot.py

Each player must DM the bot and press /start ONCE before joining a game,
because Telegram only lets a bot message a user after that user has
started a conversation with it.
"""
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from telegram.error import Forbidden

from game import UnoGame, GameError, Card, COLORS, COLOR_EMOJI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("uno_bot")

# chat_id (group) -> UnoGame
GAMES: dict[int, UnoGame] = {}
# user_id -> chat_id (which group's game this user is currently in)
USER_GAME: dict[int, int] = {}


def find_game_for_user(user_id: int) -> UnoGame | None:
    chat_id = USER_GAME.get(user_id)
    if chat_id is None:
        return None
    return GAMES.get(chat_id)


# ---------------------------------------------------------------------------
# Basic commands
# ---------------------------------------------------------------------------

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text(
            "👋 You're set! Now go to your group and use /join to enter a game, "
            "or /newgame to start one."
        )
    else:
        await update.message.reply_text(
            "Use /newgame to start a lobby, /join to enter it, then /startgame when ready.\n"
            "⚠️ Everyone must DM me /start first so I'm allowed to send your hand privately."
        )


async def newgame_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat.type == "private":
        await update.message.reply_text("Start a game inside a group chat, not in DM.")
        return
    if chat.id in GAMES and not GAMES[chat.id].finished:
        await update.message.reply_text("A game is already active here. /endgame to cancel it first.")
        return
    game = UnoGame(chat.id, user.id, user.first_name)
    GAMES[chat.id] = game
    USER_GAME[user.id] = chat.id
    await update.message.reply_text(
        f"🎴 UNO lobby created by {user.first_name}!\n"
        f"Players: {user.first_name}\n\n"
        "Others type /join to enter (2-4 players). DM me /start first!\n"
        "Host runs /startgame when everyone's in."
    )


async def join_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat.type == "private":
        await update.message.reply_text("Join from inside the group chat.")
        return
    game = GAMES.get(chat.id)
    if not game or game.finished:
        await update.message.reply_text("No active lobby. Use /newgame first.")
        return
    try:
        # quick check we're allowed to DM them
        await context.bot.send_message(user.id, "✅ You're in the UNO lobby. Hang tight for the host to start.")
    except Forbidden:
        await update.message.reply_text(
            f"{user.first_name}, please DM me /start first so I can send you your cards privately!"
        )
        return

    try:
        added = game.add_player(user.id, user.first_name)
    except GameError as e:
        await update.message.reply_text(str(e))
        return

    if not added:
        await update.message.reply_text("You're already in this lobby.")
        return

    USER_GAME[user.id] = chat.id
    names = ", ".join(p.name for p in game.players)
    await update.message.reply_text(f"✅ {user.first_name} joined! Players ({len(game.players)}/4): {names}")


async def startgame_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    game = GAMES.get(chat.id)
    if not game:
        await update.message.reply_text("No lobby here. /newgame to create one.")
        return
    if game.players[0].user_id != user.id:
        await update.message.reply_text("Only the host who created the lobby can start the game.")
        return
    try:
        game.start()
    except GameError as e:
        await update.message.reply_text(str(e))
        return

    names = ", ".join(p.name for p in game.players)
    await update.message.reply_text(
        f"🎮 Game started! Players: {names}\n"
        f"Top card: {game.top_card().label()}\n\n"
        f"👉 {game.current_player().name}'s turn — check your DM!"
    )
    for p in game.players:
        await send_hand(context, game, p)


async def endgame_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    game = GAMES.get(chat.id)
    if not game:
        await update.message.reply_text("No game to end here.")
        return
    for p in game.players:
        USER_GAME.pop(p.user_id, None)
    del GAMES[chat.id]
    await update.message.reply_text("🛑 Game ended.")


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    game = GAMES.get(chat.id)
    if not game:
        await update.message.reply_text("No active game here.")
        return
    if not game.started:
        names = ", ".join(p.name for p in game.players)
        await update.message.reply_text(f"Lobby ({len(game.players)}/4): {names}")
        return
    counts = ", ".join(f"{p.name}: {len(p.hand)} cards" for p in game.players)
    await update.message.reply_text(
        f"Top card: {game.top_card().label()}\n"
        f"Turn: {game.current_player().name}\n"
        f"{counts}"
    )


# ---------------------------------------------------------------------------
# Hand rendering (DM)
# ---------------------------------------------------------------------------

def hand_keyboard(game: UnoGame, player) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for i, card in enumerate(player.hand):
        legal = game.is_legal(card) and game.current_player().user_id == player.user_id
        label = card.label() + (" ✅" if legal else "")
        row.append(InlineKeyboardButton(label, callback_data=f"play:{i}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    if game.current_player().user_id == player.user_id:
        rows.append([InlineKeyboardButton("🃏 Draw card", callback_data="draw")])
        if player.has_uno() and not player.said_uno:
            rows.append([InlineKeyboardButton("🚨 UNO!", callback_data="uno")])
    return InlineKeyboardMarkup(rows)


async def send_hand(context: ContextTypes.DEFAULT_TYPE, game: UnoGame, player, note: str = ""):
    is_turn = game.current_player().user_id == player.user_id
    header = f"Top card: {game.top_card().label()} (color in play: {COLOR_EMOJI[game.current_color]})\n"
    header += "🟢 YOUR TURN — tap a card marked ✅, or draw.\n" if is_turn else "⏳ Waiting for your turn.\n"
    if note:
        header += f"\n{note}\n"
    try:
        await context.bot.send_message(
            player.user_id,
            header,
            reply_markup=hand_keyboard(game, player),
        )
    except Forbidden:
        # can't reach them in DM; group will still show what's happening
        pass


async def color_keyboard_message(context: ContextTypes.DEFAULT_TYPE, player):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{COLOR_EMOJI[c]} {c}", callback_data=f"color:{c}") for c in COLORS]
    ])
    await context.bot.send_message(player.user_id, "Choose a color:", reply_markup=kb)


# ---------------------------------------------------------------------------
# Callback (button) handling
# ---------------------------------------------------------------------------

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    game = find_game_for_user(user.id)
    if not game or not game.started or game.finished:
        await query.edit_message_text("This game isn't active anymore.")
        return

    player = game.get_player(user.id)
    data = query.data

    if data == "uno":
        player.said_uno = True
        await query.edit_message_text("🚨 UNO called!")
        await send_hand(context, game, player)
        return

    if data.startswith("color:"):
        if game.pending_wild_player != user.id:
            await query.answer("Not waiting on a color choice from you.", show_alert=True)
            return
        color = data.split(":", 1)[1]
        card = game.pending_wild_card
        await finish_play(context, game, player, card, chosen_color=color)
        return

    if data == "draw":
        if game.current_player().user_id != user.id:
            await query.answer("Not your turn.", show_alert=True)
            return
        card = game.draw_for_current()
        game.pass_turn()
        await context.bot.send_message(
            game.chat_id, f"🃏 {player.name} drew a card and passed.\n👉 {game.current_player().name}'s turn."
        )
        await query.edit_message_text(f"You drew: {card.label()}")
        await send_hand(context, game, player)
        await notify_current_turn(context, game)
        return

    if data.startswith("play:"):
        if game.current_player().user_id != user.id:
            await query.answer("Not your turn.", show_alert=True)
            return
        idx = int(data.split(":", 1)[1])
        if idx >= len(player.hand):
            await query.answer("Invalid card.", show_alert=True)
            return
        card = player.hand[idx]
        if not game.is_legal(card):
            await query.answer("That card isn't playable right now.", show_alert=True)
            return

        if card.is_wild():
            game.pending_wild_player = user.id
            game.pending_wild_card = card
            await query.edit_message_text(f"Playing {card.label()}...")
            await color_keyboard_message(context, player)
            return

        await finish_play(context, game, player, card)


async def finish_play(context, game: UnoGame, player, card: Card, chosen_color: str | None = None):
    game.pending_wild_player = None
    try:
        result = game.play_card(player, card, chosen_color)
    except GameError as e:
        await context.bot.send_message(player.user_id, f"⚠️ {e}")
        return

    color_note = f" → color set to {chosen_color}" if chosen_color else ""
    await context.bot.send_message(
        game.chat_id, f"▶️ {player.name} played {card.label()}{color_note}"
    )

    if game.finished:
        await context.bot.send_message(game.chat_id, f"🏆 {player.name} wins! GG 🎉")
        for p in game.players:
            USER_GAME.pop(p.user_id, None)
        GAMES.pop(game.chat_id, None)
        return

    extra_note = ""
    if result["drew"]:
        extra_note = f"Next player drew {result['drew']} cards and was skipped."
    elif result["skipped"]:
        extra_note = "Next player was skipped."
    elif result["reversed"]:
        extra_note = "Direction reversed!"

    await context.bot.send_message(
        game.chat_id,
        f"{extra_note}\n👉 {game.current_player().name}'s turn.".strip(),
    )
    await send_hand(context, game, player)  # refresh their own hand view (card removed)
    await notify_current_turn(context, game)


async def notify_current_turn(context, game: UnoGame):
    current = game.current_player()
    await send_hand(context, game, current)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def start_keepalive_server():
    """Render's free tier needs a Web Service to listen on a port, and
    UptimeRobot needs something to ping so the service doesn't sleep.
    This just returns 200 OK on any request — purely a heartbeat."""
    port = int(os.environ.get("PORT", 10000))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"UNO bot is alive")

        def log_message(self, *args):
            pass  # silence noisy access logs

    server = HTTPServer(("0.0.0.0", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info(f"Keep-alive HTTP server listening on port {port}")


def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("Set BOT_TOKEN environment variable (see .env.example).")

    start_keepalive_server()

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("newgame", newgame_cmd))
    app.add_handler(CommandHandler("join", join_cmd))
    app.add_handler(CommandHandler("startgame", startgame_cmd))
    app.add_handler(CommandHandler("endgame", endgame_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CallbackQueryHandler(on_callback))

    log.info("UNO bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
