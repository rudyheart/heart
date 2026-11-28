"""Bound an optional external Python simulator call without taking over an SDK's timer."""
from contextlib import contextmanager
import signal
import threading


class ProbeTimeout(Exception):
    pass


def interruptible():
    return (hasattr(signal, "setitimer") and hasattr(signal, "ITIMER_REAL")
            and threading.current_thread() is threading.main_thread()
            and signal.getsignal(signal.SIGALRM) == signal.SIG_DFL
            and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0))


@contextmanager
def probe_window(milliseconds):
    if milliseconds <= 0 or not interruptible():
        yield False
        return

    def elapsed(signum, frame):
        raise ProbeTimeout()

    old = signal.signal(signal.SIGALRM, elapsed)
    try:
        signal.setitimer(signal.ITIMER_REAL, milliseconds / 1000)
        yield True
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
