"""Background work for the Qt UI.

- `run_async`: short jobs (LLM calls) in a thread pool.
- `CliJob`: heavy jobs (whisper, pyannote) as a separate `voicenotes` process, so models are
  freed when done and the UI never blocks. Progress lines starting with "•" are forwarded.
"""
from __future__ import annotations

import sys
import traceback
from typing import Any, Callable

from ..cli import PROGRESS_PREFIX
from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QRunnable, QThreadPool, Signal


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any]):
        super().__init__()
        self.fn = fn
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result = self.fn()
        except Exception as e:  # report every failure to the UI
            traceback.print_exc()
            self.signals.failed.emit(str(e) or e.__class__.__name__)
        else:
            self.signals.done.emit(result)


_active: set[_Task] = set()


def run_async(fn: Callable[[], Any], on_done: Callable[[Any], None],
              on_error: Callable[[str], None]) -> None:
    task = _Task(fn)
    task.setAutoDelete(False)
    _active.add(task)

    def finish(cb, value):
        _active.discard(task)
        cb(value)

    task.signals.done.connect(lambda r: finish(on_done, r))
    task.signals.failed.connect(lambda e: finish(on_error, e))
    QThreadPool.globalInstance().start(task)


class CliJob(QObject):
    progress = Signal(str)
    finished = Signal(bool, str)  # ok, last error/output line

    def __init__(self, args: list[str], parent: QObject | None = None):
        super().__init__(parent)
        self.args = args
        self._last = ""
        self._buffer = ""
        self.proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("COLUMNS", "200")
        env.insert("NO_COLOR", "1")
        env.insert("VOICENOTES_GUI", "1")
        self.proc.setProcessEnvironment(env)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._finished)

    def start(self) -> None:
        self.proc.start(sys.executable, ["-m", "voicenotes.cli", *self.args])

    def _read(self) -> None:
        self._buffer += bytes(self.proc.readAllStandardOutput()).decode(errors="replace")
        *lines, self._buffer = self._buffer.split("\n")
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            if line.startswith(PROGRESS_PREFIX):
                self._last = line[len(PROGRESS_PREFIX):].strip()
                self.progress.emit(self._last)
            elif "Error" in line or "rror:" in line:
                self._last = line

    def _finished(self, code: int, status) -> None:
        self._read()
        ok = code == 0 and status == QProcess.ExitStatus.NormalExit
        self.finished.emit(ok, self._last)
