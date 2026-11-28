"""team121 strategy entry point. Communication and session lifetime belong to the SDK."""
from collections import deque
import time

if __package__:
    from .board import Board, Budget, BudgetExpired, DIRS, Geometry, NEXT, get, heading, label, map_signature, popcount, terrain_cell
    from .config import Config
    from .probe import ProbeTimeout, probe_window
    from .search import earliest, enemy_arrivals, frontiers, loop_search, remote_frontier, route_to
else:  # Some templates import 'strategy' from the bot directory.
    from board import Board, Budget, BudgetExpired, DIRS, Geometry, NEXT, get, heading, label, map_signature, popcount, terrain_cell
    from config import Config
    from probe import ProbeTimeout, probe_window
    from search import earliest, enemy_arrivals, frontiers, loop_search, remote_frontier, route_to


def get_beams(state, side):
    me, enemy = get(state, "me"), get(state, "enemy")
    beams = get(state, "beams", ())
    if me is None and beams:
        me = beams[side]
    if enemy is None and beams:
        enemy = beams[1 - side]
    return me, enemy


def fallback(state, game_map, me, enemy, side, d):
    """Bounded local answer even before map preprocessing or any search."""
    rows = get(game_map, "terrain")
    x, y = get(me, "x"), get(me, "y")
    if not rows or not isinstance(x, int) or not isinstance(y, int):
        return d
    boost = get(game_map, "move_steps")
    steps = 2 if boost and boost(x, y) >= 2 else 1
    owner, contour = get(state, "owner"), get(state, "contour")
    ex, ey = get(enemy, "x"), get(enemy, "y")
    best, best_key = d, None
    for cmd in NEXT[d]:
        dx, dy = ((0,-1),(1,0),(0,1),(-1,0))[cmd]
        good, home, distance = True, False, 1000
        for k in range(1, steps + 1):
            nx, ny = x + k * dx, y + k * dy
            if not (0 <= ny < len(rows) and 0 <= nx < len(rows[ny])) or terrain_cell(rows[ny][nx]) == "#":
                good = False
                break
            if contour is not None and label(contour[ny][nx], contour=True) in (side, -2):
                good = False
                break
            if isinstance(ex, int) and isinstance(ey, int) and get(enemy, "alive", True):
                distance = min(distance, abs(ex - nx) + abs(ey - ny))
            home = owner is not None and label(owner[ny][nx]) == side
        key = (good, distance > 2, home, min(4, distance), cmd == d)
        if best_key is None or key > best_key:
            best, best_key = cmd, key
    return best


def contact(a, b, ours, theirs, safe_targets=0):
    """Only two simultaneous movers collide. Destination ownership protects that mover."""
    for next_a, next_b in zip(ours, theirs):
        if not next_a or not next_b or next_a == a or next_b == b:
            return False
        if (next_a == next_b or (next_a == b and next_b == a)) and not ((1 << next_a) & safe_targets):
            return True
        a, b = next_a, next_b
    return False


def safety_options(board, side, start, direction, enemy_p, enemy_d, enemy_alive):
    g = board.g
    occupied = board.trails[side] | board.unknown
    threats = []
    if enemy_alive and enemy_p:
        for reply in NEXT[enemy_d]:
            path = g.edges[enemy_p][reply]
            if path[0] and not board.trails[1 - side] & g.bits[path[0]]:
                # If they crash on the second cell, the first can still matter.
                threats.append(path)
    ranks = {}
    for cmd in NEXT[direction]:
        a, b = g.edges[start][cmd]
        if not b or occupied & g.edge_masks[start][cmd]:
            continue
        ranks[cmd] = sum(contact(start, enemy_p, (a, b), path, board.owns[side]) for path in threats)
    return ranks


class Strategy:
    def __init__(self, init, config=None, clock=time.perf_counter):
        self.init = init
        self.seat = 1 if get(init, "seat", 0) == 1 else 0
        self.config = config or Config()
        self.clock = clock
        self.geometry = None
        self.static_map = None
        self.total_turns = int(get(init, "max_turns", self.config.max_turns))
        self.actions = ()
        self.expected = None
        self.relocating = False
        self.calls = 0
        self.sim_high_ms = 0.0
        self.enemy_headings = deque(maxlen=6)
        self._last_metrics = {}

    def clear(self):
        self.expected, self.actions = None, ()
        self.relocating = False

    def act(self, state):
        c = self.config
        budget = Budget(min(c.budget_ms, c.referee_ms), self.clock)
        answer = 0 if self.seat == 0 else 2
        self._last_metrics = {"phase": "fallback", "sim_calls": 0}
        try:
            me, enemy = get_beams(state, self.seat)
            answer = heading(me, self.seat)
            if me is None or not get(me, "alive", True) or get(me, "restart_in", 0) > 0:
                self.clear()
                self._last_metrics["phase"] = "inactive"
                return DIRS[answer]
            game_map = get(state, "map") or get(self.init, "map")
            answer = fallback(state, game_map, me, enemy, self.seat, answer)
            if game_map is not self.static_map or self.geometry is None:
                sig = map_signature(game_map, budget)
                if self.geometry is None or self.geometry.signature != sig:
                    self.geometry = Geometry(sig, budget)
                    self.clear()
                # The official GameMap is a frozen dataclass reused throughout a match.
                params = getattr(type(game_map), "__dataclass_params__", None)
                self.static_map = game_map if params and params.frozen else None
            board = Board(self.geometry, state, budget)
            p, d = self.geometry.pos(me), heading(me, self.seat)
            ep, ed = self.geometry.pos(enemy), heading(enemy, 1 - self.seat)
            alive = enemy is not None and get(enemy, "alive", True) and get(enemy, "restart_in", 0) == 0
            if alive:
                self.enemy_headings.append(ed)
            else:
                self.enemy_headings.clear()
            if not p:
                self.clear()
                return DIRS[answer]
            delay = 0
            if not alive:
                spawns = self.geometry.spawns
                ep = spawns[1 - self.seat] if len(spawns) > 1 - self.seat else ep
                ed = 2 if self.seat == 0 else 0
                # Respawn touches the home cell at the end of the last inactive turn.
                delay = max(0, int(get(enemy, "restart_in", 0)))
            ranks = safety_options(board, self.seat, p, d, ep, ed, alive)
            if not ranks:
                self.clear()
                return DIRS[answer]
            game = get(state, "game")
            allowed = self._safe_set(board, game, p, ep, ed, alive, ranks, budget)
            answer = min(allowed, key=lambda cmd: (ranks[cmd], cmd != answer, cmd != d, cmd))
            if self.expected != (p, d):
                self.clear()
            # Older templates sometimes omit the turn; keep a monotone callback count.
            turn = get(state, "turn")
            turn = turn if isinstance(turn, int) and turn >= 1 else self.calls + 1
            remaining = max(0, self.total_turns - turn + 1)
            if self.actions and self.actions[0] not in allowed:
                self.clear()
            steps, phase = self._decide(board, p, d, ep, ed, delay, alive, allowed,
                                        remaining, budget)
            if steps and steps[0] in allowed:
                answer = steps[0]
                self.actions = tuple(steps[1:])
                self.relocating = phase == "relocate"
                self.expected = (self.geometry.edges[p][answer][1], answer)
            else:
                self.clear()
            self._last_metrics["phase"] = phase
        except BudgetExpired:
            self.clear()
            self._last_metrics["phase"] = "budget"
        except Exception as exc:
            self.clear()
            self._last_metrics["exception"] = type(exc).__name__
        finally:
            self.calls += 1
            self._last_metrics["last_act_ms"] = (self.clock() - budget.start) * 1000
        return DIRS[answer]

    def _safe_set(self, board, game, p, ep, ed, alive, ranks, budget):
        c = self.config
        best = min(ranks.values())
        local = {cmd for cmd, n in ranks.items() if n == best}
        tactical = any(ranks.values()) or any(board.g.edge_masks[p][cmd] & board.trails[1-self.seat]
                                               for cmd in ranks)
        copy = get(game, "copy")
        if not c.use_sim or not tactical or not callable(copy):
            return local
        # Only compare fully evaluated candidates. A missing beam or failed reply
        # is an unknown outcome; it must never be counted as a survived reply.
        replies = NEXT[ed] if alive else (ed,)
        worst, observed_deaths = {}, set()

        def partial():
            proven = {cmd for cmd, rank in worst.items() if rank[0]}
            if proven:
                return {cmd for cmd in proven if ranks[cmd] == min(ranks[k] for k in proven)}
            candidates = ranks.keys() - observed_deaths
            if not candidates:
                return local
            return {cmd for cmd in candidates if ranks[cmd] == min(ranks[k] for k in candidates)}
        for cmd in sorted(ranks, key=ranks.get):
            outcomes = []
            for reply in replies:
                if budget.left_ms() <= max(c.sim_reserve_ms, 2 * self.sim_high_ms):
                    return partial()
                t = self.clock()
                try:
                    window_ms = min(c.sim_call_ms, budget.left_ms() - c.sim_reserve_ms)
                    with probe_window(window_ms) as enabled:
                        if not enabled:
                            self._last_metrics["sim_skip"] = "unavailable_timer"
                            return partial()
                        sim = copy()
                        if sim is game:
                            return partial()
                        moves = [None, None]
                        moves[self.seat], moves[1-self.seat] = DIRS[cmd], DIRS[reply]
                        sim.step(moves)
                        self._last_metrics["sim_calls"] += 1
                        beams = get(sim, "beams")
                        if beams is None or get(beams[self.seat], "alive") is None:
                            return partial()
                        survived = bool(get(beams[self.seat], "alive"))
                        outcomes.append(survived)
                        if not survived:
                            observed_deaths.add(cmd)
                except ProbeTimeout:
                    self._last_metrics["sim_timeout"] = True
                    return partial()
                except Exception as exc:
                    self._last_metrics["sim_error"] = type(exc).__name__
                    return partial()
                finally:
                    self.sim_high_ms = max(self.sim_high_ms, (self.clock() - t) * 1000)
            # An observed tendency to keep heading only breaks equal worst-case results.
            history = list(self.enemy_headings)
            straight = sum(a == b for a, b in zip(history, history[1:]))
            favor_straight = len(history) >= 3 and 2 * straight >= len(history) - 1
            worst[cmd] = (min(outcomes), sum(outcomes), outcomes[0] if favor_straight else 0, -ranks[cmd])
        return {cmd for cmd, result in worst.items() if result == max(worst.values())}

    def _decide(self, board, p, d, ep, ed, delay, alive, allowed, remaining, budget):
        side, other, cfg, g = self.seat, 1-self.seat, self.config, board.g
        at_home = bool(g.bits[p] & board.owns[side])
        horizon = min(remaining, cfg.enemy_horizon)
        if not remaining:
            return (), "end"
        if cfg.allow_intercept and alive and board.trails[other]:
            # Contact with an existing opposing trail is resolved before either
            # side can close. Prefer the first substep to a contested second step.
            for cmd in sorted(allowed, key=lambda k: k != d):
                first, last = g.edges[p][cmd]
                if board.trails[other] & g.bits[first]:
                    return (cmd,), "cut_now"
        if self.relocating and self.actions and at_home:
            here = p
            for cmd in self.actions:
                mask = g.edge_masks[here][cmd]
                if not mask or mask & (~board.owns[side] | board.trails[0] | board.trails[1]):
                    break
                here = g.edges[here][cmd][1]
            else:
                return self.actions, "relocate"
            self.clear()
        if remaining == 1 and at_home and not board.trails[side]:
            urgent = bool(board.trails[other]) and any(
                g.edge_masks[p][cmd] & board.trails[other] for cmd in allowed)
            if not urgent:
                return (), "hold"
        times = enemy_arrivals(board, other, ep, ed, delay, horizon, budget)
        if self.actions and len(self.actions) <= remaining:
            future = self._suffix_trail(board, p)
            if future is not None:
                threatened = earliest(board.trails[side] | future, times)
                if len(self.actions) + 1 < threatened:
                    return self.actions, "plan" if at_home else "continue"
            self.clear()
        if not at_home or board.trails[side]:
            home = route_to(board, side, p, d, board.owns[side], min(remaining, cfg.max_macro),
                            budget, first_allowed=allowed)
            if home:
                return home.steps[:1], "return"
            return (), "escape"

        attack = None
        if cfg.allow_intercept and alive and board.trails[other] and not (board.owns[other] & g.bits[ep]):
            cut = route_to(board, side, p, d, board.trails[other], min(cfg.max_cut, remaining),
                           budget, attack=True, first_allowed=allowed)
            if cut and len(cut.steps) < earliest(board.owns[other], times):
                # Denial value for one feasible enemy return, conditional on the current map.
                reply = route_to(board, other, ep, ed, board.owns[other], min(remaining, 12), budget)
                potential = (board.gain(other, board.capture(other, reply.drawn, budget))
                             if reply else popcount(board.trails[other] & g.value_cells))
                margin = earliest(cut.drawn, times) - len(cut.steps)
                if margin > 0 or len(cut.steps) == 1:
                    attack = (max(0, potential) + min(remaining, 5)) / (len(cut.steps) + 4) ** .84, cut
        if remaining < 2:
            return (attack[1].steps[:1], "intercept") if attack else ((), "hold")
        exits = frontiers(board, side, p, d, budget, allowed)
        plan = loop_search(board, side, exits, times, remaining, cfg, budget)
        if attack and (not plan or attack[0] > plan[0]):
            return attack[1].steps[:1], "intercept"
        if cfg.relocate and (not plan or plan[0] < 1.15) and remaining > 9:
            travel = remote_frontier(board, side, p, d, allowed, times, remaining, budget)
            if travel:
                return travel[:12], "relocate"
        if plan:
            self._last_metrics["projected_gain"] = plan[2]
            return plan[1].steps, "expand"
        if exits and exits[0][2] and len(exits[0][2]) + 2 <= remaining:
            return exits[0][2][:1], "to_frontier"
        return (), "hold"

    def _suffix_trail(self, board, p):
        g, side = board.g, self.seat
        own = board.owns[side]
        blocked = board.trails[0] | board.trails[1] | board.unknown
        drawn = 0
        outside = bool(board.trails[side]) or not bool(g.bits[p] & own)
        for i, cmd in enumerate(self.actions):
            a, b = g.edges[p][cmd]
            mask = g.edge_masks[p][cmd]
            if not b or mask & (blocked | drawn):
                return None
            for cell in (a,) if a == b else (a, b):
                if g.bits[cell] & own:
                    if outside and (i != len(self.actions)-1 or not g.bits[b] & own):
                        return None
                else:
                    outside = True
                    drawn |= g.bits[cell]
            p = b
        return drawn if outside and g.bits[p] & own else None

    def end(self, msg):
        self.clear()
        self.geometry = None
