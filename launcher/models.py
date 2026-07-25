from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


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

    @property
    def profile_key(self) -> str:
        return self.stable_id or self.serial

    @property
    def transport(self) -> str:
        if self.usb_location:
            return "USB"
        serial = self.serial.casefold()
        if ":" in serial or "_adb-tls-connect._tcp" in serial or "_adb-tls-pairing._tcp" in serial:
            return "Wi-Fi"
        if serial.startswith("emulator-"):
            return "Эмулятор"
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
    updated_at: datetime | None = None


@dataclass(slots=True)
class SessionInfo:
    session_id: str
    device_serial: str
    package: str
    title: str
    command: list[str]
    state: str = "running"
    log: str = ""
