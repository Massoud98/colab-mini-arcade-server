# ============================================================
# COLAB MINI ARCADE — ONLINE RELAY SERVER
# server.py
# ============================================================
#
# PURPOSE
# ------------------------------------------------------------
# Provides:
#
#   - WebSocket connections
#   - 4-character lobby creation
#   - Custom lobby codes
#   - Random lobby codes
#   - Two-player rooms
#   - Game validation
#   - Message relay
#   - Restart synchronization
#   - Disconnect handling
#   - HTTP health check
#
# The server is GAME-AGNOSTIC.
#
# It does NOT simulate:
#
#   - Pong physics
#   - Snake
#   - Tetris
#   - etc.
#
# The HOST client remains authoritative for gameplay.
#
# ============================================================


from __future__ import annotations

import asyncio
import json
import random
import string
import time

from dataclasses import dataclass
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse


# ============================================================
# 1. SERVER CONFIGURATION
# ============================================================

APP_NAME = "Colab Mini Arcade Relay"

APP_VERSION = "1.2.0"

LOBBY_CODE_LENGTH = 4

LOBBY_CODE_CHARS = (
    string.ascii_uppercase
    + string.digits
)

MAX_PLAYERS_PER_ROOM = 2

MAX_MESSAGE_SIZE = 32_000


# ============================================================
# LOBBY CODE SAFETY
# ============================================================
#
# Exact-match server-side blacklist for public lobby IDs.
# Codes are normalized to uppercase A-Z / 0-9 before validation.
# ============================================================

BANNED_LOBBY_CODES = {
    # English / international explicit
    "ANAL", "ANUS", "ARSE", "BOOB", "BUTT",
    "CLIT", "COCK", "CUNT", "DICK", "FUCK",
    "JIZZ", "PISS", "PORN", "RAPE", "SHIT",
    "SLUT", "SMUT", "TITS", "TWAT", "WANK",

    # Spanish / Chilean
    "CACA", "CULO", "PENE", "PICO", "POTO",
    "PUTA", "PUTO", "SEXO",

    # Other languages
    "PIKK", "CMAR",

    # Extremism / sensitive
    "NAZI",
}


def banned_code(code: str) -> bool:

    return code.upper() in BANNED_LOBBY_CODES


# ============================================================
# 2. FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
)


# ============================================================
# 3. PLAYER
# ============================================================

@dataclass
class Player:

    websocket: WebSocket

    role: str

    connected_at: float


# ============================================================
# 4. ROOM
# ============================================================

@dataclass
class Room:

    code: str

    game: str

    host: Player

    guest: Optional[Player]

    created_at: float


# ============================================================
# 5. SERVER STATE
# ============================================================

rooms: dict[str, Room] = {}

rooms_lock = asyncio.Lock()


# ============================================================
# 6. LOBBY CODE NORMALIZATION
# ============================================================

def normalize_code(value: object) -> str:

    if not isinstance(value, str):
        return ""

    normalized = "".join(
        character
        for character in value.upper()
        if character in LOBBY_CODE_CHARS
    )

    return normalized[:LOBBY_CODE_LENGTH]


# ============================================================
# 7. VALIDATE LOBBY CODE
# ============================================================

def valid_code(code: str) -> bool:

    return (
        len(code) == LOBBY_CODE_LENGTH
        and all(
            character in LOBBY_CODE_CHARS
            for character in code
        )
    )


# ============================================================
# 8. RANDOM LOBBY CODE
# ============================================================

def generate_random_code() -> str:

    return "".join(
        random.choice(LOBBY_CODE_CHARS)
        for _ in range(LOBBY_CODE_LENGTH)
    )


# ============================================================
# 9. FIND AVAILABLE RANDOM CODE
# ============================================================

def find_available_random_code() -> str:

    # With 36^4 = 1,679,616 possible codes,
    # collisions should be extremely uncommon.

    for _ in range(1000):

        code = generate_random_code()

        if (
            code not in rooms
            and not banned_code(code)
        ):
            return code

    raise RuntimeError(
        "Could not generate an available lobby code."
    )


# ============================================================
# 10. SAFE SEND
# ============================================================

async def safe_send(
    websocket: WebSocket,
    message: dict,
) -> bool:

    try:

        await websocket.send_json(message)

        return True

    except Exception:

        return False


# ============================================================
# 11. SEND ERROR
# ============================================================

async def send_error(
    websocket: WebSocket,
    code: str,
    message: str,
):

    await safe_send(
        websocket,
        {
            "type": "error",
            "code": code,
            "message": message,
        },
    )


# ============================================================
# 12. FIND PLAYER ROOM
# ============================================================

def find_player_room(
    websocket: WebSocket,
) -> tuple[Optional[Room], Optional[str]]:

    for room in rooms.values():

        if room.host.websocket is websocket:

            return room, "host"

        if (
            room.guest is not None
            and room.guest.websocket is websocket
        ):

            return room, "guest"

    return None, None


# ============================================================
# 13. GET OTHER PLAYER
# ============================================================

def get_other_player(
    room: Room,
    role: str,
) -> Optional[Player]:

    if role == "host":

        return room.guest

    if role == "guest":

        return room.host

    return None


# ============================================================
# 14. CREATE LOBBY
# ============================================================

async def create_lobby(
    websocket: WebSocket,
    game: object,
    requested_code: object,
):

    if not isinstance(game, str):

        await send_error(
            websocket,
            "INVALID_GAME",
            "A valid game ID is required.",
        )

        return

    game = game.strip().lower()

    if not game:

        await send_error(
            websocket,
            "INVALID_GAME",
            "A valid game ID is required.",
        )

        return

    async with rooms_lock:

        existing_room, _ = find_player_room(
            websocket
        )

        if existing_room is not None:

            await send_error(
                websocket,
                "ALREADY_IN_LOBBY",
                "You are already in a lobby.",
            )

            return

        # ----------------------------------------------------
        # RANDOM CODE
        # ----------------------------------------------------

        if requested_code is None:

            code = find_available_random_code()

        # ----------------------------------------------------
        # CUSTOM CODE
        # ----------------------------------------------------

        else:

            code = normalize_code(
                requested_code
            )

            if not valid_code(code):

                await send_error(
                    websocket,
                    "INVALID_CODE",
                    (
                        "Lobby codes must contain exactly "
                        "4 letters or numbers."
                    ),
                )

                return

            if banned_code(code):

                await send_error(
                    websocket,
                    "CODE_NOT_ALLOWED",
                    "That lobby code is not allowed. Please choose another.",
                )

                return

            if code in rooms:

                await send_error(
                    websocket,
                    "CODE_IN_USE",
                    (
                        f"Lobby code {code} "
                        "is already in use."
                    ),
                )

                return

        player = Player(
            websocket=websocket,
            role="host",
            connected_at=time.time(),
        )

        room = Room(
            code=code,
            game=game,
            host=player,
            guest=None,
            created_at=time.time(),
        )

        rooms[code] = room

    print(
        f"[CREATE] {code} | "
        f"game={game}"
    )

    await safe_send(
        websocket,
        {
            "type": "lobby_created",
            "code": code,
            "role": "host",
            "game": game,
        },
    )


# ============================================================
# 15. JOIN LOBBY
# ============================================================

async def join_lobby(
    websocket: WebSocket,
    code_value: object,
    game_value: object,
):

    code = normalize_code(
        code_value
    )

    if not valid_code(code):

        await send_error(
            websocket,
            "INVALID_CODE",
            (
                "Lobby codes must contain exactly "
                "4 letters or numbers."
            ),
        )

        return

    if banned_code(code):

        await send_error(
            websocket,
            "CODE_NOT_ALLOWED",
            "That lobby code is not allowed.",
        )

        return

    if not isinstance(game_value, str):

        await send_error(
            websocket,
            "INVALID_GAME",
            "A valid game ID is required.",
        )

        return

    game = game_value.strip().lower()

    if not game:

        await send_error(
            websocket,
            "INVALID_GAME",
            "A valid game ID is required.",
        )

        return

    async with rooms_lock:

        existing_room, _ = find_player_room(
            websocket
        )

        if existing_room is not None:

            await send_error(
                websocket,
                "ALREADY_IN_LOBBY",
                "You are already in a lobby.",
            )

            return

        room = rooms.get(
            code
        )

        if room is None:

            await send_error(
                websocket,
                "LOBBY_NOT_FOUND",
                (
                    f"Lobby {code} "
                    "does not exist."
                ),
            )

            return

        if room.guest is not None:

            await send_error(
                websocket,
                "LOBBY_FULL",
                (
                    f"Lobby {code} "
                    "already has two players."
                ),
            )

            return

        if room.game != game:

            await send_error(
                websocket,
                "GAME_MISMATCH",
                (
                    f"Lobby {code} is for "
                    f"{room.game}, not {game}."
                ),
            )

            return

        guest = Player(
            websocket=websocket,
            role="guest",
            connected_at=time.time(),
        )

        room.guest = guest

    print(
        f"[JOIN] {code} | "
        f"game={game}"
    )

    # Tell guest that joining succeeded.

    await safe_send(
        websocket,
        {
            "type": "lobby_joined",
            "code": code,
            "role": "guest",
            "game": game,
        },
    )

    # Tell host somebody joined.

    await safe_send(
        room.host.websocket,
        {
            "type": "peer_joined",
            "code": code,
            "game": game,
        },
    )

    # --------------------------------------------------------
    # START MATCH
    # --------------------------------------------------------
    #
    # Both clients receive the same start signal.
    #
    # Their local role determines which side they control.
    #
    # --------------------------------------------------------

    start_message = {
        "type": "start",
        "code": code,
        "game": game,
    }

    await safe_send(
        room.host.websocket,
        start_message,
    )

    await safe_send(
        websocket,
        start_message,
    )


# ============================================================
# 16. RELAY GAME MESSAGE
# ============================================================

async def relay_message(
    websocket: WebSocket,
    payload: object,
):

    if not isinstance(payload, dict):

        await send_error(
            websocket,
            "INVALID_PAYLOAD",
            "Relay payload must be an object.",
        )

        return

    async with rooms_lock:

        room, role = find_player_room(
            websocket
        )

        if room is None:

            await send_error(
                websocket,
                "NOT_IN_LOBBY",
                "You are not currently in a lobby.",
            )

            return

        other_player = get_other_player(
            room,
            role,
        )

    if other_player is None:

        return

    await safe_send(
        other_player.websocket,
        {
            "type": "relay",
            "payload": payload,
        },
    )


# ============================================================
# 17. SYNCHRONIZED RESTART
# ============================================================

async def restart_match(
    websocket: WebSocket,
):

    async with rooms_lock:

        room, role = find_player_room(
            websocket
        )

        if room is None:

            await send_error(
                websocket,
                "NOT_IN_LOBBY",
                "You are not currently in a lobby.",
            )

            return

        host_socket = (
            room.host.websocket
        )

        guest_socket = (
            room.guest.websocket
            if room.guest is not None
            else None
        )

    restart_message = {
        "type": "restart_now"
    }

    await safe_send(
        host_socket,
        restart_message,
    )

    if guest_socket is not None:

        await safe_send(
            guest_socket,
            restart_message,
        )


# ============================================================
# 18. LEAVE LOBBY
# ============================================================

async def leave_lobby(
    websocket: WebSocket,
    notify_peer: bool = True,
):

    peer_socket = None
    removed_code = None

    async with rooms_lock:

        room, role = find_player_room(
            websocket
        )

        if room is None:
            return

        removed_code = room.code

        # ----------------------------------------------------
        # HOST LEAVES
        # ----------------------------------------------------
        #
        # Host is authoritative, so the room ends.
        #
        # We don't promote the guest to host.
        #
        # ----------------------------------------------------

        if role == "host":

            if room.guest is not None:

                peer_socket = (
                    room.guest.websocket
                )

            rooms.pop(
                room.code,
                None,
            )

        # ----------------------------------------------------
        # GUEST LEAVES
        # ----------------------------------------------------

        elif role == "guest":

            peer_socket = (
                room.host.websocket
            )

            room.guest = None

    if removed_code:

        print(
            f"[LEAVE] {removed_code} | "
            f"role={role}"
        )

    if (
        notify_peer
        and peer_socket is not None
    ):

        await safe_send(
            peer_socket,
            {
                "type": "peer_left",
                "code": removed_code,
            },
        )


# ============================================================
# 19. MESSAGE HANDLER
# ============================================================

async def handle_message(
    websocket: WebSocket,
    raw_message: str,
):

    # --------------------------------------------------------
    # MESSAGE SIZE PROTECTION
    # --------------------------------------------------------

    if len(raw_message) > MAX_MESSAGE_SIZE:

        await send_error(
            websocket,
            "MESSAGE_TOO_LARGE",
            "Message is too large.",
        )

        return

    # --------------------------------------------------------
    # JSON
    # --------------------------------------------------------

    try:

        message = json.loads(
            raw_message
        )

    except json.JSONDecodeError:

        await send_error(
            websocket,
            "INVALID_JSON",
            "Message must contain valid JSON.",
        )

        return

    if not isinstance(message, dict):

        await send_error(
            websocket,
            "INVALID_MESSAGE",
            "Message must be a JSON object.",
        )

        return

    message_type = message.get(
        "type"
    )

    # --------------------------------------------------------
    # CREATE
    # --------------------------------------------------------

    if message_type == "create":

        await create_lobby(
            websocket,
            message.get(
                "game"
            ),
            message.get(
                "code"
            ),
        )

    # --------------------------------------------------------
    # JOIN
    # --------------------------------------------------------

    elif message_type == "join":

        await join_lobby(
            websocket,
            message.get(
                "code"
            ),
            message.get(
                "game"
            ),
        )

    # --------------------------------------------------------
    # RELAY
    # --------------------------------------------------------

    elif message_type == "relay":

        await relay_message(
            websocket,
            message.get(
                "payload"
            ),
        )

    # --------------------------------------------------------
    # RESTART
    # --------------------------------------------------------

    elif message_type == "restart_request":

        await restart_match(
            websocket
        )

    # --------------------------------------------------------
    # LEAVE
    # --------------------------------------------------------

    elif message_type == "leave":

        await leave_lobby(
            websocket
        )

        await safe_send(
            websocket,
            {
                "type": "left_lobby"
            },
        )

    # --------------------------------------------------------
    # APPLICATION-LEVEL PING
    # --------------------------------------------------------

    elif message_type == "ping":

        await safe_send(
            websocket,
            {
                "type": "pong",
                "timestamp": message.get(
                    "timestamp"
                ),
            },
        )

    # --------------------------------------------------------
    # UNKNOWN
    # --------------------------------------------------------

    else:

        await send_error(
            websocket,
            "UNKNOWN_MESSAGE",
            (
                "Unknown message type: "
                f"{message_type}"
            ),
        )


# ============================================================
# 20. HTTP HOME / HEALTH CHECK
# ============================================================

@app.get("/")
async def root():

    async with rooms_lock:

        room_count = len(
            rooms
        )

        player_count = sum(

            1 +
            (
                1
                if room.guest is not None
                else 0
            )

            for room in rooms.values()
        )

    return JSONResponse(
        {
            "status": "online",
            "service": APP_NAME,
            "version": APP_VERSION,
            "rooms": room_count,
            "players": player_count,
        }
    )


# ============================================================
# 21. SIMPLE HEALTH ENDPOINT
# ============================================================

@app.get("/health")
async def health():

    return {
        "status": "ok"
    }


# ============================================================
# 22. WEBSOCKET ENDPOINT
# ============================================================

@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
):

    await websocket.accept()

    print(
        "[CONNECT] New WebSocket client"
    )

    await safe_send(
        websocket,
        {
            "type": "connected",
            "service": APP_NAME,
            "version": APP_VERSION,
        },
    )

    try:

        while True:

            raw_message = (
                await websocket.receive_text()
            )

            await handle_message(
                websocket,
                raw_message,
            )

    except WebSocketDisconnect:

        print(
            "[DISCONNECT] WebSocket client"
        )

    except Exception as error:

        print(
            "[ERROR]",
            repr(error)
        )

    finally:

        await leave_lobby(
            websocket,
            notify_peer=True,
        )


# ============================================================
# END SERVER
# ============================================================
