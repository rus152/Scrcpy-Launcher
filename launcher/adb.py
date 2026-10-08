from __future__ import annotations

import re
from pathlib import Path

from .i18n import t
from .models import (
    FORM_FACTOR_AUTOMOTIVE,
    FORM_FACTOR_DESKTOP,
    FORM_FACTOR_PHONE,
    FORM_FACTOR_TABLET,
    FORM_FACTOR_TV,
    FORM_FACTOR_VR,
    FORM_FACTOR_WATCH,
    AppInfo,
    DeviceInfo,
)


PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_$]+)+$")
_OVERLAY_LOOKUP_RE = re.compile(r"->\s*#([0-9A-Fa-f]{6,8})")
_BATTERY_LEVEL_RE = re.compile(r"^\s*level:\s*(\d+)", re.MULTILINE)
_BATTERY_STATUS_RE = re.compile(r"^\s*status:\s*(\d+)", re.MULTILINE)
_PHYSICAL_SIZE_RE = re.compile(r"^\s*Physical size:\s*(\d+)x(\d+)", re.MULTILINE)
_PHYSICAL_DENSITY_RE = re.compile(r"^\s*Physical density:\s*(\d+)", re.MULTILINE)

# The `android.hardware.type.*` features are what PackageManager itself uses to tell
# form factors apart, so a device that declares one is that thing beyond doubt. Order
# matters only in that a device declaring two (television + leanback) resolves once.
_FEATURE_FORM_FACTORS: tuple[tuple[str, str], ...] = (
    ("android.hardware.type.watch", FORM_FACTOR_WATCH),
    ("android.hardware.type.television", FORM_FACTOR_TV),
    ("android.software.leanback", FORM_FACTOR_TV),
    ("android.hardware.type.automotive", FORM_FACTOR_AUTOMOTIVE),
    ("android.hardware.type.pc", FORM_FACTOR_DESKTOP),
    ("android.software.vr.mode", FORM_FACTOR_VR),
    ("android.hardware.vr.high_performance", FORM_FACTOR_VR),
)

# `ro.build.characteristics` is a comma-separated OEM tag list. It is a strong hint
# when it names a form factor, but far from universal — ordinary phones report
# "default" or "nosdcard" — so it is only consulted after the feature flags.
_CHARACTERISTIC_FORM_FACTORS: tuple[tuple[str, str], ...] = (
    ("watch", FORM_FACTOR_WATCH),
    ("tv", FORM_FACTOR_TV),
    ("automotive", FORM_FACTOR_AUTOMOTIVE),
    ("tablet", FORM_FACTOR_TABLET),
)

# Android's own phone/tablet divide: the width at which `sw600dp` resources kick in.
TABLET_SMALLEST_WIDTH_DP = 600

# Stamped onto every cached classification. Bump it whenever the rules in
# `detect_form_factor` change, so devices already classified by the old rules are
# re-probed instead of keeping a verdict this version would no longer reach.
FORM_FACTOR_DETECTOR_VERSION = 1


# Android 17 (aconfig flag `separate_timeouts`) puts every plain virtual display in the
# default display group, so an app window goes dark together with the phone's own
# screen. The patched server (android-helper/scrcpy-server) creates its display through
# a VirtualDeviceManager virtual device instead, whose display keeps its own power
# state; that needs a CDM "app streaming" association for the shell, created here once
# on devices with the flag on. A constant string, so `adb shell` gets no device data.
ENSURE_VIRTUAL_DEVICE_ASSOCIATION = (
    "dumpsys display | grep -q 'separate_timeouts: *true' || exit 0; "
    "cmd companiondevice list 0 | grep -q \"'com.android.shell'.*COMPANION_DEVICE_APP_STREAMING\" || "
    "cmd companiondevice associate 0 com.android.shell 02:00:00:00:5c:01 "
    "android.app.role.COMPANION_DEVICE_APP_STREAMING"
)


def bundled_tools(project_root: Path) -> tuple[Path, Path]:
    """Return the bundled executables and fail early if the archive is incomplete."""
    directory = project_root / "scrcpy"
    adb = directory / "adb.exe"
    scrcpy = directory / "scrcpy.exe"
    missing = [str(path) for path in (adb, scrcpy) if not path.is_file()]
    if missing:
        raise FileNotFoundError(t("adb.tools_missing", missing=", ".join(missing)))
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
        parts = line.split()
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


def _prefixed_values(output: str, prefix: str) -> list[str]:
    return [line.strip().removeprefix(prefix) for line in output.splitlines() if line.strip().startswith(prefix)]


def extract_package_path(output: str) -> str:
    """Prefer base.apk, then return the first accessible APK path."""
    paths = _prefixed_values(output, "package:")
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


def parse_pm_features(output: str) -> set[str]:
    """Collect the feature names from `pm list packages`-style `feature:` lines.

    Valued entries (`feature:reqGlEsVersion=0x30002`) are reduced to their name, and
    the trailing `feature:` lines some builds emit without a name are dropped.
    """
    features: set[str] = set()
    for value in _prefixed_values(output, "feature:"):
        name = value.split("=", 1)[0].strip()
        if name:
            features.add(name)
    return features


def parse_wm_size(output: str) -> tuple[int, int] | None:
    """Physical resolution from `wm size`, ignoring any `Override size:` line.

    The override is what the user or an app forced; the physical panel is what says
    which kind of device this is.
    """
    match = _PHYSICAL_SIZE_RE.search(output)
    if not match:
        return None
    width, height = int(match.group(1)), int(match.group(2))
    return (width, height) if width > 0 and height > 0 else None


def parse_wm_density(output: str) -> int | None:
    """Physical dpi from `wm density`, ignoring any `Override density:` line."""
    match = _PHYSICAL_DENSITY_RE.search(output)
    if not match:
        return None
    density = int(match.group(1))
    return density or None


def smallest_width_dp(size: tuple[int, int] | None, density: int | None) -> int | None:
    """The device's shortest edge in density-independent pixels.

    This is the number Android itself resolves `sw<N>dp` resource qualifiers against,
    which is what makes it a usable phone/tablet divide across wildly different panels.
    """
    if not size or not density:
        return None
    return round(min(size) * 160 / density)


def detect_form_factor(
    features: set[str],
    characteristics: str = "",
    size: tuple[int, int] | None = None,
    density: int | None = None,
) -> str:
    """Classify a device from its ADB-visible signals, best evidence first.

    Returns "" when the available signals don't settle it, which callers treat as an
    ordinary device — the screen-size heuristic below can only ever choose between
    phone and tablet, so the form factors that gate functionality (`watch` above all)
    are reached exclusively through signals Android states outright. Pass `size` and
    `density` to enable the phone/tablet split; without them a device that declares no
    form-factor feature and carries no matching build characteristic stays "".
    """
    for feature, form_factor in _FEATURE_FORM_FACTORS:
        if feature in features:
            return form_factor

    tokens = {token.strip().casefold() for token in characteristics.split(",")}
    for token, form_factor in _CHARACTERISTIC_FORM_FACTORS:
        if token in tokens:
            return form_factor

    width_dp = smallest_width_dp(size, density)
    if width_dp is None:
        return ""
    return FORM_FACTOR_TABLET if width_dp >= TABLET_SMALLEST_WIDTH_DP else FORM_FACTOR_PHONE


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
