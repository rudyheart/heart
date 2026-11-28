"""Public state adapter for the recovered v2 contract and per-match geometry."""
import time

DIRS = "URDL"
INDEX = {c: i for i, c in enumerate(DIRS)}
popcount = int.bit_count if hasattr(int, "bit_count") else lambda mask: bin(mask).count("1")
INF = 10**6
EMPTY, UNKNOWN = -1, -2
NEXT = tuple((d, (d + 1) % 4, (d + 3) % 4) for d in range(4))


def get(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def heading(beam, seat):
    raw = get(beam, "dir", "U" if seat == 0 else "D")
    if isinstance(raw, int) and 0 <= raw < 4:
        return INDEX["UDLR"[raw]]  # encoding inherited from v2, verify with player-kit
    text = str(getattr(raw, "name", raw)).upper()
    text = text.removeprefix("DIR_")
    names = {"UP": "U", "RIGHT": "R", "DOWN": "D", "LEFT": "L"}
    return INDEX.get(names.get(text, text), 0 if seat == 0 else 2)


def label(raw, contour=False):
    if isinstance(raw, bool) and contour:
        return UNKNOWN if raw else EMPTY
    if raw in (0, "0"):
        return 0
    if raw in (1, "1"):
        return 1
    if raw in (None, EMPTY, "-1", "", ".", "-", False):
        return EMPTY
    if isinstance(raw, str) and raw.strip().lower() in ("none", "null", "false"):
        return EMPTY
    return UNKNOWN if contour else EMPTY


def terrain_cell(raw):
    if isinstance(raw, int):
        return "#" if raw < 0 else "*" if raw == 1 else "."
    return str(raw)[:1] or "#"


class BudgetExpired(Exception):
    pass


class Budget:
    def __init__(self, ms, clock=time.perf_counter):
        self.clock = clock
        self.start = clock()
        self.end = self.start + ms / 1000

    def check(self):
        if self.clock() >= self.end:
            raise BudgetExpired

    def left_ms(self):
        return max(0.0, (self.end - self.clock()) * 1000)


def map_signature(game_map, budget):
    rows = get(game_map, "terrain")
    if not rows or not rows[0]:
        raise ValueError("missing terrain")
    w = len(rows[0])
    terrain, speeds = [], []
    speed = get(game_map, "move_steps")
    for y, row in enumerate(rows):
        if not y % 4:
            budget.check()
        if len(row) != w:
            raise ValueError("ragged terrain")
        line = row if isinstance(row, str) else "".join(terrain_cell(v) for v in row)
        terrain.append(line)
        speeds.append(bytes(2 if speed and c != "#" and speed(x, y) >= 2 else 1
                            for x, c in enumerate(line)))
    centres = []
    for item in get(game_map, "calib", ()) or ():
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            x, y = item[:2]
        else:
            x, y = get(item, "x"), get(item, "y")
        if isinstance(x, int) and isinstance(y, int):
            centres.append((x, y))
    return tuple(terrain), tuple(speeds), tuple(centres)


class Geometry:
    def __init__(self, signature, budget):
        terrain, speeds, centres = signature
        self.signature = signature
        self.h, self.w = len(terrain), len(terrain[0])
        self.stride = self.w + 2
        self.size = (self.h + 2) * self.stride
        self.bits = [1 << p for p in range(self.size)]
        self.deltas = (-self.stride, 1, self.stride, -1)
        self.grid = self.floor = self.stars = self.border = self.calib = 0
        self.positions = []
        self.spawns = [self.index(x, y) for x, y in centres]
        for y, row in enumerate(terrain):
            budget.check()
            for x, c in enumerate(row):
                p = self.index(x, y)
                bit = self.bits[p]
                self.grid |= bit
                if x in (0, self.w - 1) or y in (0, self.h - 1):
                    self.border |= bit
                if c != "#":
                    self.positions.append(p)
                    self.floor |= bit
                    if c == "*":
                        self.stars |= bit
        for cx, cy in centres:
            for yy in range(cy - 1, cy + 2):
                for xx in range(cx - 1, cx + 2):
                    p = self.index(xx, yy)
                    if p:
                        self.calib |= self.bits[p]
        self.value_cells = self.floor & ~self.calib
        self.edges = [[(0, 0)] * 4 for _ in range(self.size)]
        self.edge_masks = [[0] * 4 for _ in range(self.size)]
        for i, p in enumerate(self.positions):
            if not i % 128:
                budget.check()
            y, x = divmod(p, self.stride)
            boost = speeds[y - 1][x - 1] == 2
            for d, offset in enumerate(self.deltas):
                first = p + offset
                if not self.floor & self.bits[first]:
                    continue
                last = first + offset if boost else first
                last = last if self.floor & self.bits[last] else 0
                self.edges[p][d] = first, last
                if last:
                    self.edge_masks[p][d] = self.bits[first] | self.bits[last]

    def index(self, x, y):
        if isinstance(x, int) and isinstance(y, int) and 0 <= x < self.w and 0 <= y < self.h:
            return (y + 1) * self.stride + x + 1
        return 0

    def pos(self, beam):
        return self.index(get(beam, "x"), get(beam, "y"))

    def around(self, cells):
        return (cells << 1) | (cells >> 1) | (cells << self.stride) | (cells >> self.stride)


class Board:
    def __init__(self, geom, state, budget):
        self.g = geom
        self.owns = [0, 0]
        self.trails = [0, 0]
        self.unknown = 0
        game = get(state, "game")
        flat_owner, flat_trail = get(game, "owner"), get(game, "trail")
        if flat_owner is not None and flat_trail is not None and len(flat_owner) == len(flat_trail) == geom.w*geom.h:
            w = geom.w
            owner = [flat_owner[i:i+w] for i in range(0, len(flat_owner), w)]
            contour = [flat_trail[i:i+w] for i in range(0, len(flat_trail), w)]
        else:
            owner, contour = get(state, "owner"), get(state, "contour")
        if owner is None or contour is None or len(owner) != geom.h or len(contour) != geom.h:
            raise ValueError("owner/contour height")
        for y, (own_row, trail_row) in enumerate(zip(owner, contour)):
            if not y % 4:
                budget.check()
            if len(own_row) != geom.w or len(trail_row) != geom.w:
                raise ValueError("owner/contour width")
            base = (y + 1) * geom.stride + 1
            for x, (o, c) in enumerate(zip(own_row, trail_row)):
                bit = geom.bits[base + x]
                side = label(o)
                if side >= 0:
                    self.owns[side] |= bit
                side = label(c, contour=True)
                if side >= 0:
                    self.trails[side] |= bit
                elif side == UNKNOWN:
                    self.unknown |= bit
        self.scores = tuple(self.score(m) for m in self.owns)
        reported = get(state, "score", get(state, "scores"))
        self.match_scores = (tuple(reported) if isinstance(reported, (list, tuple))
                             and len(reported) == 2 and all(type(s) in (int, float) for s in reported)
                             else self.scores)

    def score(self, owned):
        g = self.g
        cells = owned & g.value_cells
        neighbours = owned & g.floor
        s = g.stride
        shared = sum(popcount(cells & shift) for shift in
                     (neighbours << 1, neighbours >> 1, neighbours << s, neighbours >> s))
        perimeter = 4 * popcount(cells) - shared
        return popcount(neighbours) + 2 * popcount(neighbours & g.stars) - perimeter // 3

    def capture(self, side, drawn, budget):
        g = self.g
        # Game.fill_cells permits exterior flood through defects; only ownership and trails are walls.
        walls = self.owns[side] | self.trails[side] | self.unknown | drawn
        open_cells = g.grid & ~walls
        reach = g.border & open_cells
        for i in range(g.w * g.h + 1):
            if not i % 16:
                budget.check()
            expanded = reach | (g.around(reach) & open_cells)
            if expanded == reach:
                break
            reach = expanded
        return g.value_cells & ~reach & ~self.owns[side]

    def gain(self, side, capture):
        other = 1 - side
        return (self.score(self.owns[side] | capture) - self.scores[side]
                + self.scores[other] - self.score(self.owns[other] & ~capture))
