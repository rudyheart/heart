"""bot003 搜索：在 bot002（greedy.py）的基础上，逐条推演候选路线上双方的到达时间，再做取舍。

与 bot002 的区别见 README.md。搜索预算为 100 毫秒，到时就用已经找到的路线。
"""
import time

from engine.rules import DIRS, DX, DY, OPP      # noqa: E402
from sdk import TurnState
from lib import INF, bfs, closure_gain  # noqa: E402
from greedy import GreedyBot                 # noqa: E402

BUDGET = 0.10                     # 每回合推演限时（秒），规则时限 200 毫秒
BIG = [(8, 12), (10, 14), (12, 18), (14, 20)]


class Strategy(GreedyBot):
    margin = 1
    sizes = [(3, 4), (4, 6), (5, 8), (6, 9), (5, 6), (7, 10), (3, 6), (2, 4), (3, 3), (2, 2)] + BIG
    big = True
    remote_n = 4

    def act(self, st: TurnState) -> str:
        self.t0 = time.time()
        g, s, me = st.game, st.seat, st.me
        if not me.alive:
            self.plan = []
            return me.dir
        f = self.facts(st)
        # 1. 回家
        if not f['home'] and f['threat'] <= f['d_home'] + self.margin and f['home_dir']:
            self.plan = []
            return self.dodge(st, f, f['home_dir'])
        # 2. 赛跑式进攻
        a = self.attack(st, f)
        if a and self.ok_step(st, a):
            self.plan = []
            return self.dodge(st, f, a)
        # 3. 推演选路线
        if not self.plan:
            if not f['home']:
                return self.dodge(st, f, f['home_dir'] or me.dir)
            self.plan = self.make_plan(st, f)
        d = self.plan[0]
        if not self.ok_step(st, d):
            self.plan = []
            if not f['home'] and f['home_dir']:
                return self.safe_direction(st, f['home_dir'])
            for d2 in DIRS:
                if self.ok_step(st, d2):
                    return d2
            return me.dir
        nd = self.dodge(st, f, d)
        if nd != d:
            self.plan = []
            return nd
        return self.advance_plan(st, d)

    # ---------------------------------------------------------------- 进攻
    def attack(self, st, f):
        g, s, en = st.game, st.seat, st.enemy
        N = g.map.size
        dist, first = f['mine']
        if not en.alive or not (f['home'] or f['d_home'] + 1 < f['threat']):
            return None
        # 对手轮廓：我先到，它还没回家
        e_here = g.idx(en.x, en.y)
        e_trail = [k for k in range(N * N) if g.trail[k] == 1 - s]
        if e_trail:
            ed, _ = bfs(g, en.x, en.y, blocked=lambda k: g.trail[k] == 1 - s and k != e_here, first_ban=OPP[en.dir])
            back = min([ed[k] for k in range(N * N) if g.owner[k] == 1 - s and ed[k] > 0] or [INF])
            back_t = (back + f['es'] - 1) // f['es']
            best, bd = INF, None
            for k in e_trail:
                if 0 < dist[k] < best:
                    best, bd = dist[k], first[k]
            if bd and best <= back_t and best <= 20:
                return bd
        return None

    # ---------------------------------------------------------------- 避免撞头
    def dodge(self, st, f, d):
        g, s, me, en = st.game, st.seat, st.me, st.enemy
        d = self.safe_direction(st, d)
        if not en.alive:
            return d
        nx, ny = me.x + DX[d], me.y + DY[d]
        if not g.inb(nx, ny) or g.owner[g.idx(nx, ny)] == s:
            return d                                     # 目标格在己方图形区：撞头时我安全
        reach = f['es']
        if abs(en.x - nx) + abs(en.y - ny) > reach:
            return d
        for d2 in DIRS:
            if d2 == d or not self.ok_step(st, d2):
                continue
            x2, y2 = me.x + DX[d2], me.y + DY[d2]
            if abs(en.x - x2) + abs(en.y - y2) > reach or g.owner[g.idx(x2, y2)] == s:
                return d2
        return d

    # ---------------------------------------------------------------- 推演选路线
    def plan_value(self, st, f, moves, trail):
        g, s = st.game, st.seat
        T = len(moves)
        gain, steal, got = closure_gain(g, s, trail)
        if gain <= 0:
            return -INF
        v = self.value_of(st, got, steal, T)
        return v / (T + 8.0)              # 单位时间收益（bot002 用 价值×10 − 步数）

    budget = BUDGET
