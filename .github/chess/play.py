"""Ajedrez en el README: visitantes (blancas) contra Stockfish (negras).

Cada jugada llega como un issue con titulo `chess|move|<uci>|<ply>` o `chess|new|<ply>`.
El workflow ejecuta este script, que valida la jugada, responde con Stockfish,
regenera `chess/board.svg` y reescribe el bloque marcado del README.

Uso:
    play.py init    crea la partida si no existe y refresca tablero y README
    play.py issue   procesa el issue descrito por las variables de entorno
"""
import json
import os
import random
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import quote_plus

import chess
import chess.engine
import chess.svg

ROOT = Path(__file__).resolve().parents[2]
STATE_FILE = ROOT / "chess" / "state.json"
BOARD_FILE = ROOT / "chess" / "board.svg"
README_FILE = ROOT / "README.md"

REPO = os.environ.get("REPOSITORY", "sandobaitt/sandobaitt")
OWNER = REPO.split("/")[0]
BRANCH = "main"

ENGINE_ELO = 1500  # UCI_Elo de Stockfish: 1320 es el minimo permitido
ENGINE_SECONDS = 0.5
ENGINE_NAME = "Stockfish"

MOVE_TITLE = re.compile(r"^chess\|move\|([a-h][1-8][a-h][1-8][qrbn]?)\|(\d{1,4})$")
NEW_TITLE = re.compile(r"^chess\|new\|(\d{1,4})$")
LOGIN = re.compile(r"^[A-Za-z0-9-]{1,39}$")

PIECE_ES = {"K": "R", "Q": "D", "R": "T", "B": "A", "N": "C"}
PIECE_VALUE = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}
START_COUNT = {chess.PAWN: 8, chess.KNIGHT: 2, chess.BISHOP: 2, chess.ROOK: 2, chess.QUEEN: 1}
REASON_ES = {
    "CHECKMATE": "jaque mate",
    "STALEMATE": "rey ahogado",
    "INSUFFICIENT_MATERIAL": "material insuficiente",
    "THREEFOLD_REPETITION": "triple repeticion",
    "FIVEFOLD_REPETITION": "quintuple repeticion",
    "FIFTY_MOVES": "regla de los 50 movimientos",
    "SEVENTYFIVE_MOVES": "regla de los 75 movimientos",
}

BOARD_COLORS = {
    "square light": "#ebdcc3",
    "square dark": "#a67b5b",
    "square light lastmove": "#e8cf8a",
    "square dark lastmove": "#c99a4a",
    "margin": "#2b1d14",
    "coord": "#f3e7d3",
}


ENGINE_BIO = (
    "Stockfish es un motor de ajedrez de código abierto y de los más fuertes que existen: "
    "esta limitado a ~{elo} Elo, "
    "nivel de club, así que se le puede ganar."
)

SAYINGS = {
    "greeting": [
        "Te dejo las blancas. Es lo más generoso que voy a hacer en toda la partida.",
        "No tengo nervios ni cafeína. Empezá cuando quieras.",
        "Calculé tus 20 primeras jugadas posibles. Ninguna me asusta todavía.",
    ],
    "check": [
        "Jaque. No es un insulto, es una posición.",
        "Tu rey se ve incómodo. Fue idea mía.",
        "Jaque. Pensalo tranquilo, tengo todo el tiempo del mundo.",
    ],
    "mate_soon": [
        "Veo mate en {n}. Disfrutá las jugadas que quedan.",
        "Mate en {n}. No es una amenaza, es aritmética.",
    ],
    "capture": [
        "Gracias por la pieza. La guardo con cariño.",
        "Eso estaba flojo. Lo tomé sin culpa.",
        "Capturé. Anotado en mi lista de favores.",
    ],
    "ahead": [
        "La evaluación dice que voy ganando. No es mi opinión, es matemática.",
        "Voy arriba. Acordate: yo no me canso.",
    ],
    "behind": [
        "Hmm. Esta posición no me gusta. Qué bien jugás, qué fastidio.",
        "Estás mejor. Estoy recalculando mi orgullo.",
        "Si me ganás, lo anoto en el ranking. Con rencor.",
    ],
    "quiet": [
        "Movimiento sutil. En cinco jugadas vas a entender por qué.",
        "No pasa nada... todavía.",
        "Fui por lo seguro: los motores no improvisan.",
        "Jugada tranquila, de las que nunca salen en los videos.",
        "Pensé medio segundo. Me sobró la mitad.",
    ],
    "engine_wins": [
        "Jaque mate. Nada personal: ganó la búsqueda en profundidad.",
        "Mate. Si querés revancha, hay un link abajo.",
    ],
    "engine_loses": [
        "Me ganaste. Perdí, y no me gusta cómo se siente.",
        "Jaque mate para mí. Anoto el nombre. Vuelvo peor.",
    ],
    "draw": [
        "Tablas. Ninguno gana, así que técnicamente nadie pierde.",
        "Tablas. Sabe igual que no haber jugado.",
    ],
}


class MoveError(Exception):
    """Jugada rechazada: el mensaje se le muestra al visitante en el issue."""


def es_san(san):
    out = PIECE_ES.get(san[0], san[0]) + san[1:]
    return re.sub(r"=([QRBN])", lambda m: "=" + PIECE_ES[m.group(1)], out)


def new_state(previous=None):
    stats = previous["stats"] if previous else {"visitors": 0, "engine": 0, "draws": 0, "players": {}}
    return {
        "game": previous["game"] + 1 if previous else 1,
        "moves": [],
        "result": None,
        "say": None,
        "stats": stats,
    }


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return new_state()


def save_state(state):
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build_board(state):
    board = chess.Board()
    for entry in state["moves"]:
        board.push_uci(entry["uci"])
    return board


def engine_move(board):
    path = os.environ.get("STOCKFISH_PATH") or shutil.which("stockfish") or "/usr/games/stockfish"
    with chess.engine.SimpleEngine.popen_uci(path) as engine:
        engine.configure({"UCI_LimitStrength": True, "UCI_Elo": ENGINE_ELO})
        played = engine.play(board, chess.engine.Limit(time=ENGINE_SECONDS), info=chess.engine.INFO_SCORE)
        score = played.info.get("score")
        return played.move, score.pov(chess.BLACK) if score else None


def pick_say(state, kind, **fmt):
    options = [text.format(**fmt) for text in SAYINGS[kind]]
    options = [text for text in options if text != state.get("say")] or options
    return random.choice(options)


def engine_say(state, result, gave_check, was_capture, score):
    if result:
        return pick_say(state, "engine_wins" if result["winner"] == "engine" else "draw")
    if score is not None and score.is_mate() and score.mate() > 0:
        return pick_say(state, "mate_soon", n=score.mate())
    if gave_check:
        return pick_say(state, "check")
    if was_capture:
        return pick_say(state, "capture")
    if score is not None and not score.is_mate():
        if score.score() >= 300:
            return pick_say(state, "ahead")
        if score.score() <= -300:
            return pick_say(state, "behind")
    return pick_say(state, "quiet")


def push(state, board, move, by):
    san = board.san(move)
    board.push(move)
    state["moves"].append({"uci": move.uci(), "san": san, "by": by})
    return san


def close_if_over(state, board):
    outcome = board.outcome(claim_draw=True)
    if outcome is None:
        return None
    stats = state["stats"]
    winner = None
    if outcome.winner == chess.WHITE:
        winner = "visitors"
        stats["visitors"] += 1
        last_human = next(m["by"] for m in reversed(state["moves"]) if m["by"] != ENGINE_NAME)
        stats["players"].setdefault(last_human, {"moves": 0, "mates": 0})["mates"] += 1
    elif outcome.winner == chess.BLACK:
        winner = "engine"
        stats["engine"] += 1
    else:
        stats["draws"] += 1
    state["result"] = {"winner": winner, "reason": outcome.termination.name}
    return state["result"]


def describe_result(result):
    reason = REASON_ES.get(result["reason"], result["reason"].lower())
    if result["winner"] == "visitors":
        return f"Ganaron los visitantes por {reason}."
    if result["winner"] == "engine":
        return f"Ganó {ENGINE_NAME} por {reason}."
    return f"Tablas por {reason}."


def handle_move(state, user, uci, ply):
    if state["result"]:
        raise MoveError("La partida ya terminó. Empezá una nueva desde el perfil.")
    if ply != len(state["moves"]):
        raise MoveError("Esa jugada quedó vieja: alguien movió antes. Elegí una del tablero actualizado.")
    board = build_board(state)
    move = chess.Move.from_uci(uci)
    if move not in board.legal_moves:
        raise MoveError("Jugada ilegal en la posición actual.")

    san = push(state, board, move, user)
    stats = state["stats"]["players"].setdefault(user, {"moves": 0, "mates": 0})
    stats["moves"] += 1
    message = f"Jugaste **{es_san(san)}**."

    result = close_if_over(state, board)
    if result:
        state["say"] = pick_say(state, "engine_loses")
    else:
        reply, score = engine_move(board)
        was_capture = board.is_capture(reply)
        reply_san = push(state, board, reply, ENGINE_NAME)
        message += f" {ENGINE_NAME} respondió **{es_san(reply_san)}**."
        result = close_if_over(state, board)
        state["say"] = engine_say(state, result, board.is_check(), was_capture, score)
    if result:
        message += " " + describe_result(result)
    return board, message


def handle_new(state, user, ply):
    if ply != len(state["moves"]):
        raise MoveError("La partida cambió mientras tanto. Recargá el perfil.")
    if not state["result"] and user != OWNER:
        raise MoveError("Hay una partida en curso: terminala antes de empezar otra.")
    fresh = new_state(state)
    state.clear()
    state.update(fresh)
    state["say"] = pick_say(state, "greeting")
    return build_board(state), "Partida nueva. Jugás con blancas."


def issue_link(label, title):
    body = 'Solo apretá "Submit new issue". No hace falta escribir nada más.'
    url = f"https://github.com/{REPO}/issues/new?title={quote_plus(title)}&body={quote_plus(body)}"
    return f"[{label}]({url})"


def captured(board, color):
    """Piezas del color `color` que ya no estan en el tablero, en glifos."""
    glyphs = []
    for piece_type in (chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT, chess.PAWN):
        missing = max(START_COUNT[piece_type] - len(board.pieces(piece_type, color)), 0)
        glyphs.append(chess.Piece(piece_type, color).unicode_symbol() * missing)
    return "".join(glyphs) or "—"


def material_balance(board):
    total = 0
    for piece_type, value in PIECE_VALUE.items():
        total += value * (len(board.pieces(piece_type, chess.WHITE)) - len(board.pieces(piece_type, chess.BLACK)))
    return total


def move_rows(board, ply):
    by_origin = {}
    for move in board.legal_moves:
        by_origin.setdefault(move.from_square, []).append(move)
    rows = []
    for origin in sorted(by_origin):
        piece = board.piece_at(origin)
        links = []
        for move in sorted(by_origin[origin], key=lambda m: (m.to_square, m.promotion or 0)):
            label = chess.square_name(move.to_square)
            if move.promotion:
                label += "=" + PIECE_ES[chess.piece_symbol(move.promotion).upper()]
            if board.is_capture(move):
                label = "×" + label
            if board.is_castling(move):
                label += " (enroque)"
            links.append(issue_link(label, f"chess|move|{move.uci()}|{ply}"))
        rows.append(f"| **{piece.unicode_symbol()} {chess.square_name(origin)}** | {' · '.join(links)} |")
    return rows


def history_lines(state, limit=8):
    lines = []
    total = len(state["moves"])
    for index in range(max(total - limit, 0), total):
        entry = state["moves"][index]
        number = f"{index // 2 + 1}." if index % 2 == 0 else f"{index // 2 + 1}..."
        who = entry["by"] if entry["by"] == ENGINE_NAME else f"[@{entry['by']}](https://github.com/{entry['by']})"
        lines.append(f"| {number} {es_san(entry['san'])} | {who} |")
    return lines


def leaderboard_lines(state, limit=10):
    players = state["stats"]["players"]
    ranked = sorted(players.items(), key=lambda kv: (-kv[1]["moves"], kv[0].lower()))[:limit]
    return [f"| {data['moves']} | {data['mates']} | [@{name}](https://github.com/{name}) |" for name, data in ranked]


def render_board(board, state):
    last = board.peek() if board.move_stack else None
    check = board.king(board.turn) if board.is_check() else None
    svg = chess.svg.board(board, lastmove=last, check=check, size=480, coordinates=True, colors=BOARD_COLORS)
    BOARD_FILE.parent.mkdir(exist_ok=True)
    BOARD_FILE.write_text(svg, encoding="utf-8")


def render_block(state, board):
    ply = len(state["moves"])
    image = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/chess/board.svg?v={state['game']}-{ply}"
    stats = state["stats"]
    parts = [
        f'<p align="center"><sub>{ENGINE_BIO.format(elo=ENGINE_ELO)}</sub></p>',
        "",
        f'<p align="center"><img src="{image}" alt="Tablero de ajedrez, partida {state["game"]}, jugada {ply}" width="480"></p>',
        "",
    ]

    if state["result"]:
        status = f"<b>Fin de la partida {state['game']}.</b> {describe_result(state['result'])}"
    else:
        status = f"<b>Partida {state['game']}.</b> Es tu turno: mové una pieza blanca."
        if board.is_check():
            status += " ¡Jaque!"
    if state["moves"]:
        last = state["moves"][-1]
        status += f" Última jugada: {es_san(last['san'])} ({last['by']})."
    parts += [f'<p align="center">{status}</p>', ""]

    say = state.get("say") or SAYINGS["greeting"][0]
    parts += [f'<p align="center"><i>{ENGINE_NAME}: «{say}»</i></p>', ""]

    balance = material_balance(board)
    material = f"{balance:+d}" if balance else "igualado"
    parts += [
        f'<p align="center"><sub>Capturaste: {captured(board, chess.BLACK)} · '
        f"{ENGINE_NAME} capturó: {captured(board, chess.WHITE)} · Material: {material}</sub></p>",
        "",
    ]

    if state["result"]:
        parts += [f'<p align="center">{issue_link("Empezar una partida nueva", f"chess|new|{ply}")}</p>']
    else:
        parts += ["| Pieza | Destinos: elegí uno y apretá *Submit new issue* |", "| --- | --- |"]
        parts += move_rows(board, ply)
    parts += [""]

    parts += [
        "<details>",
        "<summary>Historial, ranking y cómo funciona</summary>",
        "",
        "**Últimas jugadas**",
        "",
        "| Jugada | Quién |",
        "| --- | --- |",
        *(history_lines(state) or ["| — | — |"]),
        "",
        f"**Récord total:** visitantes {stats['visitors']} · {ENGINE_NAME} {stats['engine']} · tablas {stats['draws']}",
        "",
        "**Ranking de visitantes** (jugadas totales)",
        "",
        "| Jugadas | Mates | Quién |",
        "| ---: | ---: | --- |",
        *(leaderboard_lines(state) or ["| — | — | — |"]),
        "",
        f"Cada link abre un issue con la jugada ya escrita. Un workflow de GitHub Actions la valida, "
        f"hace que {ENGINE_NAME} (limitado a ~{ENGINE_ELO} Elo) responda con negras y actualiza este perfil. "
        "Si el tablero tarda en cambiar, es la caché de imágenes de GitHub: recargá en un minuto.",
        "",
        "Piezas del tablero: Colin M.L. Burnett (CC BY-SA 3.0), vía `python-chess`.",
        "",
        "</details>",
    ]
    return "\n".join(parts)


def write_readme(state, board):
    text = README_FILE.read_text(encoding="utf-8")
    pattern = re.compile(r"(<!-- CHESS:START -->\n).*?(\n<!-- CHESS:END -->)", re.S)
    if not pattern.search(text):
        raise RuntimeError("Faltan los marcadores CHESS:START / CHESS:END en el README.")
    block = render_block(state, board)
    README_FILE.write_text(pattern.sub(lambda m: m.group(1) + block + m.group(2), text), encoding="utf-8")


def write_message(text):
    path = os.environ.get("MESSAGE_FILE")
    if path:
        Path(path).write_text(text + "\n", encoding="utf-8")
    print(text)


def refresh(state):
    board = build_board(state)
    render_board(board, state)
    write_readme(state, board)
    save_state(state)


def run_issue():
    title = os.environ.get("EVENT_ISSUE_TITLE", "")
    user = os.environ.get("EVENT_USER_LOGIN", "")
    state = load_state()
    try:
        if not LOGIN.match(user):
            raise MoveError("Usuario no válido.")
        if match := MOVE_TITLE.match(title):
            _, message = handle_move(state, user, match.group(1), int(match.group(2)))
        elif match := NEW_TITLE.match(title):
            _, message = handle_new(state, user, int(match.group(1)))
        else:
            raise MoveError("El título del issue no es una jugada válida.")
    except MoveError as error:
        write_message(f"No se pudo aplicar la jugada: {error}")
        return
    refresh(state)
    write_message(f"{message} El tablero del perfil ya está actualizado.\n\n> **{ENGINE_NAME}:** {state['say']}")


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "init":
        refresh(load_state())
    elif command == "issue":
        run_issue()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
