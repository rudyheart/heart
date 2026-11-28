"""bot001 随机：每回合在不撞缺陷、不出界、不扫过自己轮廓、不掉头的方向里随机选一个。策略说明见 README.md。

默认使用地图编号和出生点确定随机数种子。
"""
import os
import random

from engine.rules import DIRS, DX, DY, OPP
from sdk import InitState, TurnState


class Strategy:
    def __init__(self, init: InitState):
        self.rng = random.Random('%s-%s-%s' % (init.map.id, init.seat, os.environ.get('ARENA_SEED', '0')))

    def act(self, state: TurnState) -> str:
        s = state.seat
        me = state.beams[s]
        if not me.alive:
            return me.dir
        board = state.map
        steps = board.move_steps(me.x, me.y)
        ok = []
        for d in DIRS:
            if d == OPP[me.dir]:
                continue
            path = [(me.x + i * DX[d], me.y + i * DY[d]) for i in range(1, steps + 1)]
            if all(0 <= x < board.size and 0 <= y < board.size
                   and board.terrain[y][x] != '#' and state.contour[y][x] != str(s) for x, y in path):
                ok.append(d)
        return self.rng.choice(ok) if ok else me.dir
