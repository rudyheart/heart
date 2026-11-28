"""bot002 贪心的完整实现（与 bots/bot002/strategy.py 的 Strategy 相同），bot003 在它的基础上扩展。

复制一份放在这里，是为了让每个 Bot 目录都能独立提交。策略说明见 bots/bot002/README.md。
"""
import os
import time

from engine.rules import DIRS, DX, DY, OPP
NO_BUDGET = bool(os.environ.get('ARENA_NO_BUDGET'))   # 设为 1 时不限时：结果与机器快慢无关，便于复现
from lib import INF, bfs, path_to, enemy_reach, loop_candidates, closure_gain


class GreedyBot(object):
    margin = 2
    hunt_range = 8
    sizes = [(4, 6), (5, 8), (6, 9), (5, 7), (3, 5), (3, 3), (2, 2)]
    big = False
    remote_n = 3

    def __init__(self, init):
        self.plan = []

    # ---------------------------------------------------------------- 工具
    def ok_step(self, st, d):
        g, me = st.game, st.me
        if d == OPP[me.dir]:
            return False
        for i in range(1, st.map.move_steps(me.x, me.y) + 1):
            x, y = me.x + i * DX[d], me.y + i * DY[d]
            if not g.inb(x, y) or g.map.tiles[g.idx(x, y)] == '#' or g.trail[g.idx(x, y)] == st.seat:
                return False
        return True

    def safe_direction(self, st, preferred):
        if preferred and self.ok_step(st, preferred):
            return preferred
        return next((d for d in DIRS if self.ok_step(st, d)), st.me.dir)

    def advance_plan(self, st, d):
        me = st.me
        steps = st.map.move_steps(me.x, me.y)
        if self.plan[:steps] == [d] * steps:
            del self.plan[:steps]
        else:
            self.plan.clear()  # 加速移动的两步沿同一方向
        return d

    def facts(self, st):
        """每回合都要用的量：回家距离、对手威胁（到我轮廓的最少回合数）。"""
        g, s, me, en = st.game, st.seat, st.me, st.enemy
        N = g.map.size
        here = g.idx(me.x, me.y)
        f = {'home': g.owner[here] == s}
        mine = bfs(g, me.x, me.y, blocked=lambda k: g.trail[k] == s and k != here, first_ban=OPP[me.dir])
        f['mine'] = mine
        dist, first = mine
        d_home, home_dir = 0, None
        if not f['home']:
            d_home = INF
            for k in range(N * N):
                if g.owner[k] == s and 0 < dist[k] < d_home:
                    d_home, home_dir = dist[k], first[k]
        f['d_home'], f['home_dir'] = d_home, home_dir
        es = st.map.move_steps(en.x, en.y) if en.alive else 1
        f['es'] = es
        reach = enemy_reach(g, en, es)
        f['reach'] = reach
        threat = INF
        for k in range(N * N):
            if g.trail[k] == s:
                threat = min(threat, reach[k])
        f['threat'] = threat
        return f

    def value_of(self, st, got, steal, length):
        projected = st.game.copy()
        side = st.seat
        for k in got:
            projected.owner[k] = side
        return (projected.score(side) - projected.score(1 - side)) - (st.score[side] - st.score[1 - side])

    def plan_value(self, st, f, moves, trail):
        g, s = st.game, st.seat
        gain, steal, got = closure_gain(g, s, trail)
        if gain <= 0:
            return -INF
        return self.value_of(st, got, steal, len(moves)) * 10 - len(moves)

    def unsafe(self, f, moves, trail):
        """对手能在闭合前赶到这条路线的某个轮廓格。"""
        return min([f['reach'][k] for k in trail] or [INF]) <= len(moves)

    def search(self, st, f, sx, sy, cur_dir, pre, sizes):
        g, s = st.game, st.seat
        best, best_v = None, -INF
        for moves, trail, inner in loop_candidates(g, s, sx, sy, cur_dir, sizes, big=self.big):
            full = pre + moves
            if len(full) > 40 and self.unsafe(f, full, trail):
                continue
            v = self.plan_value(st, f, full, trail)
            if v > best_v:
                best, best_v, best_t = full, v, trail
            if self.over_budget():
                break
        return (best, best_v, self.unsafe(f, best, best_t)) if best else (None, -INF, True)

    budget = 0.08                 # 每回合找路线最多用 80 毫秒（规则时限 200 毫秒）

    def over_budget(self):
        if NO_BUDGET:
            return False
        return time.time() - getattr(self, 't0', time.time()) > self.budget

    def remote_starts(self, st, f, n):
        """图形区边缘上离对手最远的几个出发点（20 步以内），返回 [(x, y, 到那里的路线)]。"""
        g, s, me = st.game, st.seat, st.me
        N = g.map.size
        dist, first = f['mine']
        reach = f['reach']
        edge = []
        for k in range(N * N):
            if g.owner[k] != s or not (0 < dist[k] <= 20):
                continue
            x, y = k % N, k // N
            if any(g.inb(x + DX[d], y + DY[d]) and g.owner[g.idx(x + DX[d], y + DY[d])] != s
                   and g.map.tiles[g.idx(x + DX[d], y + DY[d])] != '#' for d in DIRS):
                edge.append((-(reach[k] - dist[k]), dist[k], k))
        edge.sort()
        out, used = [], []
        for _, _, k in edge:
            x, y = k % N, k // N
            if any(abs(x - ux) + abs(y - uy) < 6 for ux, uy in used):
                continue
            p = path_to(f['mine'], g, g.idx(me.x, me.y), k)
            if p:
                out.append((x, y, p))
                used.append((x, y))
            if len(out) >= n:
                break
        return out

    def make_plan(self, st, f):
        g, s, me = st.game, st.seat, st.me
        best, best_v, risky = self.search(st, f, me.x, me.y, me.dir, [], self.sizes)
        if best is None or risky:
            # 附近的路线都会被对手赶上：去图形区边缘离对手远的地方
            for x, y, pre in self.remote_starts(st, f, self.remote_n):
                if self.over_budget():
                    break
                b2, v2, r2 = self.search(st, f, x, y, pre[-1], pre, self.sizes)
                if b2 and not r2 and (best is None or risky or v2 > best_v):
                    best, best_v, risky = b2, v2, r2
        if best:
            return best
        # 没有可行的绕圈：走到图形区边缘的另一个位置
        dist, first = f['mine']
        N = g.map.size
        cand = []
        for k in range(N * N):
            if g.owner[k] != s or dist[k] <= 0:
                continue
            x, y = k % N, k // N
            for d in DIRS:
                nx, ny = x + DX[d], y + DY[d]
                if g.inb(nx, ny) and g.owner[g.idx(nx, ny)] != s and g.map.tiles[g.idx(nx, ny)] != '#':
                    cand.append((dist[k], k))
                    break
        cand.sort()
        for _, k in cand[:12]:
            p = path_to(f['mine'], g, g.idx(me.x, me.y), k)
            if p:
                return p
        return [me.dir]

    def attack(self, st, f):
        """bot002：最近的对手轮廓在 hunt_range 步内，且自己安全，就去扫。"""
        g, s = st.game, st.seat
        dist, first = f['mine']
        best, bd = INF, None
        for k in range(g.map.size * g.map.size):
            if dist[k] <= 0:
                continue
            if g.trail[k] == 1 - s:
                if dist[k] < best:
                    best, bd = dist[k], first[k]
        if bd and best <= self.hunt_range and (f['home'] or f['d_home'] + 1 < f['threat']):
            return bd
        return None

    def act(self, st):
        self.t0 = time.time()
        g, s, me = st.game, st.seat, st.me
        if not me.alive:
            self.plan = []
            return me.dir
        f = self.facts(st)
        if not f['home'] and f['threat'] <= f['d_home'] + self.margin and f['home_dir']:
            self.plan = []
            return self.safe_direction(st, f['home_dir'])
        a = self.attack(st, f)
        if a and self.ok_step(st, a):
            self.plan = []
            return a
        if not self.plan:
            if not f['home']:
                return self.safe_direction(st, f['home_dir'])
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
        return self.advance_plan(st, d)

