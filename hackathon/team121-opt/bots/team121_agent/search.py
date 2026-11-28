"""Search on commanded turns. Every graph edge carries both visited substeps."""
from collections import deque
from dataclasses import dataclass
import heapq

if __package__:
    from .board import BudgetExpired, INF, NEXT, popcount
else:
    from board import BudgetExpired, INF, NEXT, popcount


@dataclass(frozen=True)
class Route:
    p: int
    steps: tuple = ()
    drawn: int = 0
    closed: bool = False


def enemy_arrivals(board, side, start, direction, delay, horizon, budget):
    """Optimistic cut times. Relax trails after ply one so a stale wall cannot imply safety."""
    g = board.g
    times = [INF] * g.size
    if not start:
        # Unlocated live opponent: no cell may be assumed to be out of reach.
        return [0] * g.size
    visits = bytearray(4 * g.size)
    q = deque([(start, direction, delay)])
    visits[4 * start + direction] = 1
    times[start] = delay
    pops = 0
    while q:
        p, d, t = q.popleft()
        if t >= horizon:
            continue
        if not pops % 128:
            budget.check()
        pops += 1
        for cmd in NEXT[d]:
            a, b = g.edges[p][cmd]
            if not a or (t == delay and g.bits[a] & board.trails[side]):
                continue
            when = t + 1
            if times[a] > when:
                times[a] = when
            if not b or (t == delay and g.bits[b] & board.trails[side]):
                continue
            if times[b] > when:
                times[b] = when
            key = 4 * b + cmd
            if not visits[key]:
                visits[key] = 1
                q.append((b, cmd, when))
    return times


def earliest(mask, times):
    best = INF
    while mask:
        low = mask & -mask
        best = min(best, times[low.bit_length() - 1])
        mask ^= low
    return best


def route_to(board, side, start, heading, targets, limit, budget, attack=False, first_allowed=None):
    """Return one validated shortest route found under orientation-state pruning."""
    if not targets or not start or limit <= 0:
        return None
    g = board.g
    own = board.owns[side]
    walls = board.trails[side] | board.unknown
    if not attack:
        walls |= board.trails[1 - side]
    visits = bytearray(4 * g.size)
    visits[4 * start + heading] = 1
    q = deque([(Route(start), heading)])
    pops = 0
    while q:
        route, d = q.popleft()
        if len(route.steps) >= limit:
            continue
        if not pops % 64:
            budget.check()
        pops += 1
        for cmd in NEXT[d]:
            if not route.steps and first_allowed is not None and cmd not in first_allowed:
                continue
            a, b = g.edges[route.p][cmd]
            crossed = g.edge_masks[route.p][cmd]
            if not b or crossed & (walls | route.drawn):
                continue
            touched = bool(crossed & targets)
            # On a boost, accept an early home touch only if the endpoint is also home.
            # Do not chain a fresh trail through the unverified closure substep.
            if touched and not attack and not g.bits[b] & targets:
                continue
            steps = route.steps + (cmd,)
            drawn = route.drawn | (crossed & ~own)
            if touched:
                return Route(b, steps, drawn, not attack)
            key = 4 * b + cmd
            if not visits[key]:
                visits[key] = 1
                q.append((Route(b, steps, drawn), cmd))
    return None


def frontiers(board, side, start, direction, budget, first_allowed):
    """Find nearby oriented exits and a navigation move when the head is deep inside."""
    g, own = board.g, board.owns[side]
    walls = board.trails[0] | board.trails[1] | board.unknown
    q = deque([(start, direction, ())])
    visited = {4 * start + direction}
    exits = []
    distinct = set()
    first_depth = None
    pops = 0
    while q and len(exits) < 16:
        p, d, access = q.popleft()
        if first_depth is not None and len(access) > first_depth + 4:
            break
        if not pops % 64:
            budget.check()
        pops += 1
        for cmd in NEXT[d]:
            if not access and cmd not in first_allowed:
                continue
            _, b = g.edges[p][cmd]
            mask = g.edge_masks[p][cmd]
            if not b or mask & walls:
                continue
            if mask & ~own:
                if (p, cmd) not in distinct:
                    distinct.add((p, cmd))
                    exits.append((p, cmd, access))
                    if first_depth is None:
                        first_depth = len(access)
                continue
            key = 4 * b + cmd
            if key not in visited:
                visited.add(key)
                q.append((b, cmd, access + (cmd,)))
    return exits


def ray(board, side, route, direction, length, max_turns):
    """Yield consecutive same-direction prefixes, stopping at the first closure."""
    g = board.g
    own = board.owns[side]
    walls = board.trails[0] | board.trails[1] | board.unknown
    p, steps, drawn = route.p, route.steps, route.drawn
    for _ in range(min(length, max_turns - len(steps))):
        a, b = g.edges[p][direction]
        mask = g.edge_masks[p][direction]
        if not b or mask & (walls | drawn):
            return
        closed = False
        for cell in (a,) if a == b else (a, b):
            bit = g.bits[cell]
            if bit & own:
                if drawn:
                    closed = True
            elif closed:
                return
            else:
                drawn |= bit
        steps += (direction,)
        yield Route(b, steps, drawn, closed)
        if closed:
            return
        p = b


def loop_search(board, side, exits, times, remaining, config, budget):
    if not exits:
        return None
    g = board.g
    max_turns = min(config.max_macro, remaining)
    end_enum = min(budget.end - .012, budget.clock() + config.enum_ms / 1000)
    own, enemy = board.owns[side], board.owns[1 - side]
    lead = board.match_scores[side] - board.match_scores[1 - side]
    defend = lead > 0 and remaining < 2 * config.max_macro
    cap = config.shortlist
    shortlist, shortest, sequence = [], {}, 0
    best = None

    def consider(route, anchor):
        nonlocal sequence
        if not route.closed or not route.drawn or len(route.steps) >= shortest.get(route.drawn, INF):
            return
        # The same contour from different access points may take different times.
        # A slower traversal cannot dominate a shorter one in the final shortlist.
        count = len(route.steps)
        eta = earliest(route.drawn, times)
        slack = eta - count
        if slack < (-2 if not defend else 1):
            return
        # Fast upper estimate: weighted cells in the route's box, then exact flood fill.
        bits = route.drawn | g.bits[anchor] | g.bits[route.p]
        xs, ys = [], []
        while bits:
            bit = bits & -bits
            y, x = divmod(bit.bit_length() - 1, g.stride)
            xs.append(x)
            ys.append(y)
            bits ^= bit
        width = max(xs) - min(xs) + 1
        row = ((1 << width) - 1) << (min(ys) * g.stride + min(xs))
        box = 0
        for _ in range(max(ys) - min(ys) + 1):
            box |= row
            row <<= g.stride
        cells = box & g.value_cells & ~own
        upper = (popcount(cells) + popcount(cells & enemy)
                 + 2 * popcount(cells & g.stars) + 2 * popcount(cells & g.stars & enemy))
        safety = 1.0 if slack >= 4 else .82 if slack >= 2 else .58 if slack >= 0 else .28
        rough = safety * (upper - .45 * popcount(route.drawn)) / (count + 4) ** .84
        sequence += 1
        entry = (rough, sequence, route, safety, slack)
        if len(shortlist) < cap:
            heapq.heappush(shortlist, entry)
        elif rough > shortlist[0][0]:
            heapq.heapreplace(shortlist, entry)
        shortest[route.drawn] = count

    # Interleave small and large departures over all available sides. Each ray
    # uses turn edges, so a candidate can never turn between two boost substeps.
    departures = []
    for p, cmd, access in exits:
        options = list(ray(board, side, Route(p, access), cmd, config.max_leg, max_turns))
        for prefix in options:
            if prefix.closed:
                consider(prefix, p)
        departures.append((p, cmd, options))
    lengths = (2, 4, 1, 7, 3, 10, 5, 12, 6, 9, 8, 11) + tuple(range(13, config.max_leg + 1))
    def enumerate_routes():
        for length in lengths:
            for anchor, first, prefixes in departures:
                if length > len(prefixes) or prefixes[length - 1].closed:
                    continue
                for second in ((first + 1) % 4, (first - 1) % 4):
                    for bend in ray(board, side, prefixes[length-1], second, config.max_leg, max_turns):
                        if budget.clock() >= end_enum:
                            return
                        if bend.closed:
                            consider(bend, anchor)
                            break
                        for k, back in enumerate(ray(board, side, bend, (first + 2) % 4,
                                                     config.max_leg, max_turns), 1):
                            if back.closed:
                                consider(back, anchor)
                                break
                            if config.allow_fourth_leg and k in (1, 3, 6, 9, 12):
                                for last in ray(board, side, back, (second + 2) % 4,
                                                config.max_leg, max_turns):
                                    if last.closed:
                                        consider(last, anchor)
    enumerate_routes()
    for _, _, route, safety, slack in sorted(shortlist, reverse=True):
        if len(route.steps) > shortest[route.drawn]:
            continue
        try:
            budget.check()
            capture = board.capture(side, route.drawn, budget)
        except BudgetExpired:
            if best is None:
                raise
            break
        gain = board.gain(side, capture)
        if gain <= 0:
            continue
        # A late loss of the trail costs all unrealised points and restart tempo.
        # In the closing phase prefer projected final advantage while ahead.
        utility = safety * gain / (len(route.steps) + 4) ** .84
        if defend and slack < 4:
            utility *= safety
        if best is None or utility > best[0]:
            best = utility, route, gain
    return best

def remote_frontier(board, side, start, direction, allowed, times, remaining, budget):
    """Choose a reachable safe launch site when the current front offers no profitable loop."""
    g = board.g
    own, enemy = board.owns[side], board.owns[1-side]
    blocked = board.trails[0] | board.trails[1] | board.unknown
    front = own & g.around(g.value_cells & ~own)
    transition = g.edges
    visited = bytearray(g.size * 4)
    visited[start*4 + direction] = 1
    scored = set()
    q = deque([(start, direction, ())])
    best, best_route = 0.0, ()
    max_access = min(26, remaining - 5)
    if max_access < 3:
        return ()
    explored = 0
    while q:
        p, d, steps = q.popleft()
        if not explored % 128:
            budget.check()
        explored += 1
        distance = len(steps)
        if distance >= 3 and p not in scored and front & g.bits[p]:
            scored.add(p)
            exits = [(cmd, transition[p][cmd][1]) for cmd in NEXT[d]
                     if transition[p][cmd][1] and g.edge_masks[p][cmd] & ~own
                     and not g.edge_masks[p][cmd] & blocked]
            for cmd, cell in exits:
                safety = min(36, times[cell]) - .35 * distance
                if safety < 5:
                    continue
                size = g.stride
                y,x = divmod(cell,size)
                x0, x1 = max(1, x-4), min(g.w, x+4)
                y0, y1 = max(1, y-4), min(g.h, y+4)
                span = ((1 << (x1-x0+1)) - 1) << (y0*size + x0)
                patch = 0
                for _ in range(y0, y1+1):
                    patch |= span
                    span <<= size
                land = patch & g.value_cells & ~own
                gain = popcount(land) + popcount(land & enemy) + 2*popcount(land & g.stars)
                rank = gain * min(1.3, safety/18) / (7 + distance) ** .70
                if rank > best:
                    best, best_route = rank, steps
        if distance >= max_access:
            continue
        for cmd in NEXT[d]:
            if not steps and cmd not in allowed:
                continue
            target = transition[p][cmd][1]
            mask = g.edge_masks[p][cmd]
            key = target*4+cmd
            if target and not visited[key] and not mask & (~own | blocked):
                visited[key] = 1
                q.append((target,cmd,steps+(cmd,)))
    return best_route
