from __future__ import annotations

from dataclasses import dataclass, field

from .i18n import t


# Device shapes the launcher can tell apart. An empty form factor means detection
# didn't reach a verdict (device offline, or an Android build that reports none of
# the signals) — treated as "an ordinary device" everywhere, so a failed probe never
# takes features away.
FORM_FACTOR_PHONE = "phone"
FORM_FACTOR_TABLET = "tablet"
FORM_FACTOR_TV = "tv"
FORM_FACTOR_WATCH = "watch"
FORM_FACTOR_AUTOMOTIVE = "automotive"
FORM_FACTOR_DESKTOP = "desktop"
FORM_FACTOR_VR = "vr"

# Form factors the launcher refuses to drive, because neither honours the
# `--new-display` + `--start-app` model this app is built around. Wear OS has no usable
# virtual display at all; Android TV boxes accept the flag but ignore it — the app opens
# on the television's own screen while the scrcpy window stays blank, which is worse
# than refusing, since it silently takes over the screen someone may be watching.
# Detection must be certain before a device lands here — see `detect_form_factor`, which
# only reaches these two through a form factor Android declares outright.
UNSUPPORTED_FORM_FACTORS = frozenset({FORM_FACTOR_WATCH, FORM_FACTOR_TV})


@dataclass(slots=True)
class DeviceInfo:
    serial: str
    state: str
    model: str = ""
    product: str = ""
    device: str = ""
    transport_id: str = ""
    usb_location: str = ""
    android_version: str = ""
    stable_id: str = ""
    battery_level: int | None = None
    battery_charging: bool = False
    form_factor: str = ""

    @property
    def profile_key(self) -> str:
        return self.stable_id or self.serial

    @property
    def hardware_key(self) -> str:
        """Identifies the device *model* — the product/device/model `adb devices -l` prints.

        Deliberately not `profile_key`: this is the key for caching what a *kind* of
        device is, not what one unit's owner configured. Form factor is a property of
        the model (every `r11btwifi` is a watch), so one probe answers for every unit
        of it, and unlike a serial this is known from the very first device listing —
        no round trip — and survives both a USB↔Wi-Fi switch and the random suffix in
        adb's mDNS serials (`adb-<id>-<random>._adb-tls-connect._tcp`).
        """
        parts = (self.product.strip(), self.device.strip(), self.model.strip())
        return "/".join(part for part in parts if part)

    @property
    def supports_launch(self) -> bool:
        """Whether the launcher's one-app-per-virtual-display model works here."""
        return self.form_factor not in UNSUPPORTED_FORM_FACTORS

    @property
    def transport(self) -> str:
        if self.usb_location:
            return "USB"
        serial = self.serial.casefold()
        if ":" in serial or "_adb-tls-connect._tcp" in serial or "_adb-tls-pairing._tcp" in serial:
            return "Wi-Fi"
        if serial.startswith("emulator-"):
            return t("device.transport_emulator")
        return "USB"


@dataclass(slots=True)
class AppInfo:
    package: str
    label: str
    is_system: bool = False
    icon_path: str = ""
    icon_attempted: bool = False


@dataclass(slots=True)
class LaunchProfile:
    device_key: str
    package: str
    name: str = ""
    force_stop: bool = False
    settings: dict[str, object] = field(default_factory=dict)
    extra_arguments: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SessionInfo:
    session_id: str
    device_serial: str
    package: str
    title: str
    command: list[str]
    # Stable device identity (DeviceInfo.profile_key) captured at launch, so a
    # session stays matched to its device after a USB/Wi-Fi reconnect changes the
    # serial.
    device_key: str = ""
    state: str = "running"
    log: str = ""
