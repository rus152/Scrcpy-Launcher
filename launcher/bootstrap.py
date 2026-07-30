from __future__ import annotations

import asyncio
import re
import shutil
import stat
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable

import flet as ft


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


async def _download_scrcpy(
    project_root: Path,
    cancel_event: asyncio.Event,
    on_progress: Callable[[int, int], None],
) -> Path:
    with tempfile.TemporaryDirectory(prefix="scrcpy-download-") as temporary:
        archive_path = Path(temporary) / SCRCPY_ARCHIVE_NAME
        request = urllib.request.Request(
            SCRCPY_DOWNLOAD_URL,
            headers={"User-Agent": "ScrcpyLauncher/1.0"},
        )
        response = await asyncio.to_thread(urllib.request.urlopen, request, timeout=30)
        try:
            total = int(response.headers.get("Content-Length", "0") or 0)
            downloaded = 0
            with archive_path.open("wb") as output:
                while True:
                    if cancel_event.is_set():
                        raise DownloadCancelled
                    chunk = await asyncio.to_thread(response.read, 256 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    downloaded += len(chunk)
                    on_progress(downloaded, total)
        finally:
            response.close()
        if cancel_event.is_set():
            raise DownloadCancelled
        return await asyncio.to_thread(install_scrcpy_archive, archive_path, project_root)


async def ask_yes_no(page: ft.Page, title: str, message: str) -> bool:
    future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()

    def respond(value: bool) -> None:
        if not future.done():
            future.set_result(value)
        page.pop_dialog()

    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text(title),
        content=ft.Text(message),
        actions=[
            ft.TextButton("Нет", on_click=lambda e: respond(False)),
            ft.FilledButton("Да", on_click=lambda e: respond(True)),
        ],
    )
    page.show_dialog(dialog)
    return await future


async def show_message(page: ft.Page, title: str, message: str) -> None:
    future: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    def dismiss(_: object = None) -> None:
        if not future.done():
            future.set_result(None)
        page.pop_dialog()

    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text(title),
        content=ft.Text(message),
        actions=[ft.FilledButton("ОК", on_click=dismiss)],
    )
    page.show_dialog(dialog)
    await future


async def ensure_scrcpy(project_root: Path, page: ft.Page) -> bool:
    """Prompt for and install scrcpy 4.1 if the bundled Windows tools are absent."""
    if scrcpy_is_installed(project_root):
        return True

    missing = "\n".join(f"• {path.name}" for path in missing_scrcpy_files(project_root))
    confirmed = await ask_yes_no(
        page,
        "Scrcpy не найден",
        f"Рядом с main.py отсутствует комплект scrcpy:\n{missing}\n\n"
        f"Скачать официальный scrcpy {SCRCPY_VERSION} для Windows и распаковать его в папку scrcpy?",
    )
    if not confirmed:
        return False

    progress_text = ft.Text(f"Загрузка scrcpy {SCRCPY_VERSION}…")
    progress_bar = ft.ProgressBar(value=None, width=380)
    cancel_event = asyncio.Event()
    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("Установка scrcpy"),
        content=ft.Column([progress_text, progress_bar], tight=True),
        actions=[ft.TextButton("Отмена", on_click=lambda e: cancel_event.set())],
    )
    page.show_dialog(dialog)

    def on_progress(downloaded: int, total: int) -> None:
        if total > 0:
            progress_bar.value = min(1.0, downloaded / total)
            progress_text.value = (
                f"Загрузка scrcpy {SCRCPY_VERSION}… {downloaded / 1_048_576:.1f} из {total / 1_048_576:.1f} МБ"
            )
        else:
            progress_bar.value = None
            progress_text.value = f"Загрузка scrcpy {SCRCPY_VERSION}… {downloaded / 1_048_576:.1f} МБ"
        page.update()

    try:
        destination = await _download_scrcpy(project_root, cancel_event, on_progress)
    except DownloadCancelled:
        page.pop_dialog()
        return False
    except (OSError, ValueError, zipfile.BadZipFile, urllib.error.URLError) as error:
        page.pop_dialog()
        await show_message(page, "Не удалось установить scrcpy", f"{error}\n\nАрхив: {SCRCPY_DOWNLOAD_URL}")
        return False
    except Exception as error:  # pragma: no cover - defensive UI boundary
        page.pop_dialog()
        await show_message(
            page,
            "Не удалось установить scrcpy",
            f"Непредвиденная ошибка: {error}\n\nАрхив: {SCRCPY_DOWNLOAD_URL}",
        )
        return False

    page.pop_dialog()
    await show_message(
        page,
        "Scrcpy установлен",
        f"Scrcpy {SCRCPY_VERSION} загружен и распакован в:\n{destination}",
    )
    return True
