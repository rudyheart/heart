"""Game parameters, tiebreakers, and technical forfeits."""

RULESET = 'prelim'

DX = {'U': 0, 'D': 0, 'L': -1, 'R': 1}
DY = {'U': -1, 'D': 1, 'L': 0, 'R': 0}
OPP = {'U': 'D', 'D': 'U', 'L': 'R', 'R': 'L'}
DIRS = ('U', 'D', 'L', 'R')
TIEBREAK_METRICS = ('score', 'lead_turns', 'time_us')

# 与规则文档各节的参数一致
PARAMS = {
    'size': 41,               # 基板边长
    'max_turns': 600,         # 回合上限
    'restart': 5,             # 轮廓报废后停机校准回合
    'key_value': 3,           # 关键图形区价值
    'startup_s': 10,          # 启动：GET /v1/health 须在 10 秒内返回就绪
    'init_ms': 2000,          # 开局请求时限
    'turn_ms': 200,           # 每回合请求时限
    'end_ms': 1000,           # 结束请求时限
    'max_errors': 10,         # 累计错误达到此数判负
}

REQUEST_STATES = ('ok', 'timeout', 'connection', 'invalid', 'crash')
STARTUP_STATES = ('ready', 'startup', 'crash')
TURN_ERRORS = ('timeout', 'connection', 'invalid')
TIME_LIMIT_STATES = ('timeout', 'connection')


def valid_command(cmd):
    return isinstance(cmd, str) and cmd in DIRS


def compare_metrics(metrics):
    """Return the winning side index and the first differing metric."""
    for key in TIEBREAK_METRICS:
        a, b = metrics[key]
        if a != b:
            return int(b < a) if key == 'time_us' else int(b > a), key
    return None, None


def _forfeit(game, failures):
    losers = [seat for seat, reason in enumerate(failures) if reason]
    if len(losers) == 1:
        loser = losers[0]
        game.forfeit(loser, failures[loser])
    elif losers:
        game.forfeit(-1, 'double_loss')


def apply_startup(game, errors, startup, initial, exits):
    failures = []
    for seat, (state, init, exited) in enumerate(zip(startup, initial, exits)):
        failure = None
        if exited or state == 'crash' or init == 'crash':
            failure = 'crash'
        elif state != 'ready':
            failure = 'startup'
        elif init is not None and init != 'ok':
            errors[seat] += 1
        failures.append(failure)
    _forfeit(game, failures)
    return failures


def apply_turn(game, errors, statuses, us, exits):
    failures = []
    for seat, (status, elapsed, exited) in enumerate(zip(statuses, us, exits)):
        # Both issued requests count, including the round that triggers a forfeit.
        game.time_us[seat] += elapsed
        if status in TURN_ERRORS:
            errors[seat] += 1
        if exited or status == 'crash':
            failures.append('crash')
        else:
            failures.append('errors' if errors[seat] >= PARAMS['max_errors'] else None)
    _forfeit(game, failures)
