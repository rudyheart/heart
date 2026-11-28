"""Simulate turns, beam movement, and territory capture."""
from functools import lru_cache

from .maps import GameMap
from .rules import DIRS, DX, DY, OPP, PARAMS, RULESET, compare_metrics

__all__ = ['Beam', 'Game']


class Beam(object):
    __slots__ = ('x', 'y', 'dir', 'speed', 'alive', 'respawn')

    def __init__(self, x, y, d):
        self.x, self.y, self.dir = x, y, d
        self.speed, self.alive, self.respawn = 1, True, 0

    def copy(self):
        b = Beam(self.x, self.y, self.dir)
        b.speed, b.alive, b.respawn = self.speed, self.alive, self.respawn
        return b

    def to_dict(self, seat):
        return {'seat': seat, 'x': self.x, 'y': self.y, 'dir': self.dir, 'speed': self.speed,
                'alive': self.alive, 'restart_in': 0 if self.alive else self.respawn}


class Game(object):
    """一局对战的完整状态。

    owner[k]  : -1 无主，0/1 归属
    trail[k]  : -1 无轮廓，0/1 谁的轮廓
    time_us   : 双方累计响应时间（微秒），由裁判累加，用于单局最后一项指标比较
    """

    def __init__(self, board, stage=None, max_turns=None):
        self.map = board if isinstance(board, GameMap) else GameMap.from_payload(board)
        if (stage is not None and stage != RULESET) or self.map.stage != RULESET:
            raise ValueError('地图须使用当前赛题规则')
        self.owner = list(self.map.base)
        self.trail = [-1] * len(self.owner)
        self.lead_turns = [0, 0]
        self.beams = [Beam(*center, direction) for center, direction in zip(self.map.calib, ('U', 'D'))]
        self.turn = 0
        self.max_turns = max_turns or PARAMS['max_turns']
        self.done = False
        self.winner = None         # 0 / 1；未结束、双败或指标全同为 None
        self.reason = ''
        self.time_us = [0, 0]
        self.events = []

    # ------------------------------------------------------------ 基本工具
    def idx(self, x, y):
        return y * self.map.size + x

    def inb(self, x, y):
        size = self.map.size
        return 0 <= x < size and 0 <= y < size

    def val(self, k):
        return PARAMS['key_value'] if self.map.tiles[k] == '*' else 1

    def score(self, s):
        face, edges = territory_value(self, s)
        return face - edges // 3

    def copy(self):
        g = Game.__new__(Game)
        g.__dict__.update(self.__dict__)
        g.owner = self.owner[:]
        g.trail = self.trail[:]
        g.lead_turns = self.lead_turns[:]
        g.time_us = self.time_us[:]
        g.beams = [b.copy() for b in self.beams]
        g.events = []
        return g

    # ------------------------------------------------------------ 结算子过程
    def fill_cells(self, s):
        """s 在当前局面下闭合会得到的格：自己的全部轮廓格 + 被围住的格（不含基板缺陷和敌方校准区）。"""
        N = self.map.size
        NN = N * N
        owner, trail, terrain, base = self.owner, self.trail, self.map.tiles, self.map.base
        wall = [owner[k] == s or trail[k] == s for k in range(NN)]
        seen = [False] * NN
        st = []
        for k in range(NN):
            x, y = k % N, k // N
            if (x == 0 or y == 0 or x == N - 1 or y == N - 1) and not wall[k]:
                seen[k] = True
                st.append(k)
        adjacent = _neighbors(N)
        while st:
            for nk in adjacent[st.pop()]:
                if not seen[nk] and not wall[nk]:
                    seen[nk] = True
                    st.append(nk)
        return [k for k in range(NN) if owner[k] != s and base[k] < 0 and terrain[k] != '#'
                and (trail[k] == s or not seen[k])]

    def step(self, cmds):
        """按起步格决定移动步数，执行一回合。"""
        if self.done:
            return []
        ev = []
        self.turn += 1
        _move_beams(self, cmds, ev)
        scores = [self.score(s) for s in (0, 1)]
        if scores[0] != scores[1]:
            self.lead_turns[int(scores[1] > scores[0])] += 1
        # 胜负
        if self.turn >= self.max_turns:
            self.done = True
            self.winner, self.reason = self.decide()
            ev.append({'t': 'end', 's': self.winner, 'reason': self.reason})
        self.events = ev
        return ev

    def metrics(self):
        return {'score': [self.score(0), self.score(1)],
                'lead_turns': list(self.lead_turns), 'time_us': list(self.time_us)}

    def decide(self):
        """返回胜方出生点编号和胜负原因。"""
        winner, metric = compare_metrics(self.metrics())
        reason = {'score': 'limit', 'time_us': 'time', None: 'tie'}.get(metric, metric)
        return winner, reason

    def forfeit(self, loser, reason):
        """loser 为 -1 表示本局双败。"""
        self.done = True
        self.reason = 'double_loss' if loser == -1 else reason
        self.winner = None if loser == -1 else 1 - loser
        self.events = [{'t': 'end', 's': self.winner, 'reason': self.reason}]


@lru_cache(maxsize=8)
def _neighbors(size):
    return tuple(tuple(ny * size + nx for nx, ny in
                       ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))
                       if 0 <= nx < size and 0 <= ny < size)
                 for y in range(size) for x in range(size))


def territory_value(game, side):
    """Return the weighted area and exposed edge count for one seat."""
    face = edges = 0
    owner = game.owner
    adjacent = _neighbors(game.map.size)
    base = game.map.base
    for k, seat in enumerate(owner):
        if seat == side:
            face += game.val(k)
            if base[k] != side:
                edges += 4 - sum(owner[n] == side for n in adjacent[k])
    return face, edges


def _move_beams(game, cmds, ev):
    cut = [False, False]
    mv = [0, 0]
    # 第 1 步 确定方向，以及走 1 格还是 2 格
    for s in (0, 1):
        b = game.beams[s]
        if not b.alive:
            continue
        command = cmds[s]
        if command in DIRS and command != OPP[b.dir]:
            b.dir = command
        b.speed = mv[s] = game.map.move_steps(b.x, b.y)
    for sub in (1, 2):
        movers = [s for s in (0, 1) if game.beams[s].alive and not cut[s] and mv[s] >= sub]
        if movers:
            _move_one_step(game, movers, cut, ev)
    # 第 7 步 报废与补标
    restart_cut = [False, False]
    for s in (0, 1):
        if cut[s]:
            _down_beam(game, s, ev)
    for s in (0, 1):
        b = game.beams[s]
        if b.alive or cut[s]:
            continue
        b.respawn -= 1
        if b.respawn > 0:
            continue
        cx, cy = game.map.calib[s]
        b.alive, b.x, b.y, b.dir, b.speed, b.respawn = True, cx, cy, ('U' if s == 0 else 'D'), 1, 0
        if game.trail[game.idx(cx, cy)] == 1 - s:      # 重新启动视为移入该格
            restart_cut[1 - s] = True
        ev.append({'t': 'respawn', 's': s})
    for s in (0, 1):
        if restart_cut[s] and game.beams[s].alive:
            _down_beam(game, s, ev)
    for s in (0, 1):                                    # 补标轮廓
        b = game.beams[s]
        if not b.alive:
            continue
        k = game.idx(b.x, b.y)
        if game.owner[k] != s and game.trail[k] != s:
            game.trail[k] = s


def _down_beam(game, seat, events):
    for k, trail_seat in enumerate(game.trail):
        if trail_seat == seat:
            game.trail[k] = -1
    beam = game.beams[seat]
    beam.alive, beam.respawn, beam.speed = False, PARAMS['restart'], 1
    events.append({'t': 'down', 's': seat})


def _move_one_step(game, movers, cut, ev):
    """一个移动步（第 2～6 步）。movers 为本移动步移动的座位。"""
    start_trail = game.trail[:]
    tx, ty = [0, 0], [0, 0]
    moving = [False, False]
    # 第 2 步 撞缺陷与出界
    for s in movers:
        b = game.beams[s]
        tx[s], ty[s] = b.x + DX[b.dir], b.y + DY[b.dir]
        moving[s] = True
        if not game.inb(tx[s], ty[s]) or game.map.tiles[game.idx(tx[s], ty[s])] == '#':
            cut[s] = True
            moving[s] = False
            ev.append({'t': 'crash', 's': s})
    # 第 3 步 束斑碰撞
    if moving[0] and moving[1]:
        a, b = game.beams
        same = tx[0] == tx[1] and ty[0] == ty[1]
        swap = tx[0] == b.x and ty[0] == b.y and tx[1] == a.x and ty[1] == a.y
        if same or swap:
            for s in (0, 1):
                if game.owner[game.idx(tx[s], ty[s])] != s:
                    cut[s] = True
                    moving[s] = False
            ev.append({'t': 'collide'})
    # 第 4 步 移动
    for s in (0, 1):
        if moving[s]:
            game.beams[s].x, game.beams[s].y = tx[s], ty[s]
    # 第 5 步 扫过轮廓（按本移动步开始时的轮廓判断）
    for s in (0, 1):
        if not moving[s]:
            continue
        k = game.idx(game.beams[s].x, game.beams[s].y)
        tr = start_trail[k]
        if tr == s:
            cut[s] = True
            ev.append({'t': 'selfcut', 's': s})
        elif tr == 1 - s:
            cut[1 - s] = True
            ev.append({'t': 'cut', 's': s})
    _extend_and_close(game, moving, cut, ev)


def _extend_and_close(game, moving, cut, events):
    closers = []
    for seat, beam in enumerate(game.beams):
        if moving[seat] and not cut[seat]:
            k = game.idx(beam.x, beam.y)
            if game.owner[k] != seat:
                game.trail[k] = seat
            elif seat in game.trail:
                closers.append(seat)

    claims = {s: set(game.fill_cells(s)) for s in closers}
    shared = claims[0] & claims[1] if len(closers) == 2 else set()
    for s in closers:
        captured = claims[s] - shared
        for k in captured:
            game.owner[k] = s
        events.append({'t': 'close', 's': s, 'gain': sum(game.val(k) for k in captured)})
    if closers:
        for k, s in enumerate(game.trail):
            if s in claims:
                game.trail[k] = -1
