"""Run matches with Bot clients managed by Arena or launched locally."""
import argparse
import http.client
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from .game import Game
from .maps import generate
from .protocol import (MAX_RESPONSE_BYTES, dumps, end_message, init_message,
                       json_object, parse_reply, turn_message)
from .replay import Recorder, _write_text, public_observation, write_replay_index
from .rules import PARAMS, TIME_LIMIT_STATES, apply_startup, apply_turn


class HttpBot:
    """HTTP client for a Bot service whose lifecycle is managed by the caller."""

    exit_info = None

    def __init__(self, url):
        parsed = urlsplit(url)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise ValueError('Bot URL 必须是 HTTP(S) 服务根地址')
        self.scheme = parsed.scheme
        self.host = parsed.hostname
        self.port = parsed.port or (443 if self.scheme == 'https' else 80)

    def exited(self):
        return False

    def request(self, method, path, body, limit):
        """Return transport status, HTTP status, body and elapsed microseconds."""
        if self.exited():
            return 'crash', 0, None, 0
        headers = {'Connection': 'close'}
        data = None
        if body is not None:
            data = dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        t0 = time.perf_counter()
        connection = http.client.HTTPSConnection if self.scheme == 'https' else http.client.HTTPConnection
        conn = connection(self.host, self.port, timeout=limit)
        deadline = None
        try:
            conn.connect()
            sock = conn.sock
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            def cancel():
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

            remaining = limit - (time.perf_counter() - t0)
            if remaining <= 0:
                raise socket.timeout()
            sock.settimeout(remaining)
            # Bound the whole exchange, including slow headers and trickled bodies.
            deadline = threading.Timer(remaining, cancel)
            deadline.daemon = True
            deadline.start()
            conn.request(method, path, body=data, headers=headers)
            with conn.getresponse() as response:
                out = response.read(MAX_RESPONSE_BYTES + 1)
                if response.length and len(out) <= MAX_RESPONSE_BYTES:
                    raise http.client.IncompleteRead(out, response.length)
                code = response.status
        except (OSError, http.client.HTTPException, ValueError) as exc:
            us = int((time.perf_counter() - t0) * 1e6)
            status = 'timeout' if isinstance(exc, socket.timeout) or us >= limit * 1e6 else 'connection'
            return status, 0, str(exc)[:200], us
        finally:
            if deadline is not None:
                deadline.cancel()
            conn.close()
        us = int((time.perf_counter() - t0) * 1e6)
        if us > limit * 1e6:
            return 'timeout', code, None, us
        if len(out) > MAX_RESPONSE_BYTES:
            return 'invalid', code, None, us
        return 'ok', code, out, us

    # ---------------------------------------------------------------- 协议
    def start(self, limit_s):
        """等待就绪。返回 (ok, 说明)。"""
        end = time.monotonic() + limit_s
        while time.monotonic() < end:
            if self.exited():
                return False, 'crash'
            st, code, data, _ = self.request('GET', '/v1/health', None, min(1.0, max(0.001, end - time.monotonic())))
            if st == 'ok' and code == 200:
                try:
                    obj = json_object(data)
                except (ValueError, RecursionError):
                    obj = None
                if isinstance(obj, dict) and obj.get('ready') is True:
                    return True, 'ready'
            elif st == 'ok' and code in (401, 403):
                return False, 'startup'               # Bot 已拒绝启动请求。
            time.sleep(min(0.05, max(0, end - time.monotonic())))
        return False, 'startup' if not self.exited() else 'crash'

    def post(self, run_id, what, msg, limit_s):
        st, code, data, us = self.request('POST', f'/v1/matches/{run_id}/{what}', msg, limit_s)
        if st == 'ok' and not 200 <= code < 300:
            st = 'invalid'
        return st, code, data, us

    def turn(self, run_id, msg, limit_s):
        """返回 (cmd, status, raw, us)，区分超时、连接失败、无效回复和请求前的程序退出。"""
        t = msg['turn']
        st, code, data, us = self.post(run_id, 'turns/%d' % t, msg, limit_s)
        raw = data.decode('utf-8', 'replace')[:200] if isinstance(data, bytes) else data
        if st == 'crash':
            return None, 'crash', raw, us
        if st == 'timeout':
            return None, 'timeout', raw, us
        if st != 'ok' or code != 200:
            return None, 'connection' if st == 'connection' else 'invalid', raw, us
        r = parse_reply(data)
        if r is None or r[0] != t:
            return None, 'invalid', raw, us
        return r[1], 'ok', raw, us


def _free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class ProcessBot(HttpBot):
    """Launch a Bot and start its readiness check after launch."""

    def __init__(self, directory):
        from sdk.submission import submission_language

        self.directory = Path(directory).expanduser().resolve()
        self.language = submission_language(self.directory)
        self.proc = None
        self.resources = ExitStack()

    def start(self, limit_s):
        if self.proc is None:
            self._launch()
        return super().start(limit_s)

    def _launch(self):
        try:
            args, cwd = self._command()
            port = _free_port()
            super().__init__(f'http://127.0.0.1:{port}')
            env = dict(os.environ, BOT_HOST='127.0.0.1', BOT_PORT=str(port))
            options = {'start_new_session': True} if os.name != 'nt' else {}
            self.proc = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                         stdout=sys.stderr, **options)
        except BaseException:
            self.close()
            raise

    def _command(self):
        from sdk.submission import KIT_ROOT

        python = [sys.executable, '-E', '-B', '-u', '-m']
        if self.language == 'python':
            return python + ['sdk.python', str(self.directory)], KIT_ROOT
        workspace = Path(self.resources.enter_context(TemporaryDirectory(prefix='player-kit-build-')))
        if self.language == 'cpp':
            from sdk.cpp.build import build_bot

            binary = build_bot(self.directory, workspace)
            return python + ['sdk.cpp.cpp_gateway', str(binary), '--cwd', str(self.directory)], KIT_ROOT
        from sdk.java.build import build_bot, java_executable

        java = java_executable()
        jar = build_bot(self.directory, workspace)
        return [java, '-cp', str(jar), 'bot.Main'], self.directory

    def turn(self, run_id, msg, limit_s):
        deadline = time.monotonic() + limit_s
        reply = super().turn(run_id, msg, limit_s)
        if reply[1] == 'connection':
            # The socket can close before Windows signals process termination.
            try:
                self.proc.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
        return reply

    def exited(self):
        exit_code = self.proc.poll() if self.proc is not None else None
        if exit_code is not None:
            self.exit_info = {'exit_code': exit_code, 'oom': False}
        return exit_code is not None

    def _terminate(self):
        if os.name != 'nt':
            os.killpg(self.proc.pid, signal.SIGTERM)
            return
        try:
            subprocess.run(['taskkill', '/PID', str(self.proc.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if not self.exited():
            self.proc.terminate()

    def close(self):
        try:
            if self.proc is not None and not self.exited():
                try:
                    self._terminate()
                    self.proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    try:
                        if os.name == 'nt':
                            self.proc.kill()
                        else:
                            os.killpg(self.proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    self.proc.wait()
                except ProcessLookupError:
                    self.proc.wait()
        finally:
            self.resources.close()


class Match:
    """Run a timed match with the supplied Bot clients."""

    def __init__(self, board, bots, *, names, run_id, out_dir=None):
        self.board, self.bots, self.names = board, bots, names
        self.out_dir = None if out_dir is None else Path(out_dir)
        self.run_id = run_id

    @staticmethod
    def _observe(log, recorder, entry):
        if log is not None:
            log.write(dumps(entry) + '\n')
        recorder.data['observations'].append(public_observation(entry))

    def run(self):
        """Write the judge archive and return the result for the Arena task."""
        if self.out_dir is None:
            raise ValueError('正式裁判需要输出目录')
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with (self.out_dir / 'log.jsonl').open('w', encoding='utf-8') as log:
            game, errors, replay = self.play(log=log)
        (self.out_dir / 'replay.json').write_text(dumps(replay), encoding='utf-8')
        return dict(replay['result'], map=game.map.id, stage=game.map.stage, players=self.names,
                    errors=errors, run_id=self.run_id)

    def play(self, *, log=None):
        """Run the shared match loop and return the final state, errors, and replay."""
        game = Game(self.board)
        recorder = Recorder(game, self.names)
        errors = [0, 0]

        with ThreadPoolExecutor(2) as pool:
            starters = [pool.submit(bot.start, PARAMS['startup_s']) for bot in self.bots]
            ready = [future.result() for future in starters]
            startup = [state for _, state in ready]
            initial = [None, None]
            if all(ok for ok, _ in ready):
                futures = [
                    pool.submit(bot.post, self.run_id, 'init', init_message(game, seat), PARAMS['init_ms'] / 1000.0)
                    for seat, bot in enumerate(self.bots)
                ]
                initial = [future.result()[0] for future in futures]
            failures = apply_startup(game, errors, startup, initial, [bot.exited() for bot in self.bots])
            entry = {'phase': 'startup', 'turn': game.turn, 'startup': startup,
                     'init': initial, 'errors': list(errors), 'failures': failures,
                     'exits': [bot.exit_info for bot in self.bots]}
            if game.done:
                entry.update({'forfeit': game.winner, 'reason': game.reason})
            self._observe(log, recorder, entry)

            while not game.done:
                self._turn(game, recorder, pool, log, errors)

            message = end_message(game)
            pending = {
                seat: pool.submit(bot.post, self.run_id, 'end', message, PARAMS['end_ms'] / 1000.0)
                for seat, bot in enumerate(self.bots) if ready[seat][0] and not bot.exited()
            }
            ends = [pending[seat].result()[0] if seat in pending else None for seat in (0, 1)]
            self._observe(log, recorder, {'phase': 'end', 'turn': game.turn, 'end': ends,
                                          'winner': game.winner, 'reason': game.reason,
                                          'exits': [bot.exit_info for bot in self.bots]})

        return game, errors, recorder.finish()

    def _turn(self, game, recorder, pool, log, errors):
        message = turn_message(game)
        limit = PARAMS['turn_ms'] / 1000.0
        futures = [pool.submit(bot.turn, self.run_id, message, limit) for bot in self.bots]
        responses = [future.result() for future in futures]
        commands, statuses, replies, elapsed = [list(field) for field in zip(*responses)]
        for seat, status in enumerate(statuses):
            if status in TIME_LIMIT_STATES:
                elapsed[seat] = PARAMS['turn_ms'] * 1000

        apply_turn(game, errors, statuses, elapsed, [bot.exited() for bot in self.bots])
        entry = {'turn': message['turn'], 'cmds': commands, 'status': statuses,
                 'us': elapsed, 'errors': list(errors), 'raw': replies}
        if game.done:
            entry.update({'forfeit': game.winner, 'reason': game.reason,
                          'exits': [bot.exit_info for bot in self.bots]})
        else:
            events = game.step(commands)
            recorder.record([command or '' for command in commands], events,
                            [round(us / 1000.0, 2) for us in elapsed])
            entry.update({'score': [game.score(0), game.score(1)], 'events': events})
        self._observe(log, recorder, entry)


def play_match(board, submission, opponent, *, replay_path=None):
    directories = (submission, opponent)
    names = [Path(directory).name for directory in directories]
    run_id = f'run-{secrets.randbits(64)}.1'
    with ExitStack() as resources:
        bots = []
        for directory in directories:
            bot = ProcessBot(directory)
            resources.callback(bot.close)
            bots.append(bot)
        game, errors, replay = Match(board, bots, names=names, run_id=run_id).play()

    if replay_path is not None:
        replay['result']['errors'] = list(errors)
        _write_text(Path(replay_path), dumps(replay) + '\n')
    return game, errors


# Local command-line entry
BOTS_DIR = Path(__file__).resolve().parents[1] / 'bots'
REASONS = {
    'limit': '得分',
    'lead_turns': '领先回合数',
    'time': '累计响应时间',
    'tie': '三项指标相同，待裁定',
    'double_loss': '双方同时技术判负',
    'startup': 'Bot 未就绪',
    'crash': 'Bot 退出',
    'errors': '累计错误达到上限',
}


def resolve_bot(argument):
    directory = Path(argument).expanduser()
    if argument == directory.name and not directory.is_dir():
        directory = BOTS_DIR / argument
    return directory.resolve()


def main(argv=None):
    """Run one match from the player-kit command line."""
    parser = argparse.ArgumentParser(prog='python -m engine', description='在本地运行一局 Bot 对战')
    bot_directory = '填写 bots/ 下的目录名或包含 bot.json 的目录路径'
    parser.add_argument('p1', metavar='P1', help=f'第一个 Bot，{bot_directory}')
    parser.add_argument('p2', metavar='P2', help=f'第二个 Bot，{bot_directory}')
    parser.add_argument('--seed', type=int, help='整数地图种子。省略时随机选取')
    args = parser.parse_args(argv)

    try:
        p1 = resolve_bot(args.p1)
        p2 = resolve_bot(args.p2)
        players = [p1.name, p2.name]
        board = generate(args.seed)
        print(f'地图 {board["id"]} · {players[0]} 对 {players[1]}', flush=True)
        name = f'{datetime.now():%Y%m%d-%H%M%S}-{board["id"]}-{secrets.token_hex(4)}.json'
        replay_path = Path('replays') / name
        game, errors = play_match(board, p1, p2, replay_path=replay_path)
    except (OSError, ValueError) as error:
        parser.exit(1, f'{error}\n')
    except KeyboardInterrupt:
        parser.exit(130, '对局已停止\n')

    print(f'完成 {game.turn}/{game.max_turns} 回合')
    for seat, name in enumerate(players):
        print(f'  {seat} 号方 {name}  得分 {game.score(seat)}'
              f'  领先 {game.lead_turns[seat]} 回合  响应时间 {game.time_us[seat]} 微秒  错误 {errors[seat]} 次')
    outcome = '未判定胜方' if game.winner is None else f'{game.winner} 号方 {players[game.winner]} 获胜'
    if game.reason == 'double_loss':
        outcome = '双方各记一负'
    print(f'{outcome}（{REASONS[game.reason]}）')
    print(f'回放已保存至 {replay_path}')
    try:
        write_replay_index(replay_path.parent)
    except OSError as error:
        print(f'无法更新回放列表（{error}）', file=sys.stderr)
    return int(game.turn < game.max_turns)


if __name__ == '__main__':
    raise SystemExit(main())
