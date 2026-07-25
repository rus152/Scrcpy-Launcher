from __future__ import annotations

import re
import shutil
import stat
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from PyQt6.QtCore import QEventLoop, QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import QMessageBox, QProgressDialog, QWidget


SCRCPY_VERSION = "4.1"
SCRCPY_ARCHIVE_NAME = f"scrcpy-win64-v{SCRCPY_VERSION}.zip"
SCRCPY_DOWNLOAD_URL = (
    f"https://github.com/Genymobile/scrcpy/releases/download/v{SCRCPY_VERSION}/{SCRCPY_ARCHIVE_NAME}"
)
REQUIRED_EXECUTABLES = ("scrcpy.exe", "adb.exe")
MAX_ARCHIVE_UNPACKED_SIZE = 1_000_000_000


class DownloadCancelled(Exception):
    pass


def scrcpy_directory(project_root: Path) -> Path:
    return project_root / "scrcpy"


def missing_scrcpy_files(project_root: Path) -> list[Path]:
    directory = scrcpy_directory(project_root)
    return [directory / filename for filename in REQUIRED_EXECUTABLES if not (directory / filename).is_file()]


def scrcpy_is_installed(project_root: Path) -> bool:
    return not missing_scrcpy_files(project_root)


def _validated_archive_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    unpacked_size = sum(member.file_size for member in members)
    if unpacked_size > MAX_ARCHIVE_UNPACKED_SIZE:
        raise ValueError("Архив scrcpy имеет недопустимо большой распакованный размер.")

    for member in members:
        name = member.filename.replace("\\", "/")
        path = PurePosixPath(name)
        unix_mode = (member.external_attr >> 16) & 0xFFFF
        if (
            not name
            or name.startswith("/")
            or re.match(r"^[A-Za-z]:", name)
            or ".." in path.parts
            or stat.S_ISLNK(unix_mode)
        ):
            raise ValueError(f"Небезопасный путь в архиве scrcpy: {member.filename}")
    return members


def install_scrcpy_archive(archive_path: Path, project_root: Path) -> Path:
    """Validate and unpack the official Windows bundle into ``project_root/scrcpy``."""
    project_root = project_root.resolve()
    target = scrcpy_directory(project_root)
    if target.exists() and not target.is_dir():
        raise ValueError(f"Путь установки занят файлом: {target}")

    with tempfile.TemporaryDirectory(prefix="scrcpy-extract-") as temporary:
        extraction_root = Path(temporary).resolve()
        with zipfile.ZipFile(archive_path) as archive:
            members = _validated_archive_members(archive)
            for member in members:
                relative = PurePosixPath(member.filename.replace("\\", "/"))
                destination = extraction_root.joinpath(*relative.parts)
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)

        candidates = [
            executable.parent
            for executable in extraction_root.rglob("scrcpy.exe")
            if (executable.parent / "adb.exe").is_file()
        ]
        if len(candidates) != 1:
            raise ValueError("В архиве не найден однозначный комплект scrcpy.exe и adb.exe.")
        shutil.copytree(candidates[0], target, dirs_exist_ok=True)

    missing = missing_scrcpy_files(project_root)
    if missing:
        raise OSError("После распаковки отсутствуют файлы: " + ", ".join(str(path) for path in missing))
    return target


class ScrcpyDownloadThread(QThread):
    progress = pyqtSignal(int, int)
    completed = pyqtSignal(str)
    failed = pyqtSignal(str)
    aborted = pyqtSignal()

    def __init__(self, project_root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project_root = project_root
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()
        self.requestInterruption()

    def _check_cancelled(self) -> None:
        if self._cancelled.is_set() or self.isInterruptionRequested():
            raise DownloadCancelled

    def run(self) -> None:
        try:
            with tempfile.TemporaryDirectory(prefix="scrcpy-download-") as temporary:
                archive_path = Path(temporary) / SCRCPY_ARCHIVE_NAME
                request = urllib.request.Request(
                    SCRCPY_DOWNLOAD_URL,
                    headers={"User-Agent": "ScrcpyLauncher/1.0"},
                )
                with urllib.request.urlopen(request, timeout=30) as response, archive_path.open("wb") as output:
                    total = int(response.headers.get("Content-Length", "0") or 0)
                    downloaded = 0
                    while True:
                        self._check_cancelled()
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        downloaded += len(chunk)
                        self.progress.emit(downloaded, total)
                self._check_cancelled()
                destination = install_scrcpy_archive(archive_path, self.project_root)
                self._check_cancelled()
                self.completed.emit(str(destination))
        except DownloadCancelled:
            self.aborted.emit()
        except (OSError, ValueError, zipfile.BadZipFile, urllib.error.URLError) as error:
            self.failed.emit(str(error))
        except Exception as error:  # pragma: no cover - defensive GUI boundary
            self.failed.emit(f"Непредвиденная ошибка: {error}")


def ensure_scrcpy(project_root: Path, parent: QWidget | None = None) -> bool:
    """Prompt for and install scrcpy 4.1 if the bundled Windows tools are absent."""
    if scrcpy_is_installed(project_root):
        return True

    missing = "\n".join(f"• {path.name}" for path in missing_scrcpy_files(project_root))
    answer = QMessageBox.question(
        parent,
        "Scrcpy не найден",
        f"Рядом с main.py отсутствует комплект scrcpy:\n{missing}\n\n"
        f"Скачать официальный scrcpy {SCRCPY_VERSION} для Windows и распаковать его в папку scrcpy?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes,
    )
    if answer != QMessageBox.StandardButton.Yes:
        return False

    dialog = QProgressDialog("Загрузка scrcpy 4.1…", "Отмена", 0, 0, parent)
    dialog.setWindowTitle("Установка scrcpy")
    dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)

    worker = ScrcpyDownloadThread(project_root, parent)
    loop = QEventLoop()
    state: dict[str, object] = {"installed": False, "error": "", "cancelled": False}

    def update_progress(downloaded: int, total: int) -> None:
        if total > 0:
            dialog.setRange(0, 1000)
            dialog.setValue(min(1000, int(downloaded * 1000 / total)))
            dialog.setLabelText(f"Загрузка scrcpy 4.1… {downloaded / 1_048_576:.1f} из {total / 1_048_576:.1f} МБ")
        else:
            dialog.setRange(0, 0)
            dialog.setLabelText(f"Загрузка scrcpy 4.1… {downloaded / 1_048_576:.1f} МБ")

    worker.progress.connect(update_progress)
    worker.completed.connect(lambda _path: state.update(installed=True))
    worker.failed.connect(lambda message: state.update(error=message))
    worker.aborted.connect(lambda: state.update(cancelled=True))
    worker.finished.connect(loop.quit)
    dialog.canceled.connect(worker.cancel)
    worker.start()
    dialog.show()
    loop.exec()
    worker.wait()
    dialog.close()

    if state["installed"]:
        QMessageBox.information(
            parent,
            "Scrcpy установлен",
            f"Scrcpy {SCRCPY_VERSION} загружен и распакован в:\n{scrcpy_directory(project_root)}",
        )
        return True
    if state["error"]:
        QMessageBox.critical(
            parent,
            "Не удалось установить scrcpy",
            f"{state['error']}\n\nАрхив: {SCRCPY_DOWNLOAD_URL}",
        )
    return False
