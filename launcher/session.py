from __future__ import annotations

from uuid import uuid4

from PyQt6.QtCore import QObject, QProcess, pyqtSignal

from .models import SessionInfo


class ScrcpySession(QObject):
    changed = pyqtSignal(object)

    def __init__(self, program: str, arguments: list[str], serial: str, package: str, title: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.info = SessionInfo(uuid4().hex, serial, package, title, arguments)
        self.process = QProcess(self)
        self.process.setProgram(program)
        self.process.setArguments(arguments)
        self.process.readyReadStandardOutput.connect(self._append_output)
        self.process.readyReadStandardError.connect(self._append_error)
        self.process.errorOccurred.connect(self._error)
        self.process.finished.connect(self._finished)

    def start(self) -> None:
        self.process.start()
        self.changed.emit(self.info)

    def stop(self) -> None:
        if self.process.state() == QProcess.ProcessState.NotRunning:
            return
        self.info.state = "stopping"
        self.changed.emit(self.info)
        self.process.terminate()
        if not self.process.waitForFinished(1500):
            self.process.kill()

    def _append_output(self) -> None:
        self.info.log += bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        self.changed.emit(self.info)

    def _append_error(self) -> None:
        self.info.log += bytes(self.process.readAllStandardError()).decode(errors="replace")
        self.changed.emit(self.info)

    def _error(self, _error: QProcess.ProcessError) -> None:
        self.info.state = "error"
        self.info.log += f"\n{self.process.errorString()}"
        self.changed.emit(self.info)

    def _finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        self._append_output()
        self._append_error()
        self.info.state = "finished" if exit_code == 0 else f"stopped ({exit_code})"
        self.changed.emit(self.info)
