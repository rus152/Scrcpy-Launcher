from __future__ import annotations

import asyncio
import re
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable, TypeVar

import flet as ft

from .i18n import LANGUAGES, get_language, set_language, t


SCRCPY_VERSION = "5.0"
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
        raise ValueError(t("archive.too_large"))

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
            raise ValueError(t("archive.unsafe_path", filename=member.filename))
    return members


def install_scrcpy_archive(archive_path: Path, project_root: Path) -> Path:
    """Validate and unpack the official Windows bundle into ``project_root/scrcpy``."""
    project_root = project_root.resolve()
    target = scrcpy_directory(project_root)
    if target.exists() and not target.is_dir():
        raise ValueError(t("install.path_occupied", target=target))

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
            raise ValueError(t("install.ambiguous_bundle"))
        shutil.copytree(candidates[0], target, dirs_exist_ok=True)

    missing = missing_scrcpy_files(project_root)
    if missing:
        raise OSError(t("install.files_missing_after_extract", missing=", ".join(str(path) for path in missing)))
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


T = TypeVar("T")


async def _await_dialog(page: ft.Page, build: Callable[[Callable[[T], None]], ft.AlertDialog]) -> T:
    future: asyncio.Future[T] = asyncio.get_running_loop().create_future()

    def respond(value: T) -> None:
        if not future.done():
            future.set_result(value)
        page.pop_dialog()

    page.show_dialog(build(respond))
    return await future


async def ask_yes_no(page: ft.Page, title: str, message: str) -> bool:
    return await _await_dialog(
        page,
        lambda respond: ft.AlertDialog(
            modal=True,
            title=ft.Text(title),
            content=ft.Text(message),
            actions=[
                ft.TextButton(t("common.no"), on_click=lambda e: respond(False)),
                ft.FilledButton(t("common.yes"), on_click=lambda e: respond(True)),
            ],
        ),
    )


async def ask_language(page: ft.Page) -> str:
    """First-run language question, preselected to the current (detected) language.

    Asked before anything else so every later dialog — including the scrcpy download
    prompt below — is already in the user's language. The options are labelled with
    each language's own endonym, so the dialog stays readable even when the detected
    language guessed wrong.
    """
    dropdown = ft.Dropdown(
        value=get_language(),
        options=[ft.DropdownOption(key=code, text=name) for code, name in LANGUAGES.items()],
        width=200,
        dense=True,
    )
    title = ft.Text(value=t("language.choose_title"))
    message = ft.Text(value=t("language.choose_message"))
    # The message points at a button the user hasn't seen yet, so it is shown rather
    # than described: same icon, same rounded surface it sits on in the app bar.
    hint_label = ft.Text(value=t("launcher_settings.title"), size=13)
    hint = ft.Row(
        controls=[
            ft.Container(
                content=ft.Icon(ft.Icons.TUNE, size=20, color=ft.Colors.ON_SURFACE),
                width=40,
                height=40,
                border_radius=20,
                alignment=ft.Alignment.CENTER,
                bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            ),
            hint_label,
        ],
        spacing=10,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    confirm_button = ft.FilledButton(content=t("common.continue"))

    def preview(_: ft.ControlEvent) -> None:
        # Retranslate the dialog's own text as the selection moves, so the language is
        # previewed live instead of only taking effect once the dialog is gone.
        if dropdown.value:
            set_language(dropdown.value)
        title.value = t("language.choose_title")
        message.value = t("language.choose_message")
        hint_label.value = t("launcher_settings.title")
        confirm_button.content = t("common.continue")
        page.update()

    dropdown.on_select = preview

    def build(respond: Callable[[str], None]) -> ft.AlertDialog:
        confirm_button.on_click = lambda e: respond(dropdown.value or get_language())
        return ft.AlertDialog(
            modal=True,
            title=title,
            content=ft.Column(controls=[message, hint, dropdown], tight=True, width=380, spacing=16),
            actions=[confirm_button],
        )

    return await _await_dialog(page, build)


async def show_message(page: ft.Page, title: str, message: str) -> None:
    await _await_dialog(
        page,
        lambda respond: ft.AlertDialog(
            modal=True,
            title=ft.Text(title),
            content=ft.Text(message),
            actions=[ft.FilledButton(t("common.ok"), on_click=lambda e: respond(None))],
        ),
    )


async def ensure_scrcpy(project_root: Path, page: ft.Page) -> bool:
    """Prompt for and install scrcpy 5.0 if the bundled Windows tools are absent."""
    if scrcpy_is_installed(project_root):
        return True

    missing = "\n".join(f"• {path.name}" for path in missing_scrcpy_files(project_root))
    confirmed = await ask_yes_no(
        page,
        t("bootstrap.not_found_title"),
        t("bootstrap.not_found_message", missing=missing, version=SCRCPY_VERSION),
    )
    if not confirmed:
        return False

    progress_text = ft.Text(t("bootstrap.downloading", version=SCRCPY_VERSION))
    progress_bar = ft.ProgressBar(value=None, width=380)
    cancel_event = asyncio.Event()
    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text(t("bootstrap.installing_title")),
        content=ft.Column([progress_text, progress_bar], tight=True),
        actions=[ft.TextButton(t("common.cancel"), on_click=lambda e: cancel_event.set())],
    )
    page.show_dialog(dialog)

    def on_progress(downloaded: int, total: int) -> None:
        if total > 0:
            progress_bar.value = min(1.0, downloaded / total)
            progress_text.value = t(
                "bootstrap.downloading_progress",
                version=SCRCPY_VERSION,
                downloaded=downloaded / 1_048_576,
                total=total / 1_048_576,
            )
        else:
            progress_bar.value = None
            progress_text.value = t(
                "bootstrap.downloading_progress_unknown",
                version=SCRCPY_VERSION,
                downloaded=downloaded / 1_048_576,
            )
        page.update()

    try:
        destination = await _download_scrcpy(project_root, cancel_event, on_progress)
    except DownloadCancelled:
        page.pop_dialog()
        return False
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        page.pop_dialog()
        await show_message(
            page,
            t("bootstrap.install_failed_title"),
            t("bootstrap.install_failed_message", error=error, url=SCRCPY_DOWNLOAD_URL),
        )
        return False
    except Exception as error:  # pragma: no cover - defensive UI boundary
        page.pop_dialog()
        await show_message(
            page,
            t("bootstrap.install_failed_title"),
            t("bootstrap.install_unexpected_error", error=error, url=SCRCPY_DOWNLOAD_URL),
        )
        return False

    page.pop_dialog()
    await show_message(
        page,
        t("bootstrap.installed_title"),
        t("bootstrap.installed_message", version=SCRCPY_VERSION, destination=destination),
    )
    return True
