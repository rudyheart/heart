"""Encode the version 1 Bot messages and parse turn replies."""
import json
import re

from .rules import valid_command

MAX_REQUEST_BYTES = 65536
MAX_RESPONSE_BYTES = 16384
RUN_ID = re.compile(r'run-[0-9]+\.[1-9][0-9]*')


def json_object(body):
    def bad_constant(value):
        raise ValueError('invalid JSON constant: ' + value)

    obj = json.loads(body.decode('utf-8'), parse_constant=bad_constant)
    if not isinstance(obj, dict):
        raise ValueError('expected a JSON object')
    return obj


def init_message(game, seat):
    return {
        'type': 'init', 'seat': seat, 'max_turns': game.max_turns, 'map': game.map.to_payload(),
    }


def grid_rows(game):
    """owner / contour 两张网格，每行一个字符串。"""
    N = game.map.size
    own, con = [], []
    for y in range(N):
        o, c = [], []
        for x in range(N):
            k = y * N + x
            w = game.owner[k]
            o.append('.' if w < 0 else str(w))
            t = game.trail[k]
            c.append('.' if t < 0 else str(t))
        own.append(''.join(o)); con.append(''.join(c))
    return own, con


def board_status(game):
    return {
        'beams': [b.to_dict(s) for s, b in enumerate(game.beams)],
        **game.metrics(),
    }


def turn_message(game):
    own, con = grid_rows(game)
    return {'type': 'turn', 'turn': game.turn + 1, 'owner': own, 'contour': con, **board_status(game)}


def end_message(game):
    return {'type': 'end', 'turn': game.turn, 'winner': game.winner, 'reason': game.reason,
            **game.metrics()}


def dumps(msg):
    return json.dumps(msg, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def parse_reply(body):
    """解析回合回复（str 或 bytes）。返回 (turn, cmd)；格式不对返回 None。cmd 只接受 U/D/L/R。"""
    try:
        obj = json_object(body.encode('utf-8') if isinstance(body, str) else body)
    except (ValueError, TypeError, AttributeError, RecursionError):
        return None
    t, cmd = obj.get('turn'), obj.get('cmd')
    if type(t) is not int or not valid_command(cmd):
        return None
    return t, cmd
