"""Team121 Exposure Territory strategy.

Drop-in strategy implementation for the official Python SDK contract described in
player-kit/README.md.  The strategy deliberately depends only on the public
InitState / TurnState surface and the documented game.copy().step() simulator.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

Coord = Tuple[int, int]
StateKey = Tuple[int, int, str]

DIRS: Dict[str, Coord] = {
    "U": (0, -1),
    "D": (0, 1),
    "L": (-1, 0),
    "R": (1, 0),
}
OPP = {"U": "D", "D": "U", "L": "R", "R": "L"}
ORDER = ("U", "R", "D", "L")
BOARD_N = 41


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coord(obj: Any) -> Optional[Coord]:
    if obj is None:
        return None
    if isinstance(obj, (tuple, list)) and len(obj) >= 2:
        return (_int(obj[0]), _int(obj[1]))
    x, y = _get(obj, "x"), _get(obj, "y")
    if x is None or y is None:
        return None
    return (_int(x), _int(y))


def _norm_dir(value: Any, fallback: str = "U") -> str:
    if isinstance(value, str):
        v = value.upper()
        if v in DIRS:
            return v
        # Common enum spellings.
        for d in DIRS:
            if v.endswith(d) or v == f"DIR_{d}":
                return d
    # The rules summary states seat 0 starts U and seat 1 starts D.  Some
    # implementations expose 0..3; support the common U,D,L,R ordering.
    if isinstance(value, int):
        return ("U", "D", "L", "R")[value % 4]
    return fallback


def _matrix_get(matrix: Any, x: int, y: int, default: Any = None) -> Any:
    try:
        row = matrix[y]
        return row[x]
    except (TypeError, IndexError, KeyError):
        return default


def _owner_value(v: Any) -> int:
    if v is None:
        return -1
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("", ".", "-", "none", "null", "empty"):
            return -1
        if s in ("0", "1"):
            return int(s)
        return _int(v, -1)
    return _int(v, -1)


def _contour_present(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip().lower() not in ("", ".", "-", "none", "null", "false")
    if isinstance(v, bool):
        return v
    return _int(v, -1) >= 0


@dataclass
class Snapshot:
    seat: int
    enemy_seat: int
    me: Any
    enemy: Any
    owner: Any
    contour: Any
    terrain: Any
    map_obj: Any
    game: Any
    width: int = BOARD_N
    height: int = BOARD_N

    @property
    def me_pos(self) -> Coord:
        return (_int(_get(self.me, "x"), 20), _int(_get(self.me, "y"), 20))

    @property
    def me_dir(self) -> str:
        return _norm_dir(_get(self.me, "dir"), "U" if self.seat == 0 else "D")

    @property
    def enemy_pos(self) -> Coord:
        return (_int(_get(self.enemy, "x"), 20), _int(_get(self.enemy, "y"), 20))

    @property
    def enemy_dir(self) -> str:
        return _norm_dir(_get(self.enemy, "dir"), "D" if self.seat == 0 else "U")

    def inside(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def owner_at(self, x: int, y: int) -> int:
        return _owner_value(_matrix_get(self.owner, x, y, -1))

    def contour_at(self, x: int, y: int) -> bool:
        return _contour_present(_matrix_get(self.contour, x, y, None))

    def terrain_at(self, x: int, y: int) -> str:
        v = _matrix_get(self.terrain, x, y, "#")
        if isinstance(v, int):
            # Defensive support for enum-like terrain values.
            return "#" if v < 0 else ("*" if v == 1 else ".")
        s = str(v)
        return s[0] if s else "#"

    def move_steps(self, x: int, y: int) -> int:
        fn = _get(self.map_obj, "move_steps")
        if callable(fn):
            try:
                return 2 if int(fn(x, y)) >= 2 else 1
            except Exception:
                pass
        # The map has exactly three boost zones.  If the official map helper
        # is unavailable, do not guess their coordinates: choose safe 1-step.
        return 1


def _make_snapshot(init: Any, state: Any, seat: int) -> Snapshot:
    me = _get(state, "me")
    enemy = _get(state, "enemy")
    beams = _get(state, "beams")
    if me is None and beams is not None:
        me = beams[seat]
    if enemy is None and beams is not None:
        enemy = beams[1 - seat]
    init_map = _get(init, "map")
    state_map = _get(state, "map", init_map)
    game = _get(state, "game")
    return Snapshot(
        seat=seat,
        enemy_seat=1 - seat,
        me=me,
        enemy=enemy,
        owner=_get(state, "owner"),
        contour=_get(state, "contour"),
        terrain=_get(state_map, "terrain", _get(init_map, "terrain")),
        map_obj=state_map,
        game=game,
    )


def _delta(a: Coord, b: Coord) -> Optional[str]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    for d, (vx, vy) in DIRS.items():
        if (dx, dy) == (vx, vy):
            return d
    return None


def _apply_action(
    snap: Snapshot,
    x: int,
    y: int,
    direction: str,
    command: str,
    allowed_owner: Optional[int] = None,
    block_contours: bool = True,
) -> Optional[StateKey]:
    """Apply one complete turn using the documented start-cell speed rule."""
    new_dir = direction if command == OPP[direction] else command
    dx, dy = DIRS[new_dir]
    steps = snap.move_steps(x, y)
    nx, ny = x, y
    for _ in range(steps):
        nx += dx
        ny += dy
        if not snap.inside(nx, ny) or snap.terrain_at(nx, ny) == "#":
            return None
        if block_contours and snap.contour_at(nx, ny):
            return None
        if allowed_owner is not None and snap.owner_at(nx, ny) != allowed_owner:
            return None
    return (nx, ny, new_dir)


def _bfs_inside_territory(snap: Snapshot, max_depth: int = 10):
    """BFS over (position, heading), staying entirely on our current territory."""
    sx, sy = snap.me_pos
    start: StateKey = (sx, sy, snap.me_dir)
    if snap.owner_at(sx, sy) != snap.seat:
        return {start: None}, {}
    parent: Dict[StateKey, Optional[Tuple[StateKey, str]]] = {start: None}
    depth: Dict[StateKey, int] = {start: 0}
    q = deque([start])
    while q:
        cur = q.popleft()
        if depth[cur] >= max_depth:
            continue
        x, y, d = cur
        for cmd in ORDER:
            nxt = _apply_action(snap, x, y, d, cmd, allowed_owner=snap.seat)
            if nxt is None or nxt in parent:
                continue
            parent[nxt] = (cur, cmd)
            depth[nxt] = depth[cur] + 1
            q.append(nxt)
    return parent, depth


def _reconstruct(parent: Dict[StateKey, Optional[Tuple[StateKey, str]]], end: StateKey) -> List[str]:
    out: List[str] = []
    cur = end
    while parent.get(cur) is not None:
        prev, cmd = parent[cur]  # type: ignore[misc]
        out.append(cmd)
        cur = prev
    out.reverse()
    return out


def _follow_path_commands(snap: Snapshot, path: Sequence[Coord], start_dir: str):
    """Translate an exact cell path into commands, respecting boosts and no-U-turn."""
    if len(path) < 2:
        return [], [], start_dir
    idx = 0
    direction = start_dir
    commands: List[str] = []
    expected: List[StateKey] = []
    while idx < len(path) - 1:
        wanted = _delta(path[idx], path[idx + 1])
        if wanted is None or wanted == OPP[direction]:
            return None
        direction = wanted
        x, y = path[idx]
        steps = snap.move_steps(x, y)
        if idx + steps >= len(path):
            return None
        for k in range(1, steps + 1):
            if _delta(path[idx + k - 1], path[idx + k]) != wanted:
                return None
        idx += steps
        nx, ny = path[idx]
        commands.append(wanted)
        expected.append((nx, ny, direction))
    return commands, expected, direction


def _path_cells_clear(snap: Snapshot, path: Sequence[Coord]) -> bool:
    for i, (x, y) in enumerate(path):
        if not snap.inside(x, y) or snap.terrain_at(x, y) == "#":
            return False
        if i not in (0, len(path) - 1) and snap.owner_at(x, y) == snap.seat:
            return False  # an earlier return would close the route prematurely
        if i > 0 and snap.contour_at(x, y):
            return False
    return True


def _rect_path_horizontal(x0: int, y0: int, x1: int, sign: int, depth: int) -> List[Coord]:
    sx = 1 if x1 > x0 else -1
    yfar = y0 + sign * depth
    path: List[Coord] = [(x0, y0)]
    for k in range(1, depth + 1):
        path.append((x0, y0 + sign * k))
    x = x0 + sx
    while True:
        path.append((x, yfar))
        if x == x1:
            break
        x += sx
    for k in range(depth - 1, -1, -1):
        path.append((x1, y0 + sign * k))
    return path


def _rect_path_vertical(x0: int, y0: int, y1: int, sign: int, depth: int) -> List[Coord]:
    sy = 1 if y1 > y0 else -1
    xfar = x0 + sign * depth
    path: List[Coord] = [(x0, y0)]
    for k in range(1, depth + 1):
        path.append((x0 + sign * k, y0))
    y = y0 + sy
    while True:
        path.append((xfar, y))
        if y == y1:
            break
        y += sy
    for k in range(depth - 1, -1, -1):
        path.append((x0 + sign * k, y1))
    return path


def _calibration_cells(map_obj: Any) -> Set[Coord]:
    """Read calibration centres when exposed by the SDK; tolerate several shapes."""
    result: Set[Coord] = set()
    calib = _get(map_obj, "calib")
    if calib is None:
        return result
    try:
        items = list(calib)
    except TypeError:
        items = [calib]
    centres: List[Coord] = []
    for item in items:
        c = _coord(item)
        if c is not None and c[0] >= 0 and c[1] >= 0:
            centres.append(c)
    for cx, cy in centres:
        for yy in range(cy - 1, cy + 2):
            for xx in range(cx - 1, cx + 2):
                result.add((xx, yy))
    return result


def _value(snap: Snapshot, cell: Coord) -> int:
    return 3 if snap.terrain_at(cell[0], cell[1]) == "*" else 1


def _score_delta_for_capture(snap: Snapshot, capture: Set[Coord], calib: Set[Coord]) -> float:
    """Estimate exact local score-difference delta, including exposure penalties."""
    if not capture:
        return 0.0
    changed = set(capture)
    for x, y in tuple(capture):
        for dx, dy in DIRS.values():
            changed.add((x + dx, y + dy))
    changed = {p for p in changed if snap.inside(*p)}

    def projected_owner(x: int, y: int) -> int:
        p = (x, y)
        if p in capture and p not in calib and snap.terrain_at(x, y) != "#":
            return snap.seat
        return snap.owner_at(x, y)

    def exposure(side: int, after: bool) -> int:
        total = 0
        for x, y in changed:
            if (x, y) in calib:
                continue
            own = projected_owner(x, y) if after else snap.owner_at(x, y)
            if own != side:
                continue
            for dx, dy in DIRS.values():
                nx, ny = x + dx, y + dy
                if not snap.inside(nx, ny):
                    total += 1
                elif snap.terrain_at(nx, ny) == "#":
                    total += 1
                else:
                    other = projected_owner(nx, ny) if after else snap.owner_at(nx, ny)
                    if other != side:
                        total += 1
        return total

    old_my_exp = exposure(snap.seat, False)
    new_my_exp = exposure(snap.seat, True)
    old_en_exp = exposure(snap.enemy_seat, False)
    new_en_exp = exposure(snap.enemy_seat, True)

    my_value_gain = 0
    enemy_value_lost = 0
    for x, y in capture:
        if not snap.inside(x, y) or (x, y) in calib or snap.terrain_at(x, y) == "#":
            continue
        old = snap.owner_at(x, y)
        v = _value(snap, (x, y))
        if old != snap.seat:
            my_value_gain += v
        if old == snap.enemy_seat:
            enemy_value_lost += v

    delta_my = my_value_gain - (new_my_exp // 3 - old_my_exp // 3)
    delta_enemy = -enemy_value_lost - (new_en_exp // 3 - old_en_exp // 3)
    return float(delta_my - delta_enemy)


def _enemy_distance_map(snap: Snapshot) -> List[List[int]]:
    inf = 10 ** 6
    dist = [[inf] * snap.width for _ in range(snap.height)]
    ex, ey = snap.enemy_pos
    alive = bool(_get(snap.enemy, "alive", True))
    if not alive or not snap.inside(ex, ey):
        return dist
    q = deque([(ex, ey)])
    dist[ey][ex] = 0
    while q:
        x, y = q.popleft()
        for dx, dy in DIRS.values():
            nx, ny = x + dx, y + dy
            if not snap.inside(nx, ny) or dist[ny][nx] != inf:
                continue
            if snap.terrain_at(nx, ny) == "#" or snap.contour_at(nx, ny):
                continue
            dist[ny][nx] = dist[y][x] + 1
            q.append((nx, ny))
    return dist



def _contour_owner_value(v: Any) -> int:
    """Return contour owner, -1 for empty, -2 when present but unlabelled."""
    if v is None:
        return -1
    if isinstance(v, bool):
        return -2 if v else -1
    if isinstance(v, str):
        t = v.strip().lower()
        if t in ("", ".", "-", "none", "null", "false"):
            return -1
        if t in ("0", "1"):
            return int(t)
        return _int(v, -2)
    return _int(v, -2)


def _calibration_centres(map_obj: Any) -> List[Coord]:
    calib = _get(map_obj, "calib")
    if calib is None:
        return []
    try:
        items = list(calib)
    except TypeError:
        items = [calib]
    out: List[Coord] = []
    for item in items:
        c = _coord(item)
        if c is not None and c[0] >= 0 and c[1] >= 0:
            out.append(c)
    return out


def _enemy_turn_distance_map(snap: Snapshot) -> List[List[int]]:
    """Exact turn-count reachability under start-cell boost semantics.

    Unlike Manhattan/BFS-distance divided by two, this only grants a second
    substep when the enemy's *current* cell is in a boost zone. Heading is part
    of the state so the first no-U-turn constraint is respected.
    """
    inf = 10 ** 6
    best = [[inf] * snap.width for _ in range(snap.height)]
    enemy = snap.enemy
    if enemy is None:
        return best
    alive = bool(_get(enemy, "alive", True))
    restart = max(0, _int(_get(enemy, "restart_in", 0), 0)) if not alive else 0
    ex, ey = snap.enemy_pos
    heading = snap.enemy_dir
    if not alive:
        centres = _calibration_centres(snap.map_obj)
        if len(centres) > snap.enemy_seat:
            ex, ey = centres[snap.enemy_seat]
        else:
            # The SDK normally retains the spawn coordinate while cooling down.
            ex, ey = snap.enemy_pos
        heading = "U" if snap.enemy_seat == 0 else "D"
    if not snap.inside(ex, ey) or snap.terrain_at(ex, ey) == "#":
        return best

    q = deque([(ex, ey, heading)])
    dist: Dict[StateKey, int] = {(ex, ey, heading): 0}
    best[ey][ex] = restart
    while q:
        x, y, old_dir = q.popleft()
        cur = dist[(x, y, old_dir)]
        nd = cur + 1
        steps = snap.move_steps(x, y)
        for cmd in ORDER:
            if cmd == OPP[old_dir]:
                continue
            dx, dy = DIRS[cmd]
            nx, ny = x, y
            valid = True
            hit_our_contour = False
            for _ in range(steps):
                nx += dx
                ny += dy
                if not snap.inside(nx, ny) or snap.terrain_at(nx, ny) == "#":
                    valid = False
                    break
                co = _contour_owner_value(_matrix_get(snap.contour, nx, ny, None))
                if co == snap.enemy_seat:
                    valid = False  # enemy walks into its own trail
                    break
                best[ny][nx] = min(best[ny][nx], restart + nd)
                if co == snap.seat or (co == -2 and snap.contour_at(nx, ny)):
                    hit_our_contour = True
                    break
            if not valid or hit_our_contour:
                continue
            state = (nx, ny, cmd)
            if nd < dist.get(state, inf):
                dist[state] = nd
                q.append(state)
    return best


def _my_contour_cells(snap: Snapshot) -> Set[Coord]:
    out: Set[Coord] = set()
    for y in range(snap.height):
        for x in range(snap.width):
            v = _contour_owner_value(_matrix_get(snap.contour, x, y, None))
            if v == snap.seat or (v == -2 and snap.contour_at(x, y)):
                out.add((x, y))
    return out


def _compute_capture_set(snap: Snapshot, new_trail: Iterable[Coord], calib: Optional[Set[Coord]] = None) -> Set[Coord]:
    """Reproduce the referee's border flood-fill capture, including old trails."""
    if calib is None:
        calib = _calibration_cells(snap.map_obj)
    old_trail = _my_contour_cells(snap)
    new_trail_set = set(new_trail)
    walls = set(old_trail) | new_trail_set
    for y in range(snap.height):
        for x in range(snap.width):
            if snap.owner_at(x, y) == snap.seat:
                walls.add((x, y))
    seen: Set[Coord] = set()
    q = deque()
    def add(x: int, y: int) -> None:
        p = (x, y)
        if p in seen or p in walls or not snap.inside(x, y) or snap.terrain_at(x, y) == "#":
            return
        seen.add(p)
        q.append(p)
    for x in range(snap.width):
        add(x, 0)
        add(x, snap.height - 1)
    for y in range(snap.height):
        add(0, y)
        add(snap.width - 1, y)
    while q:
        x, y = q.popleft()
        for dx, dy in DIRS.values():
            add(x + dx, y + dy)

    capture: Set[Coord] = set()
    for y in range(snap.height):
        for x in range(snap.width):
            p = (x, y)
            if p in calib or snap.terrain_at(x, y) == "#" or snap.owner_at(x, y) == snap.seat:
                continue
            co = _contour_owner_value(_matrix_get(snap.contour, x, y, None))
            if p in new_trail_set or p in old_trail or p not in seen:
                capture.add(p)
            elif co == snap.seat:
                capture.add(p)
    return capture


def _valid_closed_route(snap: Snapshot, path: Sequence[Coord]) -> bool:
    """A route may travel inside home before exiting, but may only re-enter at end."""
    if len(path) < 4:
        return False
    if len(set(path)) != len(path):
        return False
    outside = False
    for i, (x, y) in enumerate(path[1:], 1):
        if not snap.inside(x, y) or snap.terrain_at(x, y) == "#" or snap.contour_at(x, y):
            return False
        owner = snap.owner_at(x, y)
        if owner != snap.seat:
            outside = True
        elif outside and i != len(path) - 1:
            return False
        elif not outside and i == len(path) - 1:
            return False
    return outside and snap.owner_at(*path[-1]) == snap.seat


def _bbox_prefix(snap: Snapshot, calib: Set[Coord]):
    pref = [[0] * (snap.width + 1) for _ in range(snap.height + 1)]
    for y in range(snap.height):
        run = 0
        for x in range(snap.width):
            if (x, y) not in calib and snap.terrain_at(x, y) != "#":
                o = snap.owner_at(x, y)
                if o != snap.seat:
                    # Capturing an opponent's cell benefits us and removes their score.
                    run += _value(snap, (x, y)) * (2 if o >= 0 else 1)
            pref[y + 1][x + 1] = pref[y][x + 1] + run
    return pref


def _bbox_sum(pref, x0: int, y0: int, x1: int, y1: int) -> int:
    return pref[y1 + 1][x1 + 1] - pref[y0][x1 + 1] - pref[y1 + 1][x0] + pref[y0][x0]


def _build_loop_candidates(snap: Snapshot, parent, depth_map, max_leg: int = 12, max_turns: int = 32):
    """Multi-shape closed-loop search inspired by top-performing territory bots.

    It does not copy the other arena's engine assumptions: each candidate is
    compiled through this game's boost-aware movement model and then scored by
    this game's weighted cells and exposure penalty.
    """
    import heapq

    sx, sy = snap.me_pos
    calib = _calibration_cells(snap.map_obj)
    enemy_dist = _enemy_turn_distance_map(snap)
    pref = _bbox_prefix(snap, calib)
    starts = [(k, d) for k, d in depth_map.items()
              if d <= 6 and snap.owner_at(k[0], k[1]) == snap.seat]
    starts.sort(key=lambda item: (item[1], abs(item[0][0] - sx) + abs(item[0][1] - sy)))
    starts = starts[:16]
    if not starts:
        return None

    shortlist = []
    serial = 0
    deadline = time.perf_counter() + 0.030  # keep a wide margin under the 200 ms referee limit
    max_leg = min(max_leg, max(3, snap.width // 2 - 1))

    def consider(anchor: StateKey, access_turns: int, route: List[Coord]):
        nonlocal serial
        if not _valid_closed_route(snap, route):
            return
        start_dir = anchor[2]
        follow = _follow_path_commands(snap, route, start_dir)
        if follow is None:
            return
        perimeter_cmds, _, _ = follow
        access_cmds = _reconstruct(parent, anchor)
        commands = access_cmds + perimeter_cmds
        turns = len(commands)
        if not commands or turns > max_turns or turns > 600:
            return
        exposed = [p for p in route[1:] if snap.owner_at(*p) != snap.seat]
        if not exposed:
            return
        eta = min(enemy_dist[y][x] for x, y in exposed)
        margin = eta - turns if eta < 10**5 else 99
        # Reject routes that are already too slow to beat the opponent's reach.
        if margin < -2:
            return
        xs = [p[0] for p in route]
        ys = [p[1] for p in route]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        rough_gain = _bbox_sum(pref, x0, y0, x1, y1)
        rough_gain -= 0.45 * (len(route) - 1)
        risk_factor = 1.0 if margin >= 4 else 0.82 if margin >= 2 else 0.58 if margin >= 0 else 0.28
        rough = rough_gain * risk_factor / pow(turns + 4.0, 0.84)
        if rough <= 0.12:
            return
        serial += 1
        item = (rough, serial, commands, tuple(exposed), turns, eta)
        if len(shortlist) < 32:
            heapq.heappush(shortlist, item)
        elif rough > shortlist[0][0]:
            heapq.heapreplace(shortlist, item)

    for anchor, access_turns in starts:
        if time.perf_counter() >= deadline:
            break
        x0, y0, heading0 = anchor
        access_cmds = access_turns
        for d1 in ORDER:
            if d1 == OPP[heading0]:
                continue
            dx1, dy1 = DIRS[d1]
            route1 = [(x0, y0)]
            seen = {(x0, y0)}
            x, y = x0, y0
            has_exited = False
            for a in range(1, max_leg + 1):
                x += dx1; y += dy1
                p = (x, y)
                if not snap.inside(x, y) or snap.terrain_at(x, y) == "#" or snap.contour_at(x, y) or p in seen:
                    break
                route1.append(p); seen.add(p)
                if snap.owner_at(x, y) != snap.seat:
                    has_exited = True
                elif has_exited:
                    break
                if not has_exited:
                    continue

                for d2 in ORDER:
                    if d2 in (d1, OPP[d1]):
                        continue
                    dx2, dy2 = DIRS[d2]
                    route2 = list(route1)
                    seen2 = set(seen)
                    xx, yy = x, y
                    closed_side = False
                    for b in range(1, max_leg + 1):
                        xx += dx2; yy += dy2
                        q = (xx, yy)
                        if not snap.inside(xx, yy) or snap.terrain_at(xx, yy) == "#" or snap.contour_at(xx, yy) or q in seen2:
                            break
                        route2.append(q); seen2.add(q)
                        if snap.owner_at(xx, yy) == snap.seat:
                            consider(anchor, access_turns, route2)
                            closed_side = True
                            break
                        # Third leg walks back toward the existing territory.
                        rx, ry = xx, yy
                        route3 = list(route2)
                        seen3 = set(seen2)
                        dx3, dy3 = DIRS[OPP[d1]]
                        for c in range(1, max_leg + 1):
                            rx += dx3; ry += dy3
                            r = (rx, ry)
                            if not snap.inside(rx, ry) or snap.terrain_at(rx, ry) == "#" or snap.contour_at(rx, ry) or r in seen3:
                                break
                            route3.append(r); seen3.add(r)
                            if snap.owner_at(rx, ry) == snap.seat:
                                consider(anchor, access_turns, route3)
                                closed_side = True
                                break
                        # Four-side fallback: return parallel to the second leg.
                        if not closed_side:
                            dx4, dy4 = DIRS[OPP[d2]]
                            fx, fy = rx, ry
                            route4 = list(route3)
                            seen4 = set(seen3)
                            for _ in range(max_leg):
                                fx += dx4; fy += dy4
                                z = (fx, fy)
                                if not snap.inside(fx, fy) or snap.terrain_at(fx, fy) == "#" or snap.contour_at(fx, fy) or z in seen4:
                                    break
                                route4.append(z); seen4.add(z)
                                if snap.owner_at(fx, fy) == snap.seat:
                                    consider(anchor, access_turns, route4)
                                    break
                    if time.perf_counter() >= deadline:
                        break
                if time.perf_counter() >= deadline:
                    break
            if time.perf_counter() >= deadline:
                break

    if not shortlist:
        return None

    best = None
    for rough, _, commands, exposed, turns, eta in sorted(shortlist, reverse=True):
        if time.perf_counter() >= deadline + 0.018:
            break
        capture = _compute_capture_set(snap, exposed, calib)
        gain = _score_delta_for_capture(snap, capture, calib)
        margin = eta - turns if eta < 10**5 else 99
        risk_factor = 1.0 if margin >= 4 else 0.82 if margin >= 2 else 0.58 if margin >= 0 else 0.28
        utility = gain * risk_factor / pow(turns + 4.0, 0.84)
        if gain < 3.0 or utility <= 0.18:
            continue
        expected = _expected_for_commands(snap, commands, (0, 0, "U"), {})
        if expected is None or len(expected) != len(commands):
            continue
        if best is None or utility > best[0]:
            best = (utility, commands, expected, gain, turns, eta)
    return best


def _find_home_route(snap: Snapshot) -> List[str]:
    """Shortest legal macro route to our territory, with boost-aware edges."""
    sx, sy = snap.me_pos
    start: StateKey = (sx, sy, snap.me_dir)
    if snap.owner_at(sx, sy) == snap.seat:
        return []
    q = deque([start])
    parent: Dict[StateKey, Optional[Tuple[StateKey, str]]] = {start: None}
    while q:
        cur = q.popleft()
        x, y, d = cur
        for cmd in ORDER:
            nxt = _apply_action(snap, x, y, d, cmd, block_contours=True)
            if nxt is None or nxt in parent:
                continue
            parent[nxt] = (cur, cmd)
            if snap.owner_at(nxt[0], nxt[1]) == snap.seat:
                return _reconstruct(parent, nxt)
            q.append(nxt)
    return []


def _enemy_return_turns(snap: Snapshot) -> int:
    """Estimate how many turns an exposed enemy needs to close its own trail."""
    enemy = snap.enemy
    if enemy is None or not bool(_get(enemy, "alive", True)):
        return 10**6
    sx, sy = snap.enemy_pos
    heading = snap.enemy_dir
    if snap.owner_at(sx, sy) == snap.enemy_seat:
        return 0
    start: StateKey = (sx, sy, heading)
    q = deque([start])
    dist = {start: 0}
    while q:
        x, y, d = q.popleft()
        nd = dist[(x, y, d)] + 1
        for cmd in ORDER:
            if cmd == OPP[d]:
                continue
            dx, dy = DIRS[cmd]
            nx, ny = x, y
            valid = True
            for _ in range(snap.move_steps(x, y)):
                nx += dx; ny += dy
                if not snap.inside(nx, ny) or snap.terrain_at(nx, ny) == "#":
                    valid = False; break
                co = _contour_owner_value(_matrix_get(snap.contour, nx, ny, None))
                if co == snap.enemy_seat:
                    valid = False; break
            if not valid:
                continue
            if snap.owner_at(nx, ny) == snap.enemy_seat:
                return nd
            nxt = (nx, ny, cmd)
            if nd < dist.get(nxt, 10**6):
                dist[nxt] = nd
                q.append(nxt)
    return 10**6


def _find_enemy_cut_route(snap: Snapshot, max_turns: int = 8) -> List[str]:
    """Find a short route that steps onto the opponent's live contour."""
    enemy = snap.enemy
    if enemy is None or not bool(_get(enemy, "alive", True)):
        return []
    sx, sy = snap.me_pos
    start: StateKey = (sx, sy, snap.me_dir)
    q = deque([start])
    parent: Dict[StateKey, Optional[Tuple[StateKey, str]]] = {start: None}
    depth = {start: 0}
    while q:
        cur = q.popleft()
        x, y, d = cur
        if depth[cur] >= max_turns:
            continue
        for cmd in ORDER:
            if cmd == OPP[d]:
                continue
            dx, dy = DIRS[cmd]
            nx, ny = x, y
            valid = True
            hit = False
            for _ in range(snap.move_steps(x, y)):
                nx += dx; ny += dy
                if not snap.inside(nx, ny) or snap.terrain_at(nx, ny) == "#":
                    valid = False; break
                co = _contour_owner_value(_matrix_get(snap.contour, nx, ny, None))
                if co == snap.seat or (co == -2 and snap.contour_at(nx, ny)):
                    valid = False; break
                if co == snap.enemy_seat:
                    hit = True
                    break
            if not valid:
                continue
            nxt = (nx, ny, cmd)
            if hit:
                parent[nxt] = (cur, cmd)
                return _reconstruct(parent, nxt)
            if nxt not in depth:
                parent[nxt] = (cur, cmd)
                depth[nxt] = depth[cur] + 1
                q.append(nxt)
    return []

def _build_rectangle_candidates(snap: Snapshot, parent, depth_map, max_width: int = 14, max_depth: int = 10):
    """Enumerate safe territory-anchored rectangles and rank their net score gain.

    Candidate enumeration is intentionally bounded: the referee allows only
    200 ms per reply, so exact exposure scoring is performed only for the best
    geometry-level candidates.
    """
    import heapq

    sx, sy = snap.me_pos
    calib = _calibration_cells(snap.map_obj)
    enemy_dist = _enemy_distance_map(snap)
    starts: List[Tuple[StateKey, int]] = []
    for key, d in depth_map.items():
        x, y, direction = key
        if d <= 7 and abs(x - sx) + abs(y - sy) <= 12:
            starts.append((key, d))
    starts.sort(key=lambda item: (item[1], abs(item[0][0] - sx) + abs(item[0][1] - sy)))
    # Too many equivalent starting headings only waste the turn budget.
    starts = starts[:36]
    if not starts:
        return None

    shortlist = []
    serial = 0

    def consider(path: List[Coord], first_dir: str, access_key: StateKey,
                 access_turns: int, area_cells: Iterable[Coord]):
        nonlocal serial
        if not _path_cells_clear(snap, path):
            return
        if len(path) < 7:
            return
        follow = _follow_path_commands(snap, path, first_dir)
        if follow is None:
            return
        perimeter_cmds, _, _ = follow
        access_cmds = _reconstruct(parent, access_key)
        total_cmds = access_cmds + perimeter_cmds
        turns = len(total_cmds)
        if not total_cmds or turns > 34:
            return

        cap = set(path[1:])
        cap.update(area_cells)
        cap = {p for p in cap if p not in calib and snap.terrain_at(p[0], p[1]) != "#"}
        if not cap:
            return
        quick_gain = 0
        for px, py in cap:
            owner = snap.owner_at(px, py)
            if owner != snap.seat:
                v = _value(snap, (px, py))
                quick_gain += v * (2 if owner == snap.enemy_seat else 1)
        if quick_gain < 9:
            return

        min_enemy_eta = 10 ** 6
        if bool(_get(snap.enemy, "alive", True)):
            restart = max(0, _int(_get(snap.enemy, "restart_in", 0), 0))
            for px, py in path[1:]:
                d = enemy_dist[py][px]
                eta = restart + int(math.ceil(d / 2.0))
                if eta < min_enemy_eta:
                    min_enemy_eta = eta
        risk = max(0, turns + 1 - min_enemy_eta) * 3.5 if min_enemy_eta < 10 ** 5 else 0.0
        # Approximate the exposure cost before expensive local scoring.
        perimeter_cost = 0.55 * (len(path) - 2)
        rough_gain = quick_gain - perimeter_cost
        rough_utility = rough_gain / pow(turns + 3.0, 0.82) - risk
        if rough_utility <= 1.0:
            return
        serial += 1
        item = (rough_utility, serial, total_cmds, cap, quick_gain, turns, min_enemy_eta)
        if len(shortlist) < 32:
            heapq.heappush(shortlist, item)
        elif rough_utility > shortlist[0][0]:
            heapq.heapreplace(shortlist, item)

    for (x0, y0, first_dir), access_turns in starts:
        if first_dir in ("U", "D"):
            sign = -1 if first_dir == "U" else 1
            for sx_dir in (-1, 1):
                for width in range(2, max_width + 1):
                    x1 = x0 + sx_dir * width
                    if not snap.inside(x1, y0):
                        break
                    if any(snap.owner_at(x0 + sx_dir * k, y0) != snap.seat for k in range(width + 1)):
                        break
                    for h in range(2, max_depth + 1):
                        yfar = y0 + sign * h
                        if not snap.inside(x0, yfar) or not snap.inside(x1, yfar):
                            break
                        path = _rect_path_horizontal(x0, y0, x1, sign, h)
                        last_dir = _delta(path[-2], path[-1])
                        if last_dir is None:
                            continue
                        if snap.move_steps(*path[-2]) == 2:
                            dx, dy = DIRS[last_dir]
                            extra = (path[-1][0] + dx, path[-1][1] + dy)
                            if not snap.inside(*extra) or snap.owner_at(*extra) != snap.seat or snap.contour_at(*extra):
                                continue
                            path = path + [extra]
                        area = []
                        for xx in range(min(x0, x1), max(x0, x1) + 1):
                            for k in range(1, h + 1):
                                area.append((xx, y0 + sign * k))
                        consider(path, first_dir, (x0, y0, first_dir), access_turns, area)
        else:
            sign = -1 if first_dir == "L" else 1
            for sy_dir in (-1, 1):
                for height in range(2, max_width + 1):
                    y1 = y0 + sy_dir * height
                    if not snap.inside(x0, y1):
                        break
                    if any(snap.owner_at(x0, y0 + sy_dir * k) != snap.seat for k in range(height + 1)):
                        break
                    for w in range(2, max_depth + 1):
                        xfar = x0 + sign * w
                        if not snap.inside(xfar, y0) or not snap.inside(xfar, y1):
                            break
                        path = _rect_path_vertical(x0, y0, y1, sign, w)
                        last_dir = _delta(path[-2], path[-1])
                        if last_dir is None:
                            continue
                        if snap.move_steps(*path[-2]) == 2:
                            dx, dy = DIRS[last_dir]
                            extra = (path[-1][0] + dx, path[-1][1] + dy)
                            if not snap.inside(*extra) or snap.owner_at(*extra) != snap.seat or snap.contour_at(*extra):
                                continue
                            path = path + [extra]
                        area = []
                        for yy in range(min(y0, y1), max(y0, y1) + 1):
                            for k in range(1, w + 1):
                                area.append((x0 + sign * k, yy))
                        consider(path, first_dir, (x0, y0, first_dir), access_turns, area)

    if not shortlist:
        return None
    best = None
    # The heap is already a very small, high-value candidate set.
    for rough_utility, _, commands, cap, quick_gain, turns, enemy_eta in sorted(shortlist, reverse=True):
        gain = _score_delta_for_capture(snap, cap, calib)
        utility = gain / pow(turns + 3.0, 0.82)
        if enemy_eta < 10 ** 5:
            utility -= max(0, turns + 1 - enemy_eta) * 3.5
        if gain < 5.0 or utility <= 1.35:
            continue
        expected = _expected_for_commands(snap, commands, (0, 0, "U"), {})
        # _expected_for_commands only needs the ordered action list.  It does
        # not depend on the target argument or BFS tree after receiving it.
        if expected is None or len(expected) != len(commands):
            continue
        if best is None or utility > best[0]:
            best = (utility, commands, expected, gain, turns, enemy_eta)
    return best


def _expected_for_commands(snap: Snapshot, commands: Sequence[str], target: StateKey, parent) -> Optional[List[StateKey]]:
    """Create predicted beam states from BFS access path and loop path."""
    # Reconstruct the access states by replaying the access commands from the
    # current beam.  This also protects against SDK differences in map speed.
    x, y = snap.me_pos
    d = snap.me_dir
    expected: List[StateKey] = []
    for cmd in commands:
        nxt = _apply_action(snap, x, y, d, cmd, block_contours=True)
        if nxt is None:
            return None
        x, y, d = nxt
        expected.append(nxt)
    if expected and (expected[-1][0], expected[-1][1], expected[-1][2]) == target:
        return expected
    # Target is the access waypoint, but the macro continues around a loop.
    # We only need the sequence itself; replay above is authoritative for the
    # expected positions, so the endpoint need not equal the access waypoint.
    return expected


def _find_home_command(snap: Snapshot) -> Optional[str]:
    """Find a shortest orientation-aware route back to our territory."""
    sx, sy = snap.me_pos
    start: StateKey = (sx, sy, snap.me_dir)
    q = deque([start])
    parent: Dict[StateKey, Optional[Tuple[StateKey, str]]] = {start: None}
    first: Dict[StateKey, str] = {}
    while q:
        cur = q.popleft()
        x, y, d = cur
        for cmd in ORDER:
            nxt = _apply_action(snap, x, y, d, cmd, block_contours=True)
            if nxt is None or nxt in parent:
                continue
            nx, ny, nd = nxt
            parent[nxt] = (cur, cmd)
            first[nxt] = first[cur] if cur in first else cmd
            if snap.owner_at(nx, ny) == snap.seat:
                return first[nxt]
            q.append(nxt)
    return None


def _one_ply_robustness(snap: Snapshot, command: str) -> Tuple[int, float]:
    """Evaluate the action against five plausible enemy commands using engine copy."""
    game = snap.game
    copier = _get(game, "copy")
    if not callable(copier):
        return 5, 0.0
    enemy_actions: Sequence[Optional[str]] = (None, "U", "R", "D", "L")
    alive_count = 0
    worst = float("inf")
    checked = 0
    for enemy_cmd in enemy_actions:
        try:
            sim = copier()
            actions: List[Optional[str]] = [None, None]
            actions[snap.seat] = command
            actions[snap.enemy_seat] = enemy_cmd
            sim.step(actions)
            beams = _get(sim, "beams")
            my_beam = beams[snap.seat]
            if bool(_get(my_beam, "alive", True)):
                alive_count += 1
            score_fn = _get(sim, "score")
            if callable(score_fn):
                value = float(score_fn(snap.seat)) - float(score_fn(snap.enemy_seat))
            else:
                value = 0.0
            worst = min(worst, value)
            checked += 1
        except Exception:
            # If a simulator variant cannot be introspected, do not treat it as
            # proof of safety or failure; fall back to geometry-only planning.
            continue
    if checked == 0:
        return 5, 0.0
    return alive_count, worst if worst != float("inf") else 0.0


class Strategy:
    """SDK strategy entry point: __init__(InitState), act(TurnState), end(msg)."""

    def __init__(self, init: Any):
        self.init = init
        self.seat = _int(_get(init, "seat", 0), 0)
        self.seat = 0 if self.seat not in (0, 1) else self.seat
        self._commands: List[str] = []
        self._expected: List[StateKey] = []
        self._index = 0
        self._pending: Optional[StateKey] = None
        self._last_metrics: Dict[str, Any] = {}

    def _clear_plan(self) -> None:
        self._commands = []
        self._expected = []
        self._index = 0
        self._pending = None

    def _plan_still_aligned(self, snap: Snapshot) -> bool:
        if self._pending is None:
            return True
        x, y, d = snap.me_pos[0], snap.me_pos[1], snap.me_dir
        ok = (x, y, d) == self._pending
        self._pending = None
        return ok

    def _emit_plan_action(self, snap: Snapshot) -> Optional[str]:
        if self._index >= len(self._commands):
            self._clear_plan()
            return None
        cmd = self._commands[self._index]
        alive, _ = _one_ply_robustness(snap, cmd)
        # Abort before taking a step when the official simulator says that the
        # action is immediately fatal against at least one ordinary response.
        # This is conservative; the geometric planner will find another route.
        if alive < 4:
            self._clear_plan()
            return None
        self._pending = self._expected[self._index]
        self._index += 1
        return cmd

    def act(self, state: Any) -> str:
        started = time.perf_counter()
        try:
            snap = _make_snapshot(self.init, state, self.seat)
            me = snap.me
            if me is None:
                return "U" if self.seat == 0 else "D"
            if not self._plan_still_aligned(snap):
                self._clear_plan()

            alive = bool(_get(me, "alive", True))
            restart_in = _int(_get(me, "restart_in", 0), 0)
            if not alive or restart_in > 0:
                self._clear_plan()
                return snap.me_dir

            x, y = snap.me_pos
            # Recompute the opponent's true turn reach each turn. A long macro
            # plan is abandoned when the opponent can reach our exposed line
            # before the shortest safe return route closes it.
            if snap.owner_at(x, y) != snap.seat:
                home_route = _find_home_route(snap)
                my_trail = _my_contour_cells(snap)
                enemy_eta = _enemy_turn_distance_map(snap)
                nearest_cut = min((enemy_eta[yy][xx] for xx, yy in my_trail), default=10**6)
                if home_route and nearest_cut <= len(home_route) + 1:
                    self._clear_plan()
                    return home_route[0]

            if self._commands:
                cmd = self._emit_plan_action(snap)
                if cmd is not None:
                    return cmd

            if snap.owner_at(x, y) == snap.seat:
                # Intercept an exposed opponent trail when we can reach it
                # before they can close, and do not sacrifice our own open line.
                if not _my_contour_cells(snap):
                    cut_route = _find_enemy_cut_route(snap, max_turns=6)
                    enemy_home = _enemy_return_turns(snap)
                    if cut_route and len(cut_route) <= enemy_home:
                        # Prefer only quick interceptions; the next turn re-evaluates.
                        if len(cut_route) <= 4:
                            safe_count, _ = _one_ply_robustness(snap, cut_route[0])
                            if safe_count >= 4:
                                return cut_route[0]
                parent, depths = _bfs_inside_territory(snap, max_depth=8)
                best = _build_loop_candidates(snap, parent, depths)
                if best is not None:
                    _, commands, expected, gain, turns, enemy_eta = best
                    if commands and len(commands) == len(expected):
                        self._commands = list(commands)
                        self._expected = list(expected)
                        self._index = 0
                        self._last_metrics = {
                            "estimated_gain": gain,
                            "planned_turns": turns,
                            "enemy_eta": enemy_eta,
                        }
                        cmd = self._emit_plan_action(snap)
                        if cmd is not None:
                            return cmd

            # Outside territory: return through the safest reachable route.
            if snap.owner_at(x, y) != snap.seat:
                home_route = _find_home_route(snap)
                if home_route:
                    return home_route[0]

            # Safe fallback: use the engine simulator to choose the action with
            # the best worst-case immediate result, with a strong preference
            # for remaining in friendly territory.
            best_cmd = None
            best_value = -float("inf")
            for cmd in ORDER:
                alive_count, worst_score = _one_ply_robustness(snap, cmd)
                nxt = _apply_action(snap, x, y, snap.me_dir, cmd, block_contours=True)
                if nxt is None:
                    continue
                nx, ny, _ = nxt
                territory_bonus = 4.0 if snap.owner_at(nx, ny) == snap.seat else -3.0
                value = alive_count * 8.0 + worst_score * 0.01 + territory_bonus
                if value > best_value:
                    best_value, best_cmd = value, cmd
            if best_cmd is not None:
                return best_cmd

            return snap.me_dir
        except Exception:
            # The rules count malformed replies as errors, so the last-resort
            # path must always emit a legal direction.
            return "U" if self.seat == 0 else "D"
        finally:
            self._last_metrics["last_act_ms"] = (time.perf_counter() - started) * 1000.0

    def end(self, msg: dict) -> None:
        self._clear_plan()
        self._last_metrics["end"] = msg


__all__ = ["Strategy"]
