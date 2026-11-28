"""Decode, validate, and generate maps for the game and Bot protocol."""
import hashlib
import random
from collections import deque
from dataclasses import dataclass
from functools import cached_property
from typing import NamedTuple, Optional

from .protocol import dumps, json_object
from .rules import PARAMS, RULESET

__all__ = [
    'BOOST_SHAPES', 'C', 'N', 'STYLES', 'BoostZone', 'GameMap', 'Point',
    'bfs', 'draw', 'generate', 'load_map', 'near_calib', 'rot', 'validate',
]

N = PARAMS['size']
C = N - 1                      # 旋转中心：(x, y) <-> (C - x, C - y)
STYLES = ('open', 'walls', 'lanes', 'blocks', 'mixed')
BOOST_SHAPES = (
    ((0, -1), (-1, 0), (0, 0), (1, 0), (0, 1)),
    tuple((x, y) for y in range(-1, 2) for x in range(-1, 2)),
    ((0, -2), (0, -1), (-1, 0), (0, 0), (1, 0), (0, 1), (0, 2)),
)


class Point(NamedTuple):
    x: int
    y: int


@dataclass(frozen=True)
class BoostZone:
    marker: Point
    cells: tuple[Point, ...]


def _points(values, label):
    if not isinstance(values, (list, tuple)) or any(
        not isinstance(point, (list, tuple))
        or len(point) != 2
        or any(type(coord) is not int for coord in point)
        for point in values
    ):
        raise ValueError(f'{label}坐标无效')
    return tuple(Point(*point) for point in values)


@dataclass(frozen=True)
class GameMap:
    id: str
    stage: str
    size: int
    terrain: tuple[str, ...]
    calib: tuple[Point, Point]
    boost_zones: tuple[BoostZone, ...]
    name: Optional[str] = None
    en: Optional[str] = None

    @classmethod
    def from_payload(cls, payload):
        """Decode a complete map and validate its cell data."""
        if not isinstance(payload, dict):
            raise ValueError('地图须为 JSON 对象')
        stage = payload.get('stage', RULESET)
        if stage != RULESET:
            raise ValueError('地图须使用当前赛题规则')
        map_id = payload.get('id')
        if not isinstance(map_id, str) or not map_id:
            raise ValueError('地图缺少编号')
        size, terrain = payload.get('size'), payload.get('terrain')
        if (size != PARAMS['size'] or type(size) is not int or not isinstance(terrain, (list, tuple))
                or len(terrain) != size or any(not isinstance(row, str) or len(row) != size for row in terrain)):
            raise ValueError('尺寸不是 41×41')
        if any(set(row) - {'.', '#', '*'} for row in terrain):
            raise ValueError('出现 . # * 以外的字符')
        calib = _points(payload.get('calib'), '校准区中心')
        if len(calib) != 2:
            raise ValueError('必须提供两个校准区中心')
        if any(not (1 <= x < size - 1 and 1 <= y < size - 1) for x, y in calib):
            raise ValueError('校准区出界')

        raw_zones = payload.get('boost_zones')
        if not isinstance(raw_zones, (list, tuple)) or len(raw_zones) != 3:
            raise ValueError('地图必须包含 3 个加速区')
        zones = []
        for number, zone in enumerate(raw_zones, 1):
            label = f'加速区 {number}'
            if not isinstance(zone, dict):
                raise ValueError(f'{label}必须包含标记点和覆盖格')
            marker, = _points([zone.get('marker')], f'{label}标记点')
            cells = _points(zone.get('cells'), f'{label}覆盖格')
            if marker not in cells:
                raise ValueError(f'{label}的标记点不在区内')
            if any(not (0 <= x < size and 0 <= y < size) or terrain[y][x] == '#' for x, y in cells):
                raise ValueError(f'{label}包含缺陷格或界外格')
            zones.append(BoostZone(marker, cells))
        if len({zone.marker for zone in zones}) != len(zones):
            raise ValueError('各加速区的标记点须互不重合')

        return cls(map_id, stage, size, tuple(terrain), calib, tuple(zones), payload.get('name'), payload.get('en'))

    def to_payload(self):
        """Serialize the same map for Bot initialization and replay."""
        payload = {
            'id': self.id, 'stage': self.stage, 'size': self.size,
            'terrain': list(self.terrain), 'calib': [list(point) for point in self.calib],
            'boost_zones': [
                {'marker': list(zone.marker), 'cells': [list(cell) for cell in zone.cells]}
                for zone in self.boost_zones
            ],
        }
        if self.name is not None:
            payload['name'] = self.name
        if self.en is not None:
            payload['en'] = self.en
        return payload

    @cached_property
    def tiles(self):
        return ''.join(self.terrain)

    @cached_property
    def base(self):
        owners = [-1] * (self.size * self.size)
        for seat, (cx, cy) in enumerate(self.calib):
            for y in range(cy - 1, cy + 2):
                for x in range(cx - 1, cx + 2):
                    owners[y * self.size + x] = seat
        return tuple(owners)

    @cached_property
    def _boost_cells(self):
        return frozenset(cell for zone in self.boost_zones for cell in zone.cells)

    def move_steps(self, x, y):
        return 2 if (x, y) in self._boost_cells else 1


def rot(x, y):
    return C - x, C - y


def bfs(t, src):
    """t 为 41 个字符串（或字符列表的列表）。返回 {(x, y): 路程}。"""
    dist = {src: 0}
    q = deque([src])
    while q:
        x, y = q.popleft()
        d = dist[(x, y)] + 1
        for a, b in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= a < N and 0 <= b < N and t[b][a] != '#' and (a, b) not in dist:
                dist[(a, b)] = d
                q.append((a, b))
    return dist


def near_calib(x, y, calib, r=4):
    return any(abs(x - cx) <= r and abs(y - cy) <= r for cx, cy in calib)


# ---------------------------------------------------------------- 校验
def validate(m):
    """检查地图是否满足《赛题规则》的地图约束，返回错误列表（空列表表示通过）。"""
    try:
        board = m if isinstance(m, GameMap) else GameMap.from_payload(m)
    except ValueError as error:
        return [str(error)]
    err = []
    t = board.terrain
    for y in range(N):
        for x in range(N):
            if t[y][x] != t[C - y][C - x]:
                err.append('不对称：(%d,%d)' % (x, y))
                break
        if err and err[-1].startswith('不对称'):
            break
    c0, c1 = board.calib
    if rot(*c0) != c1:
        err.append('两个校准区不是 180° 旋转关系')
    if all(abs(a - b) <= 2 for a, b in zip(c0, c1)):
        err.append('两个 3×3 校准区重叠')
    free = sum(1 for r in t for c in r if c != '#')
    d0, d1 = bfs(t, c0), bfs(t, c1)
    if len(d0) != free:
        err.append('非缺陷格不连通：%d / %d' % (len(d0), free))
    for cx, cy in (c0, c1):
        for y in range(cy - 4, cy + 5):
            for x in range(cx - 4, cx + 5):
                inner = abs(x - cx) <= 1 and abs(y - cy) <= 1
                if not (0 <= x < N and 0 <= y < N):
                    continue
                if t[y][x] == '#':
                    err.append('校准区 (%d,%d) 周围 4 格内有缺陷 (%d,%d)' % (cx, cy, x, y))
                elif inner and t[y][x] != '.':
                    err.append('校准区 3×3 内不是空白格 (%d,%d)' % (x, y))
    nearest = min((distance for cell, distance in d0.items() if distance == d1.get(cell)), default=0)
    for number, zone in enumerate(board.boost_zones, 1):
        marker = zone.marker
        label = f'加速区 {number}'
        if near_calib(*marker, board.calib):
            err.append(f'{label}的标记点位于出生点周围 9×9 范围内')
        distance = d0.get(marker)
        if distance is None or distance != d1.get(marker):
            err.append(f'{label}的标记点到双方出生点的路程不等或不可达')
        elif distance > nearest + 2:
            err.append(f'{label}的标记点距最近等距格的路程超过 2 步')
    return err


# ---------------------------------------------------------------- 生成
def _pieces(rng, style):
    """返回一个缺陷形状（相对坐标列表）。"""
    k = rng.random()
    if style == 'lanes' and k < 0.7:
        n = rng.randint(7, 15)
        return [(i, 0) for i in range(n)] if rng.random() < 0.5 else [(0, i) for i in range(n)]
    if style == 'walls' and k < 0.6:
        n = rng.randint(3, 9)
        seg = [(i, 0) for i in range(n)] if rng.random() < 0.5 else [(0, i) for i in range(n)]
        if rng.random() < 0.4:                       # L 形
            m = rng.randint(2, 5)
            ex, ey = seg[-1]
            seg += [(ex, ey + j) for j in range(1, m)] if ey == 0 else [(ex + j, ey) for j in range(1, m)]
        return seg
    if style == 'blocks' and k < 0.7:
        w, h = rng.randint(2, 4), rng.randint(2, 4)
        return [(i, j) for i in range(w) for j in range(h)]
    if k < 0.35:
        return [(0, 0), (1, 0)] if rng.random() < 0.5 else [(0, 0), (0, 1)]
    if k < 0.6:
        return [(0, 0), (1, 0), (0, 1), (1, 1)]
    n = rng.randint(2, 6)
    return [(i, 0) for i in range(n)] if rng.random() < 0.5 else [(0, i) for i in range(n)]


def _keys(rng):
    k = rng.random()
    if k < 0.35:
        return [(0, 0), (1, 0), (0, 1), (1, 1)]
    if k < 0.55:
        return [(i, j) for i in range(3) for j in range(3)]
    if k < 0.75:
        return [(1, 0), (0, 1), (1, 1), (2, 1), (1, 2)]
    return [(0, 0), (1, 0), (2, 0)] if rng.random() < 0.5 else [(0, 0), (0, 1), (0, 2)]


def _try(rng, stage):
    style = rng.choice(STYLES)
    density = {'open': (0.004, 0.02), 'walls': (0.02, 0.07), 'lanes': (0.03, 0.07),
               'blocks': (0.03, 0.09), 'mixed': (0.02, 0.06)}[style]
    while True:
        cx, cy = rng.randint(5, 15), rng.randint(25, 35)
        if abs(cx - C / 2) + abs(cy - C / 2) >= 10:            # 两个校准区至少隔开 20 步左右
            break
    calib = [(cx, cy), rot(cx, cy)]
    g = [['.'] * N for _ in range(N)]
    target = int(N * N * rng.uniform(*density))
    count, tries = 0, 0
    total_free = N * N
    while count < target and tries < 400:
        tries += 1
        shape = _pieces(rng, style)
        ox, oy = rng.randint(0, C), rng.randint(0, C)
        cells = set()
        for dx, dy in shape:
            x, y = ox + dx, oy + dy
            if not (0 <= x < N and 0 <= y < N):
                cells = None
                break
            cells.add((x, y))
            cells.add(rot(x, y))
        if not cells or any(near_calib(x, y, calib) or g[y][x] != '.' or (abs(x - 20) <= 1 and abs(y - 20) <= 1)
                            for x, y in cells):
            continue
        for x, y in cells:
            g[y][x] = '#'
        if len(bfs(g, calib[0])) != total_free - len(cells):
            for x, y in cells:
                g[y][x] = '.'
            continue
        total_free -= len(cells)
        count += len(cells)
    keys_target = rng.randint(14, 34)
    kc, tries = 0, 0
    while kc < keys_target and tries < 300:
        tries += 1
        shape = _keys(rng)
        ox, oy = rng.randint(1, C - 3), rng.randint(1, C - 3)
        cells = set()
        for dx, dy in shape:
            cells.add((ox + dx, oy + dy))
            cells.add(rot(ox + dx, oy + dy))
        if any(g[y][x] != '.' or near_calib(x, y, calib, 2) for x, y in cells):
            continue
        for x, y in cells:
            g[y][x] = '*'
        kc += len(cells)
    t = [''.join(r) for r in g]
    # 选取靠近双方的等距标记点
    d0, d1 = bfs(t, calib[0]), bfs(t, calib[1])
    eq = [k for k in d0 if d0[k] == d1.get(k)]
    if not eq:
        return None
    dmin = min(d0[k] for k in eq)
    band = [k for k in eq if d0[k] <= dmin + 2 and not near_calib(*k, calib)]
    # 一对互为旋转的点（尽量分开），再加正中央或离这一对最远的一点
    pairs_ok = [k for k in band if k != rot(*k) and rot(*k) in band]
    rng.shuffle(pairs_ok)
    pairs_ok.sort(key=lambda k: -min(12, abs(2 * k[0] - C) + abs(2 * k[1] - C)))
    pts = []
    if pairs_ok:
        a = pairs_ok[rng.randrange(min(4, len(pairs_ok)))]
        pts = [a, rot(*a)]
    rest = [k for k in band if k not in pts]
    if (20, 20) in rest and rng.random() < 0.8:
        pts.append((20, 20))
    rest = [k for k in rest if k not in pts]
    rest.sort(key=lambda k: -min([abs(k[0] - p[0]) + abs(k[1] - p[1]) for p in pts] or [0]))
    pts += rest[:3 - len(pts)]
    if len(pts) < 3:
        return None
    shape = rng.choice(BOOST_SHAPES)
    zones = []
    for x, y in pts[:3]:
        candidates = [(x + dx, y + dy) for dx, dy in shape]
        cells = [[a, b] for a, b in candidates if 0 <= a < N and 0 <= b < N and t[b][a] != '#']
        zones.append({'marker': [x, y], 'cells': cells})
    return {'size': N, 'stage': stage, 'calib': [list(c) for c in calib], 'terrain': t,
            'boost_zones': zones, 'style': style}


def generate(seed=None, stage=RULESET):
    """生成符合《赛题规则》地图约束的地图。相同 seed 生成相同地图，省略 seed 时随机选取。"""
    if stage != RULESET:
        raise ValueError('此材料包适用初赛地图')
    if seed is None:
        seed = random.randrange(1, 10 ** 6)
    seed = int(seed)
    rng = random.Random('arena-map-%d' % seed)
    for _ in range(100):
        m = _try(rng, stage)
        if m:
            m.update({'id': 'R%d' % seed, 'name': '随机地图 %d' % seed, 'seed': seed})
            if not validate(m):
                return m
    raise RuntimeError('种子 %d 生成失败' % seed)


def load_map(spec, stage=RULESET):
    """Load a game map and hash the original bytes used by the judge."""
    if stage != RULESET:
        raise ValueError('裁判只接受当前赛题规则')
    if 'seed' in spec and 'file' not in spec and type(spec['seed']) is int:
        payload = generate(spec['seed'], stage)
        digest = hashlib.sha256(dumps(payload).encode('utf-8')).hexdigest()
        board = GameMap.from_payload(payload)
    elif 'file' in spec and 'seed' not in spec and isinstance(spec['file'], str):
        raw = spec['file'].encode('utf-8')
        digest = hashlib.sha256(raw).hexdigest()
        if digest != spec.get('sha256'):
            raise ValueError('封存地图的 SHA-256 不匹配')
        payload = json_object(raw)
        if payload.get('stage') != stage or payload.get('size') != N:
            raise ValueError('封存地图的赛段或尺寸不匹配')
        try:
            board = GameMap.from_payload(payload)
        except ValueError as error:
            raise ValueError(f'封存地图无效：{error}') from error
        problems = validate(board)
        if problems:
            raise ValueError('封存地图无效：' + '；'.join(problems))
    else:
        raise ValueError('config.map 必须包含整数 seed 或封存地图 file 与 sha256')
    return board, digest


def draw(m):
    """用 0、1 标记校准区，+ 标记加速格，A、B、C 标记各区的标记点。"""
    g = [list(r) for r in m['terrain']]
    for zone in m['boost_zones']:
        for x, y in zone['cells']:
            g[y][x] = '+'
    for s, (cx, cy) in enumerate(m['calib']):
        for y in range(cy - 1, cy + 2):
            for x in range(cx - 1, cx + 2):
                g[y][x] = str(s)
    for index, zone in enumerate(m['boost_zones']):
        x, y = zone['marker']
        g[y][x] = 'ABC'[index]
    return '\n'.join(' '.join(r) for r in g)
