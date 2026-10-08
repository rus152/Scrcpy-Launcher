from __future__ import annotations

import shlex
from dataclasses import dataclass

from .i18n import t
from .models import UNSUPPORTED_FORM_FACTORS


@dataclass(frozen=True, slots=True)
class OptionSpec:
    key: str
    flag: str
    group: str  # translation key, e.g. "group.window" — resolve with i18n.t()
    label: str  # translation key, e.g. "opt.always_on_top" — resolve with i18n.t()
    kind: str = "text"  # bool, text, int, choice
    choices: tuple[str, ...] = ()
    placeholder: str = ""
    windows_supported: bool = True
    # Optional subheading *within* the tab, as a translation key. Empty means the field
    # sits in the tab's unlabelled lead section, which is rendered first.
    section: str = ""


def _option(key: str, flag: str, group: str, label: str, kind: str = "text", choices: tuple[str, ...] = (), placeholder: str = "", windows_supported: bool = True, section: str = "") -> OptionSpec:
    return OptionSpec(key, flag, group, label, kind, choices, placeholder, windows_supported, section)


# Every long-form client flag exposed by bundled scrcpy 5.0.  Server-query options
# (--list-*, --help and --version) are commands, not launch settings.
OPTION_SPECS: tuple[OptionSpec, ...] = (
    _option("always_on_top", "--always-on-top", "group.window", "opt.always_on_top", "bool"),
    _option("angle", "--angle", "group.window", "opt.angle", "int"),
    _option("window_title", "--window-title", "group.window", "opt.window_title"),
    _option("window_x", "--window-x", "group.window", "opt.window_x", "int"),
    _option("window_y", "--window-y", "group.window", "opt.window_y", "int"),
    _option("window_width", "--window-width", "group.window", "opt.window_width", "int"),
    _option("window_height", "--window-height", "group.window", "opt.window_height", "int"),
    _option("window_borderless", "--window-borderless", "group.window", "opt.window_borderless", "bool"),
    _option("fullscreen", "--fullscreen", "group.window", "opt.fullscreen", "bool"),
    _option("no_window_aspect_ratio_lock", "--no-window-aspect-ratio-lock", "group.window", "opt.no_window_aspect_ratio_lock", "bool"),
    _option("background_color", "--background-color", "group.window", "opt.background_color", placeholder="#222"),
    _option("render_driver", "--render-driver", "group.window", "opt.render_driver", "choice", ("direct3d", "opengl", "opengles2", "opengles", "software")),
    _option("render_fit", "--render-fit", "group.window", "opt.render_fit", "choice", ("letterbox", "stretched", "unscaled")),
    _option("disable_screensaver", "--disable-screensaver", "group.window", "opt.disable_screensaver", "bool"),
    _option("no_mipmaps", "--no-mipmaps", "group.window", "opt.no_mipmaps", "bool"),
    _option("new_display", "--new-display", "group.display", "opt.new_display"),
    _option("flex_display", "--flex-display", "group.display", "opt.flex_display", "bool", section="group.virtual_display"),
    _option("no_vd_destroy_content", "--no-vd-destroy-content", "group.display", "opt.no_vd_destroy_content", "bool", section="group.virtual_display"),
    _option("no_vd_system_decorations", "--no-vd-system-decorations", "group.display", "opt.no_vd_system_decorations", "bool", section="group.virtual_display"),
    _option("display_ime_policy", "--display-ime-policy", "group.display", "opt.display_ime_policy", "choice", ("local", "fallback", "hide"), section="group.virtual_display"),
    _option("display_id", "--display-id", "group.screen", "opt.display_id", "int"),
    _option("display_orientation", "--display-orientation", "group.screen", "opt.display_orientation", "choice", ("0", "90", "180", "270", "flip0", "flip90", "flip180", "flip270")),
    _option("capture_orientation", "--capture-orientation", "group.screen", "opt.capture_orientation", "choice", ("0", "90", "180", "270", "flip0", "flip90", "flip180", "flip270", "@", "@90", "@180", "@270")),
    _option("record_orientation", "--record-orientation", "group.screen", "opt.record_orientation", "choice", ("0", "90", "180", "270")),
    _option("orientation", "--orientation", "group.screen", "opt.orientation", "choice", ("0", "90", "180", "270", "flip0", "flip90", "flip180", "flip270")),
    _option("crop", "--crop", "group.screen", "opt.crop"),
    _option("max_size", "--max-size", "group.display", "opt.max_size", "int"),
    _option("max_fps", "--max-fps", "group.display", "opt.max_fps", "int"),
    _option("video_bit_rate", "--video-bit-rate", "group.display", "opt.video_bit_rate", placeholder="8M"),
    _option("video_codec", "--video-codec", "group.display", "opt.video_codec", "choice", ("h264", "h265", "av1", "vp8", "vp9")),
    _option("video_encoder", "--video-encoder", "group.video", "opt.video_encoder"),
    _option("video_codec_options", "--video-codec-options", "group.video", "opt.video_codec_options"),
    _option("video_buffer", "--video-buffer", "group.video", "opt.video_buffer", "int"),
    # vaapi/videotoolbox are Linux/macOS-only decoders, so they're not offered here.
    _option("hwdec", "--hwdec", "group.video", "opt.hwdec", "choice", ("auto", "disabled", "d3d11va")),
    _option("video_source", "--video-source", "group.video", "opt.video_source", "choice", ("display", "camera")),
    _option("no_video", "--no-video", "group.video", "opt.no_video", "bool"),
    _option("no_video_playback", "--no-video-playback", "group.video", "opt.no_video_playback", "bool"),
    _option("no_playback", "--no-playback", "group.video", "opt.no_playback", "bool"),
    _option("no_window", "--no-window", "group.window", "opt.no_window", "bool"),
    _option("ignore_video_encoder_constraints", "--ignore-video-encoder-constraints", "group.video", "opt.ignore_video_encoder_constraints", "bool"),
    _option("min_size_alignment", "--min-size-alignment", "group.video", "opt.min_size_alignment", "choice", ("1", "2", "4", "8", "16")),
    _option("no_downsize_on_error", "--no-downsize-on-error", "group.video", "opt.no_downsize_on_error", "bool"),
    _option("audio_bit_rate", "--audio-bit-rate", "group.audio", "opt.audio_bit_rate", placeholder="128K"),
    _option("audio_codec", "--audio-codec", "group.audio", "opt.audio_codec", "choice", ("opus", "aac", "flac", "raw")),
    _option("audio_encoder", "--audio-encoder", "group.audio", "opt.audio_encoder"),
    _option("audio_codec_options", "--audio-codec-options", "group.audio", "opt.audio_codec_options"),
    _option("audio_source", "--audio-source", "group.audio", "opt.audio_source", "choice", ("output", "playback", "mic", "mic-unprocessed", "mic-camcorder", "mic-voice-recognition", "mic-voice-communication", "voice-call", "voice-call-uplink", "voice-call-downlink", "voice-performance")),
    _option("audio_buffer", "--audio-buffer", "group.audio", "opt.audio_buffer", "int"),
    _option("audio_output_buffer", "--audio-output-buffer", "group.audio", "opt.audio_output_buffer", "int"),
    _option("audio_dup", "--audio-dup", "group.audio", "opt.audio_dup", "bool"),
    _option("no_audio", "--no-audio", "group.audio", "opt.no_audio", "bool"),
    _option("no_audio_playback", "--no-audio-playback", "group.audio", "opt.no_audio_playback", "bool"),
    _option("require_audio", "--require-audio", "group.audio", "opt.require_audio", "bool"),
    _option("keyboard", "--keyboard", "group.control", "opt.keyboard", "choice", ("disabled", "sdk", "uhid", "aoa")),
    _option("mouse", "--mouse", "group.control", "opt.mouse", "choice", ("disabled", "sdk", "uhid", "aoa")),
    _option("gamepad", "--gamepad", "group.control", "opt.gamepad", "choice", ("disabled", "uhid", "aoa")),
    _option("mouse_bind", "--mouse-bind", "group.control", "opt.mouse_bind"),
    _option("no_control", "--no-control", "group.control", "opt.no_control", "bool"),
    _option("no_key_repeat", "--no-key-repeat", "group.control", "opt.no_key_repeat", "bool"),
    _option("raw_key_events", "--raw-key-events", "group.control", "opt.raw_key_events", "bool"),
    _option("prefer_text", "--prefer-text", "group.control", "opt.prefer_text", "bool"),
    _option("legacy_paste", "--legacy-paste", "group.control", "opt.legacy_paste", "bool"),
    _option("no_clipboard_autosync", "--no-clipboard-autosync", "group.control", "opt.no_clipboard_autosync", "bool"),
    _option("no_mouse_hover", "--no-mouse-hover", "group.control", "opt.no_mouse_hover", "bool"),
    _option("shortcut_mod", "--shortcut-mod", "group.control", "opt.shortcut_mod", placeholder="lalt,lsuper"),
    _option("show_touches", "--show-touches", "group.device", "opt.show_touches", "bool"),
    _option("stay_awake", "--stay-awake", "group.device", "opt.stay_awake", "bool"),
    _option("keep_active", "--keep-active", "group.device", "opt.keep_active", "bool"),
    _option("turn_screen_off", "--turn-screen-off", "group.device", "opt.turn_screen_off", "bool"),
    _option("power_off_on_close", "--power-off-on-close", "group.device", "opt.power_off_on_close", "bool"),
    _option("no_power_on", "--no-power-on", "group.device", "opt.no_power_on", "bool"),
    _option("screen_off_timeout", "--screen-off-timeout", "group.device", "opt.screen_off_timeout", "int"),
    _option("time_limit", "--time-limit", "group.misc", "opt.time_limit", "int"),
    _option("port", "--port", "group.network", "opt.port"),
    _option("force_adb_forward", "--force-adb-forward", "group.network", "opt.force_adb_forward", "bool"),
    _option("tunnel_host", "--tunnel-host", "group.network", "opt.tunnel_host"),
    _option("tunnel_port", "--tunnel-port", "group.network", "opt.tunnel_port", "int"),
    _option("push_target", "--push-target", "group.misc", "opt.push_target"),
    _option("record", "--record", "group.recording", "opt.record"),
    _option("record_format", "--record-format", "group.recording", "opt.record_format", "choice", ("mp4", "mkv", "m4a", "mka", "opus", "aac", "flac", "wav")),
    _option("print_fps", "--print-fps", "group.misc", "opt.print_fps", "bool"),
    _option("verbosity", "--verbosity", "group.misc", "opt.verbosity", "choice", ("verbose", "debug", "info", "warn", "error")),
    _option("pause_on_exit", "--pause-on-exit", "group.misc", "opt.pause_on_exit", "choice", ("true", "false", "if-error")),
    _option("no_cleanup", "--no-cleanup", "group.misc", "opt.no_cleanup", "bool"),
    _option("no_terminal_title", "--no-terminal-title", "group.misc", "opt.no_terminal_title", "bool"),
    _option("kill_adb_on_close", "--kill-adb-on-close", "group.restrictions", "opt.kill_adb_on_close", "bool", windows_supported=False),
    _option("camera_id", "--camera-id", "group.camera", "opt.camera_id"),
    _option("camera_facing", "--camera-facing", "group.camera", "opt.camera_facing", "choice", ("front", "back", "external")),
    _option("camera_size", "--camera-size", "group.camera", "opt.camera_size"),
    _option("camera_ar", "--camera-ar", "group.camera", "opt.camera_ar"),
    _option("camera_fps", "--camera-fps", "group.camera", "opt.camera_fps", "int"),
    _option("camera_high_speed", "--camera-high-speed", "group.camera", "opt.camera_high_speed", "bool"),
    _option("camera_torch", "--camera-torch", "group.camera", "opt.camera_torch", "bool"),
    _option("camera_zoom", "--camera-zoom", "group.camera", "opt.camera_zoom"),
    _option("otg", "--otg", "group.otg", "opt.otg", "bool"),
    _option("v4l2_sink", "--v4l2-sink", "group.linux", "opt.v4l2_sink", windows_supported=False),
    _option("v4l2_buffer", "--v4l2-buffer", "group.linux", "opt.v4l2_buffer", "int", windows_supported=False),
)

RESERVED_FLAGS = {"--serial", "-s", "--new-display", "--start-app"}

# scrcpy exposes ~96 flags, but launching one app in its own virtual display only needs
# a handful of them day to day. These are the ones the settings dialog puts on its main
# tabs; everything else is bucketed into a single "Advanced" tab (still grouped by the
# `group` on each spec, which becomes a subheading there). Nothing is hidden or removed
# — this only decides where a field is rendered, so `build_scrcpy_arguments` is
# unaffected. Moving an option in or out of the main tabs is a one-line edit here.
ESSENTIAL_KEYS: frozenset[str] = frozenset(
    {
        # The virtual display this launcher is built around — its geometry and
        # behaviour — plus the knobs that decide how it looks and what it costs.
        "new_display",
        "flex_display",
        "no_vd_destroy_content",
        "no_vd_system_decorations",
        "display_ime_policy",
        "max_size",
        "max_fps",
        "video_bit_rate",
        "video_codec",
        # Where and how the scrcpy window shows up on the desktop.
        "window_title",
        "window_width",
        "window_height",
        "fullscreen",
        "always_on_top",
        "window_borderless",
        # "Is there sound, and how good is it" — the rest of the audio stack is expert.
        "no_audio",
        "audio_bit_rate",
        "audio_codec",
        # Whether the window is interactive at all, and what drives it.
        "no_control",
        "keyboard",
        "mouse",
        # Phone-side behaviour people actually toggle per app.
        "show_touches",
        "stay_awake",
        "turn_screen_off",
        "power_off_on_close",
    }
)

# Tab order for the main (non-advanced) groups; the Advanced tab is always appended last.
MAIN_GROUP_ORDER: tuple[str, ...] = (
    "group.display",
    "group.window",
    "group.audio",
    "group.control",
    "group.device",
)
ADVANCED_GROUP = "group.advanced"


def is_essential(spec: OptionSpec) -> bool:
    """Whether `spec` belongs on a main tab rather than in the Advanced bucket."""
    return spec.key in ESSENTIAL_KEYS


def parse_extra_arguments(text: str) -> list[str]:
    arguments: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            # The subprocess is launched with an argument list (no shell), so quotes
            # only group a single argument; they must not reach scrcpy literally.
            arguments.extend(shlex.split(line, posix=True))
    return arguments


def build_profile_settings(raw_values: dict[str, object], inherited: set[str]) -> dict[str, object]:
    """Resolve a per-app profile's stored settings from a settings-dialog snapshot.

    A key marked `inherited` takes its value from the global settings instead, so it
    is dropped here rather than stored — including an explicit `False` override of a
    boolean, which must survive when not inherited.
    """
    settings: dict[str, object] = {}
    for spec in OPTION_SPECS:
        if not spec.windows_supported or spec.key in inherited or spec.key not in raw_values:
            continue
        settings[spec.key] = raw_values[spec.key]
    return settings


# Seeded into the global settings on first run (see LauncherApp._apply_default_global_settings).
DEFAULT_GLOBAL_SETTINGS: dict[str, object] = {}
# Bumping this re-seeds any newly added default on existing installations.
GLOBAL_DEFAULTS_VERSION = 2
GLOBAL_DEFAULTS_APPLIED_KEY = "global_defaults_applied"


def build_global_settings(raw_values: dict[str, object]) -> dict[str, object]:
    """Resolve the global settings dialog's snapshot, dropping empty/false values."""
    settings: dict[str, object] = {}
    for spec in OPTION_SPECS:
        if not spec.windows_supported:
            continue
        value = raw_values.get(spec.key)
        if value not in (False, "", None):
            settings[spec.key] = value
    return settings


def validate_launch_settings(
    settings: dict[str, object],
    extra_arguments: list[str],
    form_factor: str = "",
) -> list[str]:
    problems: list[str] = []
    # Refusing the device itself comes first: on a form factor that cannot run a
    # launched app at all, the rest of the report would only be noise. An unknown
    # form factor ("") is never in the set, so a device whose probe failed still
    # launches — the block needs positive proof, not absence of proof.
    if form_factor in UNSUPPORTED_FORM_FACTORS:
        problems.append(t("err.form_factor_unsupported"))
    for argument in extra_arguments:
        flag = argument.split("=", 1)[0]
        if flag in RESERVED_FLAGS:
            problems.append(t("err.reserved_flag", flag=flag))
    if settings.get("video_source") == "camera":
        problems.append(t("err.video_source_camera"))
    if settings.get("otg"):
        problems.append(t("err.otg_unsupported"))
    if settings.get("no_video") or settings.get("no_window") or settings.get("no_playback"):
        problems.append(t("err.video_window_required"))
    return problems


def build_scrcpy_arguments(
    serial: str,
    package: str,
    settings: dict[str, object],
    *,
    force_stop: bool = False,
    extra_arguments: list[str] | None = None,
    form_factor: str = "",
) -> list[str]:
    """Build a shell-free launch command for a package in a new virtual display."""
    extras = extra_arguments or []
    problems = validate_launch_settings(settings, extras, form_factor)
    if problems:
        raise ValueError("\n".join(problems))
    arguments = ["--serial", serial]
    new_display = str(settings.get("new_display", "")).strip()
    arguments.append("--new-display" if not new_display else f"--new-display={new_display}")
    for spec in OPTION_SPECS:
        if spec.key in {"new_display", "no_window"} or not spec.windows_supported:
            continue
        value = settings.get(spec.key)
        if spec.kind == "bool":
            if value:
                arguments.append(spec.flag)
        elif value not in (None, ""):
            arguments.append(f"{spec.flag}={value}")
    start_app = f"+{package}" if force_stop else package
    arguments.append(f"--start-app={start_app}")
    arguments.extend(extras)
    return arguments
