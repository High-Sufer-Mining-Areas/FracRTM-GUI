from __future__ import annotations

import codecs
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from PyQt5 import QtCore

from fracrtm.messages import message as msg
from fracrtm.messages import prepare_json, render_record


def _now():
    return datetime.now(timezone.utc).isoformat()


class LoggedProcess(QtCore.QObject):
    completed = QtCore.pyqtSignal(int, str)
    output = QtCore.pyqtSignal(str, bool)
    cancellation_failed = QtCore.pyqtSignal(object)
    persistence_failed = QtCore.pyqtSignal(object)

    def __init__(self, directory, program, arguments, *, cancel_command=None, parent=None):
        super().__init__(parent)
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.program, self.arguments = (str(program), list(map(str, arguments)))
        self.cancel_command = list(cancel_command) if cancel_command else None
        self.process = QtCore.QProcess(self)
        self.process.setProcessChannelMode(QtCore.QProcess.SeparateChannels)
        self.process.readyReadStandardOutput.connect(lambda: self._read(False))
        self.process.readyReadStandardError.connect(lambda: self._read(True))
        self.process.errorOccurred.connect(self._error)
        self.process.finished.connect(self._finished)
        self.process.started.connect(self._started)
        self._terminal = False
        self._stop_requested = False
        self._cancel_process = None
        self._pending_finish = None
        self._started_clock = None
        self._persistence_failed = False
        self._storage_stop_scheduled = False
        self._pending_logs = {"stdout": bytearray(), "stderr": bytearray()}
        self._decoders = {
            error: codecs.getincrementaldecoder("utf-8")(errors="replace")
            for error in (False, True)
        }
        self.record = {
            "command": [self.program, *self.arguments],
            "status": "created",
            "started_at": None,
            "ended_at": None,
            "returncode": None,
            "stdout": "stdout.log",
            "stderr": "stderr.log",
            "error": "",
        }
        for name in ("stdout.log", "stderr.log"):
            (self.directory / name).touch(exist_ok=True)

    def _save(self):
        path = self.directory / "execution.json"
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(
                json.dumps(prepare_json(render_record(self.record)), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(path)
            return True
        except OSError as exc:
            self._persistence_error(msg("text.execution.metadata"), path, exc)
            return False

    def _persistence_error(self, operation, path, error):
        self._persistence_failed = True
        issue = {"operation": operation, "path": str(path), "error": str(error), "at": _now()}
        self.record.setdefault("persistence_errors", []).append(issue)
        message = msg(
            "diagnostic.logged_process.operation.could.not.be.persisted.path.error",
            operation=operation,
            path=path,
            error=error,
        )
        self.record["error"] = message
        self.persistence_failed.emit(message)
        if (
            not self._terminal
            and (not self._storage_stop_scheduled)
            and (self.process.state() != QtCore.QProcess.NotRunning)
        ):
            self._storage_stop_scheduled = True
            QtCore.QTimer.singleShot(0, self._stop_after_storage_failure)

    def _stop_after_storage_failure(self):
        if not self._terminal and self.process.state() != QtCore.QProcess.NotRunning:
            self.stop()

    def pending_log_bytes(self):
        return {name: bytes(value) for name, value in self._pending_logs.items()}

    def start(self):
        if self._started_clock is not None:
            raise RuntimeError(msg("text.a.loggedprocess.cannot.be.reused.for.another.execution"))
        self._started_clock = time.monotonic()
        self.record.update(status="starting", started_at=_now())
        if not self._save():
            self._finish(-1, "log_failed")
            return
        self.process.start(self.program, self.arguments)

    def _started(self):
        self.record.update(status="running", local_process_id=int(self.process.processId()))
        self._save()

    def _read(self, error):
        data = (
            self.process.readAllStandardError() if error else self.process.readAllStandardOutput()
        )
        raw = bytes(data)
        if raw:
            name = "stderr" if error else "stdout"
            path = self.directory / (name + ".log")
            try:
                with path.open("ab") as stream:
                    stream.write(raw)
            except OSError as exc:
                self._pending_logs[name].extend(raw)
                self._persistence_error(name, path, exc)
            self.output.emit(self._decoders[error].decode(raw), error)

    def _error(self, error):
        self.record["error"] = self.process.errorString()
        self._save()
        # FailedToStart does not emit Qt's finished signal.
        if error == QtCore.QProcess.FailedToStart:
            self._finish(-1, "start_failed")

    def _finished(self, code, status):
        if self._cancel_process is not None:
            self._pending_finish = (code, status)
            return
        reason = (
            "cancelled"
            if self._stop_requested
            else "succeeded"
            if code == 0 and status == QtCore.QProcess.NormalExit
            else "failed"
        )
        self._finish(int(code), reason)

    def _finish(self, code, reason):
        if self._terminal:
            return
        self._terminal = True
        self._read(False)
        self._read(True)
        for error, decoder in self._decoders.items():
            tail = decoder.decode(b"", final=True)
            if tail:
                self.output.emit(tail, error)
        self.record.update(
            status="log_failed" if self._persistence_failed else reason,
            process_outcome=reason,
            returncode=code,
            ended_at=_now(),
            duration_seconds=0
            if self._started_clock is None
            else time.monotonic() - self._started_clock,
        )
        self._save()
        terminal_reason = "log_failed" if self._persistence_failed else reason
        self.record["status"] = terminal_reason
        self.completed.emit(code, terminal_reason)

    def stop(self):
        if self._terminal or self._cancel_process is not None:
            return
        self.record["cancellation_requested_at"] = _now()
        if self.cancel_command:
            # Route cancellation through the token-bound supervisor.
            self.record["cancel_command"] = self.cancel_command
            proc = self._cancel_process = QtCore.QProcess(self)
            proc.finished.connect(self._cancel_finished)
            proc.errorOccurred.connect(self._cancel_error)
            self._save()
            proc.start(self.cancel_command[0], self.cancel_command[1:])
            QtCore.QTimer.singleShot(15000, self._cancel_timeout)
        else:
            if Path(self.program).name.casefold() in {"wsl", "wsl.exe"}:
                self.cancellation_failed.emit(
                    msg("text.missing.remote.cancellation.identity.no.process.was.killed")
                )
                return
            self._stop_requested = True
            self._save()
            self.process.terminate()
            QtCore.QTimer.singleShot(5000, self._kill_local_if_running)

    def _kill_local_if_running(self):
        if not self._terminal and self._stop_requested:
            self.process.kill()

    def _cancel_error(self, error):
        if error == QtCore.QProcess.FailedToStart:
            self._cancel_finished(-1, QtCore.QProcess.CrashExit)

    def _cancel_timeout(self):
        if self._cancel_process is not None:
            self._cancel_process.kill()

    def _cancel_finished(self, code, status):
        proc = self._cancel_process
        if proc is None:
            return
        text = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        error = (
            bytes(proc.readAllStandardError()).decode("utf-8", errors="replace")
            or proc.errorString()
        )
        self.record["cancellation"] = {"returncode": int(code), "stdout": text, "stderr": error}
        if code == 0 and status == QtCore.QProcess.NormalExit:
            try:
                acknowledgement = json.loads(text)
            except ValueError:
                acknowledgement = {}
            if acknowledgement.get("terminated_group") and acknowledgement.get("remaining") == []:
                self._stop_requested = True
            elif not acknowledgement.get("already_finished"):
                self.cancellation_failed.emit(
                    msg("text.remote.cancellation.acknowledgement.is.invalid") + text
                )
        else:
            self.cancellation_failed.emit(
                error or text or msg("text.remote.termination.was.not.confirmed")
            )
        self._save()
        self._cancel_process = None
        proc.deleteLater()
        if self._pending_finish is not None:
            pending, self._pending_finish = (self._pending_finish, None)
            self._finished(*pending)
