from __future__ import annotations

import re
from pathlib import Path

from .models import AppInfo, DeviceInfo


PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_$]+)+$")
_OVERLAY_LOOKUP_RE = re.compile(r"->\s*#([0-9A-Fa-f]{6,8})")
_BATTERY_LEVEL_RE = re.compile(r"^\s*level:\s*(\d+)", re.MULTILINE)
_BATTERY_STATUS_RE = re.compile(r"^\s*status:\s*(\d+)", re.MULTILINE)


def bundled_tools(project_root: Path) -> tuple[Path, Path]:
    """Return the bundled executables and fail early if the archive is incomplete."""
    directory = project_root / "scrcpy"
    adb = directory / "adb.exe"
    scrcpy = directory / "scrcpy.exe"
    missing = [str(path) for path in (adb, scrcpy) if not path.is_file()]
    if missing:
        raise FileNotFoundError("Не найдены bundled-инструменты: " + ", ".join(missing))
    return adb, scrcpy


def parse_adb_devices(output: str) -> list[DeviceInfo]:
    devices: list[DeviceInfo] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        columns = line.split()
        if len(columns) < 2:
            continue
        serial, state = columns[:2]
        attributes: dict[str, str] = {}
        for item in columns[2:]:
            if ":" in item:
                key, value = item.split(":", 1)
                attributes[key] = value.replace("_", " ")
        devices.append(
            DeviceInfo(
                serial=serial,
                state=state,
                model=attributes.get("model", ""),
                product=attributes.get("product", ""),
                device=attributes.get("device", ""),
                transport_id=attributes.get("transport_id", ""),
                usb_location=attributes.get("usb", ""),
            )
        )
    return devices


def parse_mdns_services(output: str) -> list[dict[str, str]]:
    """Parse both current adb tabular output and the older whitespace format."""
    services: list[dict[str, str]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.lower().startswith(("instance", "list of")):
            continue
        parts = re.split(r"\s+", line)
        if len(parts) < 3:
            continue
        service_type = next((part for part in parts if part.startswith("_adb-")), "")
        endpoint = next((part for part in parts if re.match(r"^.+:\d+$", part)), "")
        if service_type and endpoint:
            services.append({"instance": parts[0], "service": service_type, "endpoint": endpoint})
    return services


def parse_scrcpy_apps(output: str) -> list[AppInfo]:
    """Accept all list formats used by scrcpy releases around v3-v4."""
    applications: dict[str, AppInfo] = {}
    pending_label = ""
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.startswith(("INFO:", "WARN:", "ERROR:")):
            continue
        package = ""
        label = ""
        # scrcpy 4.1 server output: " * App name    com.example.app".
        # Very long labels are wrapped, with the package on the next line.
        bullet = re.match(r"^[*+-]\s+(?P<content>.+)$", line)
        if bullet:
            content = bullet.group("content").strip()
            match = re.match(r"^(?P<label>.+?)\s{2,}(?P<package>[\w.$]+)$", content)
            if match and PACKAGE_RE.match(match.group("package")):
                package, label = match.group("package"), match.group("label").strip()
                pending_label = ""
            else:
                pending_label = content
        elif pending_label and PACKAGE_RE.match(line):
            package, label = line, pending_label
            pending_label = ""
        # Current server output: Label [com.example.app]
        match = re.match(r"^(?P<label>.+?)\s*\[(?P<package>[\w.$]+)\]$", line) if not package else None
        if match is not None:
            package, label = match.group("package"), match.group("label").strip()
        # Common machine-readable variants: com.example.app: Label
        if not package and ":" in line:
            left, right = line.split(":", 1)
            if PACKAGE_RE.match(left.strip()):
                package, label = left.strip(), right.strip()
        # Fallback: com.example.app (Label) or just com.example.app
        if not package:
            match = re.match(r"^(?P<package>[\w.$]+)(?:\s*\((?P<label>.*)\))?$", line)
            if match:
                package, label = match.group("package"), (match.group("label") or "").strip()
        if package and PACKAGE_RE.match(package):
            applications[package] = AppInfo(package=package, label=label or package)
    return sorted(applications.values(), key=lambda app: (app.label.casefold(), app.package))


def parse_pm_packages(output: str) -> set[str]:
    return {
        line.removeprefix("package:").strip()
        for line in output.splitlines()
        if line.strip().startswith("package:")
    }


def parse_pm_package_versions(output: str) -> dict[str, str]:
    """Parse `pm list packages --show-versioncode` without depending on column order."""
    versions: dict[str, str] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line.startswith("package:"):
            continue
        parts = line.split()
        package = parts[0].removeprefix("package:")
        version = next((part.removeprefix("versionCode:") for part in parts[1:] if part.startswith("versionCode:")), "")
        if package:
            versions[package] = version
    return versions


def extract_package_path(output: str) -> str:
    """Prefer base.apk, then return the first accessible APK path."""
    paths = [line.strip().removeprefix("package:") for line in output.splitlines() if line.strip().startswith("package:")]
    return next((path for path in paths if path.endswith("/base.apk")), paths[0] if paths else "")


def parse_accent_color(output: str) -> str:
    """Extract the resolved color from
    `cmd overlay lookup android android:color/system_accent1_600`.

    `cmd overlay lookup` resolves the resource through whatever overlay is
    currently active - including a fabricated, file-less RRO - so it reflects
    the live Material You accent even when it was derived automatically from
    the wallpaper. (The `theme_customization_overlay_packages` secure setting
    only carries a hex color for manually-picked preset colors; when the color
    comes from the wallpaper it stays empty, which is why that approach was
    dropped in favor of this one.) Returns "" when the lookup failed (older
    Android without Material You, or the resource id doesn't exist there).
    """
    match = _OVERLAY_LOOKUP_RE.search(output)
    if not match:
        return ""
    hex_value = match.group(1)
    if len(hex_value) == 8:  # ARGB -> drop the alpha channel
        hex_value = hex_value[2:]
    return f"#{hex_value.upper()}" if re.fullmatch(r"[0-9A-Fa-f]{6}", hex_value) else ""


def parse_battery_status(output: str) -> tuple[int | None, bool]:
    """Parse `adb shell dumpsys battery` into (level percent, is charging).

    BatteryManager status codes: 1=unknown, 2=charging, 3=discharging,
    4=not charging, 5=full. Both 2 and 5 mean the device is on a charger.
    """
    level_match = _BATTERY_LEVEL_RE.search(output)
    level = int(level_match.group(1)) if level_match else None
    status_match = _BATTERY_STATUS_RE.search(output)
    charging = status_match is not None and status_match.group(1) in {"2", "5"}
    return level, charging
