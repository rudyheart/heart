"""Python 策略示例。修改 Strategy.act 来选择每回合的命令。"""
from engine.rules import DIRS, OPP
from sdk import InitState, TurnState


LEFT = {'U': 'L', 'L': 'D', 'D': 'R', 'R': 'U'}
SIDE = 4


class Strategy:
    def __init__(self, init: InitState):
        self.steps = 0                      # 出门后沿当前方向已走的步数

    def safe(self, st, d):
        """用规则引擎模拟一回合：对手假设沿原方向走，看自己会不会报废。"""
        g = st.game.copy()
        cmds = [None, None]
        cmds[st.seat] = d
        g.step(cmds)
        return g.beams[st.seat].alive

    def act(self, st: TurnState) -> str:
        g, me, s = st.game, st.me, st.seat
        if not me.alive:
            return me.dir
        home = g.owner[g.idx(me.x, me.y)] == s
        if home:
            self.steps = 0
            want = me.dir
        else:
            self.steps += me.speed
            want = me.dir
            if self.steps >= SIDE:
                self.steps = 0
                want = LEFT[me.dir]
        order = [want] + [d for d in DIRS if d != want and d != OPP[me.dir]]
        for d in order:
            if self.safe(st, d):
                return d
        return want
