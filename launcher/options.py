from __future__ import annotations

import shlex
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OptionSpec:
    key: str
    flag: str
    group: str
    label: str
    kind: str = "text"  # bool, text, int, choice
    choices: tuple[str, ...] = ()
    placeholder: str = ""
    windows_supported: bool = True


def _option(key: str, flag: str, group: str, label: str, kind: str = "text", choices: tuple[str, ...] = (), placeholder: str = "", windows_supported: bool = True) -> OptionSpec:
    return OptionSpec(key, flag, group, label, kind, choices, placeholder, windows_supported)


# Every long-form client flag exposed by bundled scrcpy 4.1.  Server-query options
# (--list-*, --help and --version) are commands, not launch settings.
OPTION_SPECS: tuple[OptionSpec, ...] = (
    _option("always_on_top", "--always-on-top", "Окно", "Всегда поверх", "bool"),
    _option("angle", "--angle", "Окно", "Поворот содержимого (°)", "int"),
    _option("window_title", "--window-title", "Окно", "Заголовок окна"),
    _option("window_x", "--window-x", "Окно", "Позиция X", "int"),
    _option("window_y", "--window-y", "Окно", "Позиция Y", "int"),
    _option("window_width", "--window-width", "Окно", "Ширина окна", "int"),
    _option("window_height", "--window-height", "Окно", "Высота окна", "int"),
    _option("window_borderless", "--window-borderless", "Окно", "Окно без рамки", "bool"),
    _option("fullscreen", "--fullscreen", "Окно", "Полноэкранный режим", "bool"),
    _option("no_window_aspect_ratio_lock", "--no-window-aspect-ratio-lock", "Окно", "Не фиксировать пропорции окна", "bool"),
    _option("background_color", "--background-color", "Окно", "Цвет фона", placeholder="#222"),
    _option("render_driver", "--render-driver", "Окно", "Драйвер рендеринга", "choice", ("direct3d", "opengl", "opengles2", "opengles", "software")),
    _option("render_fit", "--render-fit", "Окно", "Подгонка изображения", "choice", ("letterbox", "stretched", "unscaled")),
    _option("disable_screensaver", "--disable-screensaver", "Окно", "Отключить заставку ПК", "bool"),
    _option("no_mipmaps", "--no-mipmaps", "Окно", "Отключить mipmaps", "bool"),
    _option("new_display", "--new-display", "Виртуальный дисплей", "Размер/DPI (например 1920x1080/420)"),
    _option("flex_display", "--flex-display", "Виртуальный дисплей", "Гибкий размер дисплея", "bool"),
    _option("no_vd_destroy_content", "--no-vd-destroy-content", "Виртуальный дисплей", "Переместить приложение на основной экран при закрытии", "bool"),
    _option("no_vd_system_decorations", "--no-vd-system-decorations", "Виртуальный дисплей", "Без системных декораций", "bool"),
    _option("display_ime_policy", "--display-ime-policy", "Виртуальный дисплей", "Политика экранной клавиатуры", "choice", ("local", "fallback", "hide")),
    _option("display_id", "--display-id", "Экран", "ID экрана", "int"),
    _option("display_orientation", "--display-orientation", "Экран", "Ориентация экрана", "choice", ("0", "90", "180", "270", "flip0", "flip90", "flip180", "flip270")),
    _option("capture_orientation", "--capture-orientation", "Экран", "Ориентация захвата", "choice", ("0", "90", "180", "270", "flip0", "flip90", "flip180", "flip270", "@", "@90", "@180", "@270")),
    _option("record_orientation", "--record-orientation", "Экран", "Ориентация записи", "choice", ("0", "90", "180", "270")),
    _option("orientation", "--orientation", "Экран", "Ориентация экрана и записи", "choice", ("0", "90", "180", "270", "flip0", "flip90", "flip180", "flip270")),
    _option("crop", "--crop", "Экран", "Обрезка width:height:x:y"),
    _option("max_size", "--max-size", "Видео", "Максимальная сторона", "int"),
    _option("max_fps", "--max-fps", "Видео", "Максимальный FPS", "int"),
    _option("video_bit_rate", "--video-bit-rate", "Видео", "Битрейт видео", placeholder="8M"),
    _option("video_codec", "--video-codec", "Видео", "Видеокодек", "choice", ("h264", "h265", "av1", "vp8", "vp9")),
    _option("video_encoder", "--video-encoder", "Видео", "Видеокодер"),
    _option("video_codec_options", "--video-codec-options", "Видео", "Опции видеокодера"),
    _option("video_buffer", "--video-buffer", "Видео", "Буфер видео (мс)", "int"),
    _option("video_source", "--video-source", "Видео", "Источник видео", "choice", ("display", "camera")),
    _option("no_video", "--no-video", "Видео", "Отключить видео", "bool"),
    _option("no_video_playback", "--no-video-playback", "Видео", "Не показывать видео на ПК", "bool"),
    _option("no_playback", "--no-playback", "Видео", "Не показывать видео и аудио на ПК", "bool"),
    _option("no_window", "--no-window", "Окно", "Не создавать окно scrcpy", "bool"),
    _option("ignore_video_encoder_constraints", "--ignore-video-encoder-constraints", "Видео", "Игнорировать ограничения кодера", "bool"),
    _option("min_size_alignment", "--min-size-alignment", "Видео", "Выравнивание размера", "choice", ("1", "2", "4", "8", "16")),
    _option("no_downsize_on_error", "--no-downsize-on-error", "Видео", "Не уменьшать разрешение при ошибке", "bool"),
    _option("audio_bit_rate", "--audio-bit-rate", "Аудио", "Битрейт аудио", placeholder="128K"),
    _option("audio_codec", "--audio-codec", "Аудио", "Аудиокодек", "choice", ("opus", "aac", "flac", "raw")),
    _option("audio_encoder", "--audio-encoder", "Аудио", "Аудиокодер"),
    _option("audio_codec_options", "--audio-codec-options", "Аудио", "Опции аудиокодера"),
    _option("audio_source", "--audio-source", "Аудио", "Источник аудио", "choice", ("output", "playback", "mic", "mic-unprocessed", "mic-camcorder", "mic-voice-recognition", "mic-voice-communication", "voice-call", "voice-call-uplink", "voice-call-downlink", "voice-performance")),
    _option("audio_buffer", "--audio-buffer", "Аудио", "Буфер захвата (мс)", "int"),
    _option("audio_output_buffer", "--audio-output-buffer", "Аудио", "Буфер вывода (мс)", "int"),
    _option("audio_dup", "--audio-dup", "Аудио", "Оставить воспроизведение на телефоне", "bool"),
    _option("no_audio", "--no-audio", "Аудио", "Отключить аудио", "bool"),
    _option("no_audio_playback", "--no-audio-playback", "Аудио", "Не проигрывать аудио на ПК", "bool"),
    _option("require_audio", "--require-audio", "Аудио", "Считать отсутствие аудио ошибкой", "bool"),
    _option("keyboard", "--keyboard", "Управление", "Клавиатура", "choice", ("disabled", "sdk", "uhid", "aoa")),
    _option("mouse", "--mouse", "Управление", "Мышь", "choice", ("disabled", "sdk", "uhid", "aoa")),
    _option("gamepad", "--gamepad", "Управление", "Геймпад", "choice", ("disabled", "uhid", "aoa")),
    _option("mouse_bind", "--mouse-bind", "Управление", "Привязки кнопок мыши"),
    _option("no_control", "--no-control", "Управление", "Только просмотр", "bool"),
    _option("no_key_repeat", "--no-key-repeat", "Управление", "Не повторять удерживаемые клавиши", "bool"),
    _option("raw_key_events", "--raw-key-events", "Управление", "Только raw key events", "bool"),
    _option("prefer_text", "--prefer-text", "Управление", "Предпочитать текстовые события", "bool"),
    _option("legacy_paste", "--legacy-paste", "Управление", "Старый режим вставки", "bool"),
    _option("no_clipboard_autosync", "--no-clipboard-autosync", "Управление", "Отключить синхронизацию буфера", "bool"),
    _option("no_mouse_hover", "--no-mouse-hover", "Управление", "Не передавать наведение мыши", "bool"),
    _option("shortcut_mod", "--shortcut-mod", "Управление", "Модификатор сочетаний scrcpy", placeholder="lalt,lsuper"),
    _option("show_touches", "--show-touches", "Устройство", "Показывать касания", "bool"),
    _option("stay_awake", "--stay-awake", "Устройство", "Не давать устройству уснуть", "bool"),
    _option("keep_active", "--keep-active", "Устройство", "Поддерживать экран активным", "bool"),
    _option("turn_screen_off", "--turn-screen-off", "Устройство", "Выключить экран при запуске", "bool"),
    _option("power_off_on_close", "--power-off-on-close", "Устройство", "Выключить экран при закрытии", "bool"),
    _option("no_power_on", "--no-power-on", "Устройство", "Не включать экран при запуске", "bool"),
    _option("screen_off_timeout", "--screen-off-timeout", "Устройство", "Тайм-аут выключения (с)", "int"),
    _option("time_limit", "--time-limit", "Дополнительно", "Лимит работы (с)", "int"),
    _option("port", "--port", "Сеть", "Порты сервера scrcpy"),
    _option("force_adb_forward", "--force-adb-forward", "Сеть", "Принудительный adb forward", "bool"),
    _option("tunnel_host", "--tunnel-host", "Сеть", "Хост туннеля"),
    _option("tunnel_port", "--tunnel-port", "Сеть", "Порт туннеля", "int"),
    _option("push_target", "--push-target", "Дополнительно", "Папка для перетаскивания файлов"),
    _option("record", "--record", "Запись", "Файл записи"),
    _option("record_format", "--record-format", "Запись", "Формат записи", "choice", ("mp4", "mkv", "m4a", "mka", "opus", "aac", "flac", "wav")),
    _option("print_fps", "--print-fps", "Дополнительно", "Печатать FPS", "bool"),
    _option("verbosity", "--verbosity", "Дополнительно", "Подробность журнала", "choice", ("verbose", "debug", "info", "warn", "error")),
    _option("pause_on_exit", "--pause-on-exit", "Дополнительно", "Пауза при выходе", "choice", ("true", "false", "if-error")),
    _option("no_cleanup", "--no-cleanup", "Дополнительно", "Не очищать состояние устройства", "bool"),
    _option("no_terminal_title", "--no-terminal-title", "Дополнительно", "Не менять заголовок терминала", "bool"),
    _option("kill_adb_on_close", "--kill-adb-on-close", "Ограничения", "Закрыть общий ADB после сессии", "bool", windows_supported=False),
    _option("camera_id", "--camera-id", "Камера", "ID камеры"),
    _option("camera_facing", "--camera-facing", "Камера", "Сторона камеры", "choice", ("front", "back", "external")),
    _option("camera_size", "--camera-size", "Камера", "Размер камеры"),
    _option("camera_ar", "--camera-ar", "Камера", "Соотношение сторон"),
    _option("camera_fps", "--camera-fps", "Камера", "FPS камеры", "int"),
    _option("camera_high_speed", "--camera-high-speed", "Камера", "Высокая скорость камеры", "bool"),
    _option("camera_torch", "--camera-torch", "Камера", "Фонарик камеры", "bool"),
    _option("camera_zoom", "--camera-zoom", "Камера", "Зум камеры"),
    _option("otg", "--otg", "OTG", "Режим USB OTG", "bool"),
    _option("v4l2_sink", "--v4l2-sink", "Linux", "V4L2-устройство", windows_supported=False),
    _option("v4l2_buffer", "--v4l2-buffer", "Linux", "Буфер V4L2", "int", windows_supported=False),
)

SPEC_BY_KEY = {spec.key: spec for spec in OPTION_SPECS}
RESERVED_FLAGS = {"--serial", "-s", "--new-display", "--start-app"}


def parse_extra_arguments(text: str) -> list[str]:
    arguments: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            # QProcess receives a list, so quotes only group a single argument;
            # they must not be forwarded to scrcpy as literal characters.
            arguments.extend(shlex.split(line, posix=True))
    return arguments


def validate_launch_settings(settings: dict[str, object], extra_arguments: list[str]) -> list[str]:
    problems: list[str] = []
    for argument in extra_arguments:
        flag = argument.split("=", 1)[0]
        if flag in RESERVED_FLAGS:
            problems.append(f"Аргумент {flag} задаётся программой автоматически.")
    if settings.get("video_source") == "camera":
        problems.append("Запуск Android-приложения требует источника видео «display», не «camera».")
    if settings.get("otg"):
        problems.append("Режим OTG не поддерживает зеркалирование и запуск Android-приложений.")
    if settings.get("no_video") or settings.get("no_window") or settings.get("no_playback"):
        problems.append("Для окна приложения нельзя отключать видео или окно scrcpy.")
    return problems


def build_scrcpy_arguments(
    serial: str,
    package: str,
    settings: dict[str, object],
    *,
    force_stop: bool = False,
    extra_arguments: list[str] | None = None,
) -> list[str]:
    """Build a shell-free launch command for a package in a new virtual display."""
    extras = extra_arguments or []
    problems = validate_launch_settings(settings, extras)
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
