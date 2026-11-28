"""Record, verify, and index public match replays."""
import json
from base64 import b64encode
from gzip import compress
from pathlib import Path
from stat import S_ISREG
from tempfile import NamedTemporaryFile

from . import VERSION
from .game import Game
from .maps import GameMap, validate as validate_map
from .protocol import board_status, dumps, grid_rows, json_object
from .rules import (PARAMS, REQUEST_STATES, RULESET, STARTUP_STATES, TIME_LIMIT_STATES,
                    apply_startup, apply_turn, valid_command)


def game_result(game):
    return {'winner': game.winner, 'reason': game.reason, 'turns': game.turn, **game.metrics()}


def public_observation(entry):
    """Keep rule inputs while omitting private responses and exit details."""
    keys = {
        'startup': ('phase', 'startup', 'init'),
        'end': ('phase', 'reason'),
        None: ('turn', 'status', 'us'),
    }[entry.get('phase')]
    result = {key: entry[key] for key in keys}
    if 'exits' in entry:
        result['exits'] = [info is not None for info in entry['exits']]
    return result


def _cells(game):
    owner, contour = grid_rows(game)
    return ''.join(owner), ''.join(contour)


class Recorder:
    def __init__(self, game, players):
        self.game = game
        self.previous = _cells(game)
        owner, contour = self.previous
        initial = {'owner': owner, 'contour': contour, **board_status(game)}
        self.data = {
            'version': VERSION,
            'map': game.map.to_payload(),
            'stage': game.map.stage,
            'players': list(players),
            'params': dict(PARAMS),
            'init': initial,
            'frames': [],
            'observations': [],
            'result': None,
        }

    def record(self, commands, events, ms=None):
        game = self.game
        cells = _cells(game)
        changes = []
        for index, (old, new) in enumerate(zip(zip(*self.previous), zip(*cells))):
            if old != new:
                changes.append([index, *new])
        self.previous = cells
        frame = {
            'turn': game.turn,
            'cmds': list(commands),
            **board_status(game),
            'events': events,
            'd': changes,
        }
        if ms is not None:
            frame['ms'] = list(ms)
        self.data['frames'].append(frame)
        return frame

    def finish(self):
        self.data['result'] = game_result(self.game)
        return self.data


# Replay verification
class VerificationError(ValueError):
    pass


def _json(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


def check_fields(recorded, expected, source):
    if not isinstance(recorded, dict):
        raise VerificationError(f'{source} 必须是 JSON 对象')
    for key, value in expected.items():
        if key not in recorded or _json(recorded[key]) != _json(value):
            raise VerificationError(f'{source} 的 {key} 与重算不符')


def read_referee_log(path):
    entries = []
    with path.open('rb') as stream:
        for number, line in enumerate(stream, 1):
            try:
                entries.append(json_object(line))
            except (ValueError, RecursionError) as error:
                raise VerificationError(f'裁判日志第 {number} 行不是有效的 JSON 对象') from error
    return entries


def _pair(value, field):
    if not isinstance(value, list) or len(value) != 2:
        raise VerificationError(f'{field} 必须按出生点提供两项')
    return value


def _check_snapshot(recorded, expected, source):
    check_fields(recorded, {k: v for k, v in expected.items() if k != 'beams'}, source)
    beams = _pair(recorded.get('beams'), source + ' beams')
    for seat, (actual, beam) in enumerate(zip(beams, expected['beams'])):
        check_fields(actual, beam, f'{source} 的 {seat} 号束斑')


def _exit_flags(observation):
    flags = _pair(observation.get('exits', [False, False]), 'exits')
    if any(type(flag) is not bool for flag in flags):
        raise VerificationError('回放观测的 exits 必须为布尔值')
    return flags


def _observations(replay):
    observed = replay.get('observations')
    if not isinstance(observed, list) or len(observed) < 2 or any(not isinstance(entry, dict) for entry in observed):
        raise VerificationError('回放缺少完整的裁判观测')
    if observed[0].get('phase') != 'startup' or observed[-1].get('phase') != 'end':
        raise VerificationError('裁判观测必须按开局、回合、终止排列')
    return observed


def _check_log_observations(log, observations):
    if len(log) != len(observations):
        raise VerificationError('裁判日志与回放的观测数量不一致')
    for number, (entry, observation) in enumerate(zip(log, observations), 1):
        if not isinstance(entry, dict):
            raise VerificationError(f'裁判日志第 {number} 行必须是 JSON 对象')
        for info in _pair(entry.get('exits', [None, None]), '裁判日志 exits'):
            if info is not None and (not isinstance(info, dict) or type(info.get('exit_code')) is not int
                                     or type(info.get('oom')) is not bool):
                raise VerificationError(f'裁判日志第 {number} 行的退出信息无效')
        public = public_observation(entry)
        check_fields(dict(observation, exits=_exit_flags(observation)),
                     dict(public, exits=_exit_flags(public)), f'第 {number} 条回放观测与裁判日志')


def _check_log_fields(entry, expected, source):
    if entry is not None:
        check_fields(entry, {key: value for key, value in expected.items() if key in entry}, source)


def _open_game(game, errors, opening, log_entry):
    startup = _pair(opening['startup'], 'startup')
    initial = _pair(opening['init'], 'init')
    allowed_init = REQUEST_STATES if startup == ['ready', 'ready'] else (None,)
    if any(state not in STARTUP_STATES for state in startup) or any(state not in allowed_init for state in initial):
        raise VerificationError('开局请求状态无效')
    failures = apply_startup(game, errors, startup, initial, _exit_flags(opening))
    expected = {'turn': game.turn, 'errors': errors, 'failures': failures}
    if game.done:
        expected.update(forfeit=game.winner, reason=game.reason)
    _check_log_fields(log_entry, expected, '开局裁判日志')


def _turn_observation(observation, turn):
    source = f'第 {turn} 回合的裁判观测'
    if observation.get('phase') is not None:
        raise VerificationError(f'{source} 的阶段无效')
    check_fields(observation, {'turn': turn}, source)
    statuses = _pair(observation['status'], 'status')
    timing = _pair(observation['us'], 'us')
    limit = PARAMS['turn_ms'] * 1000
    for status, elapsed in zip(statuses, timing):
        if status not in REQUEST_STATES or type(elapsed) is not int or not 0 <= elapsed <= limit:
            raise VerificationError(f'{source} 的请求状态或耗时无效')
        if (status in TIME_LIMIT_STATES and elapsed != limit) or (status == 'crash' and elapsed != 0):
            raise VerificationError(f'{source} 的计时不符合请求状态')
    return statuses, timing, _exit_flags(observation)


def _commands(values, statuses, source):
    commands = _pair(values, source + ' cmds')
    for status, command in zip(statuses, commands):
        valid = command == ''
        if status == 'ok':
            valid = valid_command(command)
        if not valid:
            raise VerificationError(f'{source} 的命令与请求状态不符')
    return commands


def _logged_commands(entry, statuses, source):
    if entry is None or 'cmds' not in entry:
        return None
    commands = _pair(entry['cmds'], source + ' cmds')
    return _commands(['' if command is None else command for command in commands], statuses, source)


def _recompute(recorder, frames, observations, log):
    game = recorder.game
    errors = [0, 0]
    _open_game(game, errors, observations[0], log[0] if log is not None else None)

    for turn, observation in enumerate(observations[1:-1], 1):
        if game.done:
            raise VerificationError(f'对局已结束，仍有第 {turn} 回合的观测')
        statuses, timing, exits = _turn_observation(observation, turn)
        entry = log[turn] if log is not None else None
        log_source = f'第 {turn} 回合的裁判日志'
        logged = _logged_commands(entry, statuses, log_source)
        apply_turn(game, errors, statuses, timing, exits)
        if game.done:
            _check_log_fields(entry, {'errors': errors, 'forfeit': game.winner, 'reason': game.reason}, log_source)
            continue

        frame_source = f'第 {turn} 回合的回放帧'
        if turn > len(frames) or not isinstance(frames[turn - 1], dict):
            raise VerificationError(f'缺少有效的{frame_source}')
        frame = frames[turn - 1]
        commands = _commands(frame['cmds'], statuses, frame_source)
        if logged is not None and logged != commands:
            raise VerificationError(f'{frame_source} 的命令与裁判日志不符')
        events = game.step([command or None for command in commands])
        expected = recorder.record(commands, events)
        _check_snapshot(frame, expected, frame_source)
        _check_log_fields(entry, {'errors': errors, 'score': expected['score'], 'events': events}, log_source)

    if not game.done or len(frames) != game.turn:
        raise VerificationError('回放帧和裁判观测未对应到一个完整终局')
    end = observations[-1]
    _exit_flags(end)
    check_fields(end, {'reason': game.reason}, '终止观测')
    _check_log_fields(log[-1] if log is not None else None,
                      {'turn': game.turn, 'winner': game.winner}, '终止裁判日志')


def _replay_game(replay, board):
    version = replay.get('version')
    if version != VERSION:
        raise VerificationError(f'回放版本不受支持（{version!r}）')
    recorded_map = replay.get('map')
    if replay.get('stage') != RULESET or not isinstance(recorded_map, dict) or 'boost_zones' not in recorded_map:
        raise VerificationError('回放须使用当前赛题规则')
    if board is None:
        board = GameMap.from_payload(recorded_map)
        if recorded_map.get('stage') != RULESET or validate_map(board):
            raise VerificationError('回放地图无效')
    else:
        if not isinstance(board, GameMap):
            board = GameMap.from_payload(board)
        check_fields(recorded_map, board.to_payload(), '回放地图')
    return Game(board)


def verify_replay(replay, *, board=None, log=None):
    """Check the recorded initial state, all settled rounds, and the result."""
    try:
        game = _replay_game(replay, board)
        players = _pair(replay['players'], 'players')
        if any(not isinstance(name, str) for name in players):
            raise VerificationError('回放中的 Bot 名称须为字符串')
        recorder = Recorder(game, players)
        check_fields(replay, {'params': recorder.data['params']}, '回放')
        _check_snapshot(replay['init'], recorder.data['init'], '回放初始局面')

        frames = replay['frames']
        if not isinstance(frames, list):
            raise VerificationError('frames 必须是数组')
        observations = _observations(replay)
        if log is not None:
            _check_log_observations(log, observations)
        _recompute(recorder, frames, observations, log)
        check_fields(replay['result'], game_result(game), '回放结果')
        return game
    except VerificationError:
        raise
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, RecursionError) as error:
        raise VerificationError(f'回放或观测格式无效 ({error})') from error


# Offline replay index
def _write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _replay_sources(directory):
    sources = []
    for path in directory.glob('*'):
        if path.suffix.lower() != '.json':
            continue
        try:
            info = path.stat()
        except FileNotFoundError:
            continue
        if S_ISREG(info.st_mode):
            sources.append((path, info.st_mtime_ns, info.st_size))
    return sorted(sources, key=lambda source: (-source[1], source[0].name))


def write_replay_index(directory):
    """Make the replay JSON available to a page opened through file://."""
    directory = Path(directory)
    while True:
        sources = _replay_sources(directory)
        entries = []
        for path, _, _ in sources:
            try:
                raw = path.read_bytes()
                replay = json_object(raw)
            except (OSError, ValueError, RecursionError):
                continue
            board = replay.get('map')
            params = replay.get('params')
            if (replay.get('version') == VERSION and replay.get('stage') == RULESET
                    and isinstance(params, dict) and params.get('max_turns') == PARAMS['max_turns']
                    and isinstance(board, dict) and isinstance(board.get('boost_zones'), list)):
                encoded = b64encode(compress(raw, mtime=0)).decode('ascii')
                entries.append('  ' + dumps([path.name, encoded]))

        header = '// Generated by python -m engine.replay from the adjacent JSON files.\n'
        listing = ',\n'.join(entries)
        _write_text(directory / 'index.js', f'{header}globalThis.playerKitReplayIndex = [\n{listing}\n];\n')
        # Another local match may have saved its replay while this index was built.
        if _replay_sources(directory) == sources:
            return


if __name__ == '__main__':
    write_replay_index('replays')
