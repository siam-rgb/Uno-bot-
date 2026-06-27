"""
Core UNO game engine. No Telegram code here — pure game logic.
"""
import random
from dataclasses import dataclass, field
from typing import Optional

COLORS = ["red", "yellow", "green", "blue"]
COLOR_EMOJI = {
    "red": "🔴",
    "yellow": "🟡",
    "green": "🟢",
    "blue": "🔵",
    "wild": "🌈",
}
ACTION_LABELS = {
    "skip": "SKIP",
    "reverse": "REVERSE",
    "draw2": "+2",
    "wild": "WILD",
    "wild_draw4": "WILD +4",
}


@dataclass(frozen=True)
class Card:
    color: str  # red/yellow/green/blue/wild
    value: str  # "0".."9", "skip", "reverse", "draw2", "wild", "wild_draw4"

    def label(self) -> str:
        emoji = COLOR_EMOJI[self.color]
        if self.value in ACTION_LABELS:
            return f"{emoji}{ACTION_LABELS[self.value]}"
        return f"{emoji}{self.value}"

    def is_wild(self) -> bool:
        return self.color == "wild"


def build_deck() -> list[Card]:
    deck = []
    for color in COLORS:
        deck.append(Card(color, "0"))
        for n in range(1, 10):
            deck.append(Card(color, str(n)))
            deck.append(Card(color, str(n)))
        for action in ("skip", "reverse", "draw2"):
            deck.append(Card(color, action))
            deck.append(Card(color, action))
    for _ in range(4):
        deck.append(Card("wild", "wild"))
        deck.append(Card("wild", "wild_draw4"))
    random.shuffle(deck)
    return deck


@dataclass
class Player:
    user_id: int
    name: str
    hand: list[Card] = field(default_factory=list)
    said_uno: bool = False

    def has_uno(self) -> bool:
        return len(self.hand) == 1


class GameError(Exception):
    pass


class UnoGame:
    def __init__(self, chat_id: int, host_id: int, host_name: str):
        self.chat_id = chat_id
        self.players: list[Player] = [Player(host_id, host_name)]
        self.deck: list[Card] = []
        self.discard: list[Card] = []
        self.current_idx = 0
        self.direction = 1  # 1 = clockwise, -1 = counter-clockwise
        self.started = False
        self.finished = False
        self.current_color: Optional[str] = None  # active color when top card is wild
        self.pending_wild_player: Optional[int] = None  # waiting on color choice
        self.pending_wild_card: Optional[Card] = None
        self.winner: Optional[Player] = None

    # ---------- lobby ----------
    def add_player(self, user_id: int, name: str) -> bool:
        if self.started:
            raise GameError("Game already started.")
        if any(p.user_id == user_id for p in self.players):
            return False
        if len(self.players) >= 4:
            raise GameError("Lobby full (max 4 players).")
        self.players.append(Player(user_id, name))
        return True

    def remove_player(self, user_id: int):
        self.players = [p for p in self.players if p.user_id != user_id]

    def get_player(self, user_id: int) -> Optional[Player]:
        for p in self.players:
            if p.user_id == user_id:
                return p
        return None

    # ---------- game start ----------
    def start(self):
        if len(self.players) < 2:
            raise GameError("Need at least 2 players to start.")
        self.deck = build_deck()
        for p in self.players:
            p.hand = [self.deck.pop() for _ in range(7)]
        # find a non-wild, non-action starting card so the first turn is simple
        first = self.deck.pop()
        while first.value in ("wild", "wild_draw4", "skip", "reverse", "draw2"):
            self.deck.insert(0, first)
            random.shuffle(self.deck)
            first = self.deck.pop()
        self.discard = [first]
        self.current_color = first.color
        self.current_idx = 0
        self.started = True

    # ---------- helpers ----------
    def top_card(self) -> Card:
        return self.discard[-1]

    def current_player(self) -> Player:
        return self.players[self.current_idx]

    def _draw_one(self) -> Card:
        if not self.deck:
            # reshuffle discard (except top) back into deck
            top = self.discard.pop()
            self.deck = self.discard
            self.discard = [top]
            random.shuffle(self.deck)
        return self.deck.pop()

    def draw_cards(self, player: Player, n: int) -> list[Card]:
        drawn = [self._draw_one() for _ in range(n)]
        player.hand.extend(drawn)
        return drawn

    def is_legal(self, card: Card) -> bool:
        top = self.top_card()
        if card.is_wild():
            return True
        if card.color == self.current_color:
            return True
        if card.value == top.value:
            return True
        return False

    def advance_turn(self, steps: int = 1):
        n = len(self.players)
        self.current_idx = (self.current_idx + steps * self.direction) % n

    # ---------- main actions ----------
    def play_card(self, player: Player, card: Card, chosen_color: Optional[str] = None) -> dict:
        """Returns a dict describing what happened, for the bot layer to announce."""
        if card not in player.hand:
            raise GameError("You don't have that card.")
        if not self.is_legal(card):
            raise GameError("That card doesn't match the top of the discard pile.")

        player.hand.remove(card)
        self.discard.append(card)
        player.said_uno = False

        result = {"card": card, "skipped": False, "reversed": False, "drew": 0, "next_color": None}

        if card.is_wild():
            if chosen_color not in COLORS:
                raise GameError("Need a valid color for wild card.")
            self.current_color = chosen_color
            result["next_color"] = chosen_color
        else:
            self.current_color = card.color

        # check win
        if not player.hand:
            self.finished = True
            self.winner = player
            return result

        # apply action effects
        if card.value == "reverse":
            self.direction *= -1
            result["reversed"] = True
            if len(self.players) == 2:
                # acts like skip in 2p
                self.advance_turn(1)
                result["skipped"] = True
        elif card.value == "skip":
            self.advance_turn(1)
            result["skipped"] = True
        elif card.value == "draw2":
            self.advance_turn(1)
            victim = self.current_player()
            self.draw_cards(victim, 2)
            result["drew"] = 2
            self.advance_turn(1)
            result["skipped"] = True
        elif card.value == "wild_draw4":
            self.advance_turn(1)
            victim = self.current_player()
            self.draw_cards(victim, 4)
            result["drew"] = 4
            self.advance_turn(1)
            result["skipped"] = True
        else:
            self.advance_turn(1)

        return result

    def draw_for_current(self) -> Card:
        player = self.current_player()
        card = self._draw_one()
        player.hand.append(card)
        return card

    def pass_turn(self):
        self.advance_turn(1)
