from __future__ import annotations

import base64
import contextlib
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


HELPER_REMOTE_PATH = "/data/local/tmp/scrcpy-launcher/icon-dumper.dex"
HELPER_MAIN_CLASS = "io.scrcpy.launcher.IconDumper"
HELPER_RECORD_PREFIX = "SCRCPY_APP\t"


@dataclass(frozen=True, slots=True)
class DeviceAppRecord:
    package: str
    label: str
    is_system: bool
    revision: str
    png: bytes = b""


def parse_device_app_records(output: str) -> list[DeviceAppRecord]:
    records: list[DeviceAppRecord] = []
    for line in output.splitlines():
        if not line.startswith(HELPER_RECORD_PREFIX):
            continue
        parts = line.split("\t", 5)
        if len(parts) != 6:
            continue
        _prefix, package, encoded_label, system, revision, encoded_png = parts
        try:
            label = base64.b64decode(encoded_label, validate=True).decode("utf-8", errors="replace")
            png = base64.b64decode(encoded_png, validate=True) if encoded_png else b""
        except (ValueError, UnicodeError):
            continue
        if not package or not revision or (png and not png.startswith(b"\x89PNG\r\n\x1a\n")):
            continue
        records.append(DeviceAppRecord(package, label or package, system == "1", revision, png))
    return records


def rendered_icon_directory(cache_dir: Path, device_key: str) -> Path:
    device_token = hashlib.sha256(device_key.encode("utf-8")).hexdigest()[:20]
    return cache_dir / "android-rendered-icons-v3" / device_token


def _package_prefix(package: str) -> str:
    return hashlib.sha256(package.encode("utf-8")).hexdigest()[:24] + "-"


def _replace_atomically(destination: Path, data: bytes) -> None:
    temporary = destination.with_suffix(".tmp")
    temporary.write_bytes(data)
    temporary.replace(destination)


def cached_rendered_icon(cache_dir: Path, device_key: str, package: str) -> tuple[str, Path | None]:
    directory = rendered_icon_directory(cache_dir, device_key)
    token = _package_prefix(package)
    candidates = sorted(directory.glob(token + "*.png"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        return "", None
    path = candidates[0]
    return path.stem.removeprefix(token), path


def write_rendered_icon(cache_dir: Path, device_key: str, record: DeviceAppRecord) -> Path | None:
    if not record.png:
        _revision, existing = cached_rendered_icon(cache_dir, device_key, record.package)
        return existing
    directory = rendered_icon_directory(cache_dir, device_key)
    directory.mkdir(parents=True, exist_ok=True)
    prefix = _package_prefix(record.package)
    destination = directory / f"{prefix}{record.revision}.png"
    _replace_atomically(destination, record.png)
    for stale in directory.glob(prefix + "*.png"):
        if stale != destination:
            with contextlib.suppress(OSError):
                stale.unlink()
    return destination


def load_cached_app_records(cache_dir: Path, device_key: str) -> dict[str, DeviceAppRecord]:
    index = rendered_icon_directory(cache_dir, device_key) / "catalog.json"
    try:
        payload = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    records: dict[str, DeviceAppRecord] = {}
    if not isinstance(payload, dict):
        return records
    for package, value in payload.items():
        if not isinstance(package, str) or not isinstance(value, dict):
            continue
        label = value.get("label")
        revision = value.get("revision")
        if not isinstance(label, str) or not isinstance(revision, str) or not revision:
            continue
        records[package] = DeviceAppRecord(package, label or package, bool(value.get("is_system")), revision)
    return records


def write_cached_app_records(cache_dir: Path, device_key: str, records: dict[str, DeviceAppRecord]) -> None:
    directory = rendered_icon_directory(cache_dir, device_key)
    directory.mkdir(parents=True, exist_ok=True)
    index = directory / "catalog.json"
    payload = {
        package: {
            "label": record.label,
            "is_system": record.is_system,
            "revision": record.revision,
        }
        for package, record in sorted(records.items())
    }
    _replace_atomically(index, json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def helper_package_argument(package: str, cached_revision: str = "") -> str:
    # adb shell joins its argument list into a remote shell command. Avoid '|',
    # '&' and other metacharacters here; ':' is not valid in an Android package
    # name and is safe for the numeric revision suffix.
    return f"{package}:{cached_revision}" if cached_revision else package


def package_batches(packages: list[str], batch_size: int = 40) -> list[list[str]]:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    return [packages[index : index + batch_size] for index in range(0, len(packages), batch_size)]
