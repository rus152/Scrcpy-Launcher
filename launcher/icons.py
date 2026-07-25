from __future__ import annotations

import hashlib
import shutil
import zipfile
from pathlib import Path

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPainter

# androguard emits thousands of low-level resource-parser diagnostics for many
# perfectly valid vendor APKs. Icons are best-effort, so keep that third-party
# diagnostic output out of the launcher console and use the fallback icon when
# a resource cannot be resolved.
logger.disable("androguard")

ICON_CACHE_VERSION = "normalized-png-v3"
NORMALIZED_ICON_SIZE = 144
NORMALIZED_ICON_PADDING = 12


def cache_filename(cache_dir: Path, device_key: str, package: str, remote_path: str) -> Path:
    token = hashlib.sha256(f"{ICON_CACHE_VERSION}\0{device_key}\0{package}\0{remote_path}".encode()).hexdigest()
    return cache_dir / "icons" / token


def extract_icon_from_apk(apk_path: Path, destination: Path) -> Path | None:
    """Extract a bitmap application icon using androguard, with safe fallbacks.

    Adaptive XML icons and vendor-protected APKs intentionally return None. The UI
    then keeps a generic package icon rather than failing catalogue loading.
    """
    try:
        from androguard.core.apk import APK

        apk = APK(str(apk_path))
        icon_name = apk.get_app_icon(max_dpi=640)
        source = _extract_bitmap_resource(apk, icon_name, destination)
        if not source:
            # Adaptive/vector icons normally use the Android helper. This path is
            # only a fallback for firmware that blocks app_process.
            source = _extract_common_bitmap_icon(apk_path, destination)
        return _normalize_icon(source, destination) if source else None
    except Exception:
        # Broken or vendor-specific resource tables are common. The fast ZIP
        # fallback still gives many such APKs a useful icon.
        source = _extract_common_bitmap_icon(apk_path, destination)
        return _normalize_icon(source, destination) if source else None
    finally:
        shutil.rmtree(apk_path.parent, ignore_errors=True)


def _extract_bitmap_resource(apk, icon_name: str | None, destination: Path) -> Path | None:  # type: ignore[no-untyped-def]
    """Write the exact bitmap resource referenced by the package manifest."""
    if not icon_name:
        return None
    suffix = Path(icon_name).suffix.lower()
    if suffix not in {".png", ".webp", ".jpg", ".jpeg", ".bmp"}:
        return None
    contents = apk.get_file(icon_name)
    if not contents:
        return None
    actual_destination = destination.with_suffix(suffix)
    actual_destination.parent.mkdir(parents=True, exist_ok=True)
    actual_destination.write_bytes(contents)
    return actual_destination


def _extract_common_bitmap_icon(apk_path: Path, destination: Path) -> Path | None:
    """Extract conventional launcher assets without parsing resources.arsc.

    Most Android packages place a PNG/WebP launcher bitmap under a mipmap or
    drawable path. Reading it directly from the APK ZIP is orders of magnitude
    faster than loading the full Android resource table with androguard.
    """
    try:
        with zipfile.ZipFile(apk_path) as archive:
            candidates: list[tuple[int, str]] = []
            for name in archive.namelist():
                lowered = name.lower()
                if not lowered.startswith("res/") or not lowered.endswith((".png", ".webp", ".jpg", ".jpeg", ".bmp")):
                    continue
                if any(token in lowered for token in ("foreground", "background", "monochrome", "notification", "splash")):
                    continue
                score = 0
                if "/mipmap" in lowered:
                    score += 70
                if "ic_launcher" in lowered:
                    score += 60
                elif "launcher" in lowered:
                    score += 45
                elif "/icon" in lowered or "_icon" in lowered:
                    score += 20
                if "xxxhdpi" in lowered:
                    score += 12
                elif "xxhdpi" in lowered:
                    score += 10
                elif "xhdpi" in lowered:
                    score += 8
                elif "hdpi" in lowered:
                    score += 6
                if score:
                    candidates.append((score, name))
            if not candidates:
                return None
            _score, icon_name = max(candidates, key=lambda item: item[0])
            suffix = Path(icon_name).suffix.lower()
            actual_destination = destination.with_suffix(suffix)
            actual_destination.parent.mkdir(parents=True, exist_ok=True)
            actual_destination.write_bytes(archive.read(icon_name))
            return actual_destination
    except (OSError, zipfile.BadZipFile, KeyError):
        return None


def _normalize_icon(source: Path, destination: Path) -> Path | None:
    """Convert every desktop fallback to the same padded 144×144 PNG."""
    image = QImage(str(source))
    if image.isNull():
        return None
    content_size = NORMALIZED_ICON_SIZE - NORMALIZED_ICON_PADDING * 2
    scaled = image.scaled(
        content_size,
        content_size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    canvas = QImage(NORMALIZED_ICON_SIZE, NORMALIZED_ICON_SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawImage((NORMALIZED_ICON_SIZE - scaled.width()) // 2, (NORMALIZED_ICON_SIZE - scaled.height()) // 2, scaled)
    painter.end()
    normalized = destination.with_suffix(".png")
    normalized.parent.mkdir(parents=True, exist_ok=True)
    if not canvas.save(str(normalized), "PNG"):
        return None
    if source != normalized:
        try:
            source.unlink()
        except OSError:
            pass
    return normalized
