from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Awaitable, Callable

import flet as ft

from .adb import (
    ENSURE_VIRTUAL_DEVICE_ASSOCIATION,
    bundled_tools,
    extract_package_path,
    parse_accent_color,
    parse_adb_devices,
    parse_battery_status,
    FORM_FACTOR_DETECTOR_VERSION,
    parse_mdns_services,
    parse_pm_features,
    parse_pm_package_versions,
    parse_pm_packages,
    parse_wm_density,
    parse_wm_size,
    detect_form_factor,
)
from .bootstrap import SCRCPY_VERSION, ask_language, ensure_scrcpy, missing_scrcpy_files, show_message
from .device_icons import (
    HELPER_MAIN_CLASS,
    HELPER_REMOTE_PATH,
    DeviceAppRecord,
    cached_rendered_icon,
    helper_package_argument,
    load_cached_app_records,
    package_batches,
    parse_device_app_records,
    write_cached_app_records,
    write_rendered_icon,
)
from .i18n import LANGUAGES, detect_system_language, get_language, set_language, t
from .icons import cache_filename, extract_icon_from_apk
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
    LaunchProfile,
    SessionInfo,
)
from .options import (
    ADVANCED_GROUP,
    DEFAULT_GLOBAL_SETTINGS,
    GLOBAL_DEFAULTS_APPLIED_KEY,
    GLOBAL_DEFAULTS_VERSION,
    MAIN_GROUP_ORDER,
    OPTION_SPECS,
    OptionSpec,
    is_essential,
    build_global_settings,
    build_profile_settings,
    build_scrcpy_arguments,
)
from .profiles import ProfileStore
from .session import NO_CONSOLE_WINDOW, ScrcpySession
from .window_focus import focus_process_window, process_windows


SEED_COLOR = "#4F86D9"
# One icon per detected device shape; anything undetected keeps the neutral one rather
# than being drawn as a phone it may not be.
FORM_FACTOR_ICONS: dict[str, str] = {
    FORM_FACTOR_PHONE: ft.Icons.SMARTPHONE,
    FORM_FACTOR_TABLET: ft.Icons.TABLET_ANDROID,
    FORM_FACTOR_TV: ft.Icons.TV,
    FORM_FACTOR_WATCH: ft.Icons.WATCH,
    FORM_FACTOR_AUTOMOTIVE: ft.Icons.DIRECTIONS_CAR,
    FORM_FACTOR_DESKTOP: ft.Icons.LAPTOP_CHROMEBOOK,
    FORM_FACTOR_VR: ft.Icons.VIEW_IN_AR,
}
UNKNOWN_FORM_FACTOR_ICON = ft.Icons.DEVICES_OTHER
# Why a given form factor is refused, so the bottom bar names the actual reason instead
# of a shrug. Keys must cover models.UNSUPPORTED_FORM_FACTORS; anything else falls back
# to the generic wording.
UNSUPPORTED_FORM_FACTOR_MESSAGES: dict[str, str] = {
    FORM_FACTOR_WATCH: "device.unsupported_watch",
    FORM_FACTOR_TV: "device.unsupported_tv",
}
# How long the "not supported" bar sits at the bottom after tapping such a device.
UNSUPPORTED_SNACK_DURATION = 4000
# Native window background while the app boots — Material 3's surface in either
# brightness. Only ever visible if the compositor paints the frame before the first
# Flutter frame lands, but it has to match the theme or that frame flashes inverted.
STARTUP_WINDOW_BGCOLOR_DARK = "#141218"
STARTUP_WINDOW_BGCOLOR_LIGHT = "#FEF7FF"
# Grace period between flushing the built UI and showing the window (see reveal_window).
WINDOW_REVEAL_SETTLE = 0.25
# Base M3 theme choices offered in the launcher settings. The *key* is what gets
# persisted, so a palette tweak never invalidates a stored preference.
THEME_SEED_COLORS: dict[str, str] = {
    "blue": SEED_COLOR,
    "violet": "#7C5CD6",
    "pink": "#D9558F",
    "red": "#D95555",
    "orange": "#DE8A3C",
    "green": "#48A45E",
    "teal": "#2F9E9E",
}
DEFAULT_THEME_SEED = "blue"
# Light/dark selection. "system" follows the OS setting, which is also the default.
THEME_MODES: dict[str, ft.ThemeMode] = {
    "system": ft.ThemeMode.SYSTEM,
    "light": ft.ThemeMode.LIGHT,
    "dark": ft.ThemeMode.DARK,
}
DEFAULT_THEME_MODE = "system"
DEFAULT_VIEW_MODE = "grid"
SHAPE_RADIUS = 20.0
PILL_RADIUS = 28.0
TILE_RADIUS = 16.0
ICON_SIZE = 44
APP_CARD_WIDTH = 150
APP_CARD_HEIGHT = 132
HOVER_COLOR_REVERT_DELAY = 0.3
CATALOG_SLIDE_DURATION_MS = 320
BATTERY_POLL_INTERVAL = 5.0
# A freshly launched scrcpy shows its window only after it has pushed and started the
# server on the device, which is seconds over Wi-Fi. These bound the wait for that
# window to appear before the launcher gives up on focusing it.
LAUNCH_WINDOW_TIMEOUT = 30.0
LAUNCH_WINDOW_POLL = 0.15
CATALOG_SLIDE_CURVE = ft.AnimationCurve.EASE_IN_OUT_CUBIC
CATALOG_ZOOM_OUT_DURATION_MS = 200
CATALOG_ZOOM_IN_DURATION_MS = 300
# Both halves of the transition zoom *up*: the outgoing view keeps growing past 1.0
# as it fades out, and the incoming one grows into place from below 1.0.
CATALOG_ZOOM_OUT_END_SCALE = 1.08
CATALOG_ZOOM_IN_START_SCALE = 0.88
CATALOG_ZOOM_OUT_CURVE = ft.AnimationCurve.EASE_IN_CUBIC
CATALOG_ZOOM_IN_CURVE = ft.AnimationCurve.EASE_OUT_CUBIC
# The invisible reset frame has to reach the client, and the client has to finish
# laying out whatever just got mounted, before the zoom-in is queued — otherwise the
# fade-in animation itself gets scheduled on a main thread that's still busy building
# the new content (measured: mounting ~66 fresh cards blocks the browser's render
# thread for ~280ms), and the animation clock runs late and invisible instead of
# visibly gradual. This has to outlast that mount cost, not just be a nonzero gap.
CATALOG_ZOOM_RESET_SETTLE = 0.4
# The app-bar actions slide in from the right as a fraction of their own width; the
# launcher-settings button mirrors that from the left, out of the title.
APPBAR_ACTIONS_SLIDE_OFFSET = 0.25
APPBAR_SETTINGS_SLIDE_OFFSET = -0.6
APPBAR_ACTIONS_SLIDE_DURATION_MS = 320
# The glow the launcher-settings button pulses when it points itself out, and how
# long it then stays put before sliding away again.
SETTINGS_HINT_DURATION = 3.0
SETTINGS_HINT_PULSE_MS = 260
SETTINGS_HINT_PULSES = 2


def _shape(radius: float = SHAPE_RADIUS) -> ft.RoundedRectangleBorder:
    return ft.RoundedRectangleBorder(radius=radius)


def build_theme(seed_color: str = SEED_COLOR) -> ft.Theme:
    """A Material 3 "expressive" theme: bold seed color, bigger rounded shapes."""
    return ft.Theme(
        color_scheme_seed=seed_color,
        use_material3=True,
        visual_density=ft.VisualDensity.COMFORTABLE,
        card_theme=ft.CardTheme(elevation=1, shape=_shape()),
        chip_theme=ft.ChipTheme(shape=_shape(PILL_RADIUS)),
        dialog_theme=ft.DialogTheme(shape=_shape()),
        bottom_sheet_theme=ft.BottomSheetTheme(shape=_shape()),
        filled_button_theme=ft.FilledButtonTheme(style=ft.ButtonStyle(shape=_shape(PILL_RADIUS))),
        floating_action_button_theme=ft.FloatingActionButtonTheme(shape=_shape(PILL_RADIUS)),
    )


def stored_theme_mode(store: ProfileStore) -> str:
    """The saved light/dark choice, validated. Shared by main_async, which needs it
    before LauncherApp exists, and LauncherApp itself."""
    stored = store.get_preference("theme_mode", DEFAULT_THEME_MODE)
    return stored if stored in THEME_MODES else DEFAULT_THEME_MODE


def stored_theme_seed(store: ProfileStore) -> str:
    """The saved base M3 colour key, validated."""
    stored = store.get_preference("theme_seed", DEFAULT_THEME_SEED)
    return stored if isinstance(stored, str) and stored in THEME_SEED_COLORS else DEFAULT_THEME_SEED


def stored_seed_color(store: ProfileStore) -> str:
    """The saved base M3 colour, resolved to a hex value."""
    return THEME_SEED_COLORS[stored_theme_seed(store)]


def system_prefers_dark() -> bool:
    """Whether Windows itself is set to a dark app theme.

    Flutter resolves ThemeMode.SYSTEM on its own; this is only for the native window's
    boot colour, which is painted before any Flutter frame exists. Anything unreadable
    falls back to dark, the app's own default look.
    """
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            return not winreg.QueryValueEx(key, "AppsUseLightTheme")[0]
    except (ImportError, OSError, ValueError):
        return True


def startup_window_bgcolor(theme_mode: str) -> str:
    if theme_mode == "system":
        return STARTUP_WINDOW_BGCOLOR_DARK if system_prefers_dark() else STARTUP_WINDOW_BGCOLOR_LIGHT
    return STARTUP_WINDOW_BGCOLOR_DARK if theme_mode == "dark" else STARTUP_WINDOW_BGCOLOR_LIGHT


def configure_window(page: ft.Page, theme_mode: str = DEFAULT_THEME_MODE) -> None:
    """Title and geometry of the app window, plus the state it starts hidden in.

    Runs before the client's first paint and exactly once. Sizing the window later —
    which is what LauncherApp used to do — resizes it in full view: the whole layout
    jumps and the floating action button visibly flies across to its new corner. The
    window also stays hidden (in the theme's own surface colour, in case the
    compositor paints it anyway) until reveal_window, so the default white frame
    never reaches the screen.

    Geometry is skipped in the web-server view (see CLAUDE.md): a browser has no
    window to size, and pinning it before the first layout leaves the app painting
    into a small fixed box instead of filling the viewport.
    """
    page.title = "Scrcpy Launcher"
    if page.web:
        return
    page.window.visible = False
    page.window.bgcolor = startup_window_bgcolor(theme_mode)
    page.window.width = 1180
    page.window.height = 760
    page.window.min_width = 860
    page.window.min_height = 560


async def reveal_window(page: ft.Page) -> None:
    """Show the window once there is something worth looking at. Idempotent, and a
    no-op in the browser, where the window was never hidden in the first place.

    The pending tree is flushed and given a moment to land before the window is told
    to show. Measured the hard way: bundled into the same update as the freshly built
    UI, the show never takes effect and the app runs on with an invisible window.
    """
    if page.web or page.window.visible:
        return
    page.update()
    await asyncio.sleep(WINDOW_REVEAL_SETTLE)
    # Placed while still hidden: the client leaves a hidden window in the top-left
    # corner, and centring it after the reveal would be a visible jump.
    await page.window.center()
    page.window.visible = True
    page.update()
    # A window that comes back from hidden is shown *behind* whatever holds focus,
    # so it has to ask for the foreground itself.
    await page.window.to_front()


def _data_directory() -> Path:
    base_directory = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    return base_directory / "ScrcpyLauncher" / "Scrcpy Launcher"


async def run_tool(program: Path, arguments: list[str]) -> tuple[int, str, str]:
    """Run one bundled ADB/scrcpy command without a shell and collect its output."""
    try:
        process = await asyncio.create_subprocess_exec(
            str(program),
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=NO_CONSOLE_WINDOW,
        )
    except OSError as error:
        return 1, "", str(error)
    stdout, stderr = await process.communicate()
    return process.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")


@dataclass(slots=True)
class AppCell:
    """The live controls of one app's catalogue entry.

    Held so an icon arriving or a session starting can be pushed into the existing
    controls, instead of rebuilding every card — a full rebuild visibly flashes the
    whole grid, and icons stream in continuously while a device loads.
    """

    icon_slot: ft.Container
    badge: ft.Icon
    label: ft.Text
    # Top-level control and which row owns it, so a re-sort can re-order the existing
    # controls in their parent instead of building new ones.
    root: ft.Control
    section: str
    launch_item: ft.PopupMenuItem | None = None
    stop_item: ft.PopupMenuItem | None = None
    surface: ft.Container | None = None
    tile: ft.ListTile | None = None
    subtitle: ft.Text | None = None
    launch_button: ft.Control | None = None
    show_button: ft.Control | None = None


def app_icon_control(icon_path: str, label: str, size: int = ICON_SIZE) -> ft.Control:
    """An app icon image, or a rounded initial-letter avatar when none is cached yet."""
    if icon_path:
        try:
            data = Path(icon_path).read_bytes()
        except OSError:
            data = b""
        if data:
            return ft.Image(src=data, width=size, height=size, fit=ft.BoxFit.CONTAIN, border_radius=10)
    initial = (label or "?").strip()[:1].upper() or "?"
    return ft.Container(
        width=size,
        height=size,
        border_radius=10,
        bgcolor=ft.Colors.PRIMARY_CONTAINER,
        alignment=ft.Alignment.CENTER,
        content=ft.Text(value=initial, color=ft.Colors.ON_PRIMARY_CONTAINER, weight=ft.FontWeight.BOLD, size=size * 0.42),
    )


def view_mode_segments() -> list[ft.Segment]:
    return [
        ft.Segment(value="grid", icon=ft.Icons.GRID_VIEW, label=t("catalog.view_grid")),
        ft.Segment(value="list", icon=ft.Icons.VIEW_LIST, label=t("catalog.view_list")),
    ]


class SettingsForm:
    """Builds the scrcpy option fields for both the global and per-app profile dialogs."""

    def __init__(self, settings: dict[str, object], extra_arguments: list[str], *, profile: bool) -> None:
        self._extra_arguments = list(extra_arguments)
        self.field_controls: dict[str, ft.Control] = {}
        self.inherit_switches: dict[str, ft.Switch] = {}
        # Main tabs hold only the everyday options; everything else lands in one
        # Advanced tab. Both are rendered as sectioned columns: a main tab subdivides by
        # each spec's `section`, the Advanced tab by the `group` the option came from,
        # so neither is a flat wall of fields. See ESSENTIAL_KEYS.
        main_rows: dict[str, dict[str, list[ft.Control]]] = {}
        advanced_rows: dict[str, list[ft.Control]] = {}

        for spec in OPTION_SPECS:
            row = self._build_option_row(spec, settings, profile=profile)
            if is_essential(spec):
                main_rows.setdefault(spec.group, {}).setdefault(spec.section, []).append(row)
            else:
                advanced_rows.setdefault(spec.group, []).append(row)

        group_order = [group for group in MAIN_GROUP_ORDER if group in main_rows]
        tab_labels = [t(group) for group in group_order]
        tab_contents = [self._build_sectioned_column(main_rows[group]) for group in group_order]
        if advanced_rows:
            tab_labels.append(t(ADVANCED_GROUP))
            tab_contents.append(
                self._build_sectioned_column({group: rows for group, rows in advanced_rows.items()})
            )

        tabs_bar = ft.TabBar(tabs=[ft.Tab(label=label) for label in tab_labels])
        tabs_view = ft.TabBarView(
            expand=True,
            controls=[ft.Container(content=content, padding=12) for content in tab_contents],
        )
        self.tabs = ft.Tabs(
            length=len(group_order),
            expand=True,
            content=ft.Column(controls=[tabs_bar, tabs_view], expand=True),
        )
        self.force_stop_switch = ft.Switch(
            label=t("settings.force_stop_label"),
            visible=profile,
        )

    def _build_option_row(self, spec: OptionSpec, settings: dict[str, object], *, profile: bool) -> ft.Control:
        """One label-plus-field row, identical wherever the option ends up being shown."""
        field_control = self._build_field(spec, settings)
        self.field_controls[spec.key] = field_control
        if not spec.windows_supported:
            field_control.disabled = True
        trailing: ft.Control = field_control
        if profile and spec.windows_supported:
            inherited = spec.key not in settings
            field_control.disabled = inherited
            inherit_switch = ft.Switch(
                label=t("settings.inherit_label"),
                value=inherited,
                tooltip=t("settings.inherit_tooltip"),
            )
            inherit_switch.on_change = self._make_inherit_handler(field_control, inherit_switch)
            self.inherit_switches[spec.key] = inherit_switch
            trailing = ft.Row(
                controls=[
                    ft.Container(content=field_control, expand=True, alignment=ft.Alignment.CENTER_RIGHT),
                    inherit_switch,
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        label_text = t(spec.label)
        if not spec.windows_supported:
            label_text = f"{label_text} {t('settings.windows_unsupported_suffix')}"
        return ft.Row(
            controls=[
                ft.Container(content=ft.Text(value=label_text, size=13), width=260),
                ft.Container(content=trailing, expand=True, alignment=ft.Alignment.CENTER_RIGHT),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    @staticmethod
    def _build_sectioned_column(sections: dict[str, list[ft.Control]]) -> ft.Column:
        """One tab's fields, split under subheadings.

        A section keyed by the empty string is the tab's lead block and is rendered
        without a heading — it is where fields that need no further qualification go.
        Sections keep the order they appear in OPTION_SPECS, except that the lead block
        is always first so a tab never opens on a subheading.
        """
        controls: list[ft.Control] = []
        ordered = sorted(sections.items(), key=lambda item: item[0] != "")
        for key, rows in ordered:
            if key:
                if controls:
                    controls.append(ft.Divider(height=1))
                controls.append(
                    ft.Text(value=t(key), size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.PRIMARY)
                )
            controls.extend(rows)
        return ft.Column(controls=controls, spacing=10, scroll=ft.ScrollMode.AUTO)

    @staticmethod
    def _make_inherit_handler(field_control: ft.Control, inherit_switch: ft.Switch) -> Callable[[ft.ControlEvent], None]:
        def handler(e: ft.ControlEvent) -> None:
            field_control.disabled = inherit_switch.value
            field_control.update()

        return handler

    @staticmethod
    def _build_field(spec: OptionSpec, settings: dict[str, object]) -> ft.Control:
        if spec.kind == "bool":
            return ft.Switch(value=bool(settings.get(spec.key, False)))
        if spec.kind == "choice":
            options = [ft.DropdownOption(key="", text=t("settings.default_option"))]
            options += [ft.DropdownOption(key=choice, text=choice) for choice in spec.choices]
            return ft.Dropdown(value=str(settings.get(spec.key, "")), options=options, dense=True)
        field_control = ft.TextField(value=str(settings.get(spec.key, "")), hint_text=spec.placeholder, dense=True)
        if spec.kind == "int":
            field_control.keyboard_type = ft.KeyboardType.NUMBER
            field_control.input_filter = ft.NumbersOnlyInputFilter()
        return field_control

    def content(self) -> ft.Control:
        return ft.Column(controls=[self.tabs, self.force_stop_switch], spacing=12, expand=True)

    def raw_values(self) -> dict[str, object]:
        values: dict[str, object] = {}
        for spec in OPTION_SPECS:
            if not spec.windows_supported:
                continue
            control = self.field_controls[spec.key]
            if spec.kind == "bool":
                values[spec.key] = bool(control.value)
            elif spec.kind == "choice":
                values[spec.key] = control.value or ""
            else:
                values[spec.key] = (control.value or "").strip()
        return values

    def inherited_keys(self) -> set[str]:
        return {key for key, switch in self.inherit_switches.items() if switch.value}

    def extra_arguments(self) -> list[str]:
        return list(self._extra_arguments)


class LauncherApp:
    """Flet Material 3 Expressive UI for the scrcpy launcher."""

    def __init__(self, page: ft.Page, project_root: Path, *, first_run: bool = False) -> None:
        self.page = page
        self.project_root = project_root
        self.adb, self.scrcpy = bundled_tools(project_root)
        # scrcpy's own server, patched so an app's display outlives the phone's screen
        # (see ENSURE_VIRTUAL_DEVICE_ASSOCIATION). Versioned name: the client refuses a
        # server of another version, so a scrcpy upgrade falls back to the stock one.
        patched_server = project_root / "launcher" / "resources" / f"scrcpy-server-{SCRCPY_VERSION}"
        if patched_server.is_file():
            os.environ.setdefault("SCRCPY_SERVER_PATH", str(patched_server))
        self.virtual_device_ready: set[str] = set()

        data_directory = _data_directory()
        self.store = ProfileStore(data_directory / "profiles.sqlite3")
        self.cache_directory = data_directory / "cache"
        self._apply_default_global_settings()

        self.devices: dict[str, DeviceInfo] = {}
        # serial -> form factor, in front of the store's per-model cache. `refresh_devices`
        # rebuilds every DeviceInfo from scratch, so without it the tile icon would drop
        # to the neutral one and pop back on each refresh; it is also what the session
        # tiles read, since a session outlives its device's entry in `self.devices`.
        self.device_form_factors: dict[str, str] = {}
        self.apps: list[AppInfo] = []
        self.selected_device: DeviceInfo | None = None
        self.sessions: dict[str, ScrcpySession] = {}

        self.icon_semaphore = asyncio.Semaphore(6)
        self.device_icon_generation = 0
        self.theme_color_generation = 0
        self.battery_poll_generation = 0
        self.device_icon_cache_key = ""
        self.device_package_versions: dict[str, str] = {}
        self.device_cached_records: dict[str, DeviceAppRecord] = {}
        self.device_fully_cached_packages: set[str] = set()
        self.favorite_device_key = ""
        self.favorite_packages: set[str] = set()
        self.view_mode = self._stored_view_mode()
        self.loading_apps = False
        self.zoom_catalog_on_render = False
        # Launcher-wide look-and-feel preferences (see edit_launcher_settings).
        self.base_seed_color = stored_seed_color(self.store)
        self.theme_mode = stored_theme_mode(self.store)
        self.use_device_accent = self._stored_device_accent()
        # Accent colour of the selected device, "" when unknown or disabled — what a
        # hover preview reverts *to*.
        self.device_seed_color = ""
        # Running packages as of the last _render_apps, to detect when the catalogue
        # actually needs rebuilding on a session change.
        self.rendered_running_packages: set[str] = set()
        # Live controls of the currently rendered cards/rows, keyed by package, so
        # single-cell changes never rebuild the catalogue. See AppCell. A package maps
        # to a *list* because a favorite is rendered twice — once in the favorites row
        # and once in the full grid — and both copies have to stay in sync.
        self.app_cards: dict[str, list[AppCell]] = {}
        self.app_tiles: dict[str, list[AppCell]] = {}
        # One-shot onboarding pointer at the launcher-settings button, shown on the
        # first run only — after that the user has already been told where it is —
        # plus the hover state that decides whether it may be taken away again.
        self.settings_hint_done = not first_run
        self.settings_hint_active = False
        self.title_hovered = False

        self._build_ui()
        self._apply_theme_colors()

    # -- launcher preferences ---------------------------------------------------------

    def _stored_view_mode(self) -> str:
        stored = self.store.get_preference("default_view_mode", DEFAULT_VIEW_MODE)
        return stored if stored in {"grid", "list"} else DEFAULT_VIEW_MODE

    def _stored_device_accent(self) -> bool:
        return bool(self.store.get_preference("device_accent_enabled", True))

    def _apply_default_global_settings(self) -> None:
        """Seed recommended global defaults once, leaving them switchable afterwards.

        Merging DEFAULT_GLOBAL_SETTINGS on every read would make them impossible to
        turn off: build_global_settings drops False values, so a saved settings dict
        simply lacks the keys the user unchecked, and a merge would keep reviving them.
        Seeding once behind a version marker also lets an existing installation pick up
        a default added later, without overwriting a value the user already chose.
        """
        applied = self.store.get_preference(GLOBAL_DEFAULTS_APPLIED_KEY, 0)
        if isinstance(applied, int) and applied >= GLOBAL_DEFAULTS_VERSION:
            return
        settings = dict(self.store.get_preference("global_settings", {}))
        for key, value in DEFAULT_GLOBAL_SETTINGS.items():
            settings.setdefault(key, value)
        self.store.set_preference("global_settings", settings)
        self.store.set_preference(GLOBAL_DEFAULTS_APPLIED_KEY, GLOBAL_DEFAULTS_VERSION)

    # -- small async-binding helper -------------------------------------------------

    def _bind(self, func: Callable[..., object], *args: object) -> Callable[[ft.ControlEvent], Awaitable[None]]:
        async def handler(e: ft.ControlEvent | None = None) -> None:
            result = func(*args)
            if asyncio.iscoroutine(result):
                await result

        return handler

    # -- UI construction --------------------------------------------------------------

    def _build_ui(self) -> None:
        self.device_dropdown = ft.Dropdown(
            hint_text=t("appbar.select_device_hint"),
            width=260,
            dense=True,
            options=[],
            on_select=self._on_device_dropdown_change,
        )
        self.battery_icon = ft.Icon(ft.Icons.BATTERY_UNKNOWN, size=20, color=ft.Colors.OUTLINE)
        self.battery_text = ft.Text(value="", size=13, color=ft.Colors.OUTLINE)
        self.battery_indicator = ft.Row(
            controls=[self.battery_icon, self.battery_text],
            spacing=4,
            visible=False,
            tooltip=t("appbar.battery_tooltip"),
        )
        # Tucked in beside the app title and only revealed on hover: these are
        # set-once preferences (language, theme, default view) that are rarely
        # revisited, so they earn no permanent space in the app bar. It slides out
        # from behind the title (left to right) and back in again, the mirror of the
        # app-bar actions on the other end. Offset and opacity rather than `visible`
        # keep the slot reserved, so revealing it never shifts the title sideways.
        self.launcher_settings_button = ft.IconButton(
            icon=ft.Icons.TUNE,
            tooltip=t("launcher_settings.title"),
            on_click=self._bind(self.edit_launcher_settings),
        )
        self.launcher_settings_area = ft.Container(
            content=self.launcher_settings_button,
            offset=ft.Offset(APPBAR_SETTINGS_SLIDE_OFFSET, 0),
            opacity=0,
            border_radius=24,
            # `animate` covers the decoration, which is what the hint pulse drives.
            animate=ft.Animation(SETTINGS_HINT_PULSE_MS, ft.AnimationCurve.EASE_IN_OUT),
            animate_offset=ft.Animation(APPBAR_ACTIONS_SLIDE_DURATION_MS, CATALOG_ZOOM_IN_CURVE),
            animate_opacity=ft.Animation(APPBAR_ACTIONS_SLIDE_DURATION_MS, CATALOG_ZOOM_IN_CURVE),
        )
        self.appbar_title = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Text(value="Scrcpy Launcher", weight=ft.FontWeight.BOLD),
                    self.launcher_settings_area,
                ],
                spacing=4,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            on_hover=self._on_title_hover,
        )
        self.appbar_actions = ft.Row(
            controls=[
                self.battery_indicator,
                ft.Container(width=8),
                self.device_dropdown,
                ft.IconButton(icon=ft.Icons.SMARTPHONE, tooltip=t("appbar.device_manager_tooltip"), on_click=self._on_show_device_manager),
                ft.IconButton(icon=ft.Icons.VIEW_AGENDA, tooltip=t("appbar.sessions_tooltip"), on_click=self._on_show_session_manager),
                ft.IconButton(icon=ft.Icons.SETTINGS, tooltip=t("settings.global_title"), on_click=self._bind(self.edit_global_settings)),
                ft.Container(width=8),
            ],
            visible=False,
        )
        # Parked to the right and transparent; _set_appbar_actions_visible slides it
        # into place once a device is picked. `visible` on the row itself stays the
        # on/off gate, so the parked controls can't be clicked through the fade.
        self.appbar_actions_area = ft.Container(
            content=self.appbar_actions,
            offset=ft.Offset(APPBAR_ACTIONS_SLIDE_OFFSET, 0),
            opacity=0,
            animate_offset=ft.Animation(APPBAR_ACTIONS_SLIDE_DURATION_MS, CATALOG_ZOOM_IN_CURVE),
            animate_opacity=ft.Animation(APPBAR_ACTIONS_SLIDE_DURATION_MS, CATALOG_ZOOM_IN_CURVE),
        )
        self.page.appbar = ft.AppBar(
            title=self.appbar_title,
            center_title=False,
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            actions=[self.appbar_actions_area],
        )
        self.page.floating_action_button = ft.FloatingActionButton(
            icon=ft.Icons.REFRESH,
            content=t("common.refresh"),
            tooltip=t("appbar.refresh_devices_tooltip"),
            on_click=self._bind(self.refresh_devices),
        )

        self.search_field = ft.TextField(
            hint_text=t("catalog.search_hint"),
            prefix_icon=ft.Icons.SEARCH,
            width=280,
            dense=True,
            on_change=self._on_filter_changed,
        )
        self.system_switch = ft.Switch(label=t("catalog.system_packages_label"), value=False, on_change=self._on_system_switch_changed)
        self.app_count_text = ft.Text(value=t("catalog.app_count_plain", count=0), color=ft.Colors.OUTLINE)
        self.view_toggle = ft.SegmentedButton(
            segments=view_mode_segments(),
            selected=[self.view_mode],
            on_change=self._on_view_toggle,
        )
        self.filter_row = ft.Row(
            controls=[
                self.search_field,
                self.system_switch,
                ft.Container(expand=True),
                self.app_count_text,
                self.view_toggle,
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            visible=False,
        )

        self.favorites_label = ft.Text(value=t("catalog.favorites_label"), weight=ft.FontWeight.BOLD, size=16, color=ft.Colors.TERTIARY)
        self.all_label = ft.Text(value=t("catalog.all_label"), weight=ft.FontWeight.BOLD, size=16)
        # Both card rows are replaced wholesale on every render (see _render_apps).
        self.favorites_row = self._build_app_card_row([], set(), section="favorites", visible=False)
        self.all_row = self._build_app_card_row([], set(), section="all")
        self.grid_view_column = ft.Column(
            controls=[
                self.favorites_label,
                self.favorites_row,
                self.all_label,
                self.all_row,
            ],
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

        self.app_list = ft.ListView(controls=[], expand=True, spacing=4)

        self._build_device_select_view()
        self._build_loading_view()
        self._build_catalog_slide_views()

        self.catalog_area = ft.Container(
            content=self.device_select_view,
            expand=True,
            padding=8,
            scale=1.0,
            opacity=1.0,
            alignment=ft.Alignment.CENTER,
            animate_scale=ft.Animation(CATALOG_ZOOM_IN_DURATION_MS, CATALOG_ZOOM_IN_CURVE),
            animate_opacity=ft.Animation(CATALOG_ZOOM_IN_DURATION_MS, CATALOG_ZOOM_IN_CURVE),
        )
        # The filter row appears together with the catalogue it filters, so it rides
        # the same zoom rather than snapping in above an animating catalog_area. It
        # gets its own container because the row itself is toggled with `visible`.
        self.filter_area = ft.Container(
            content=self.filter_row,
            scale=1.0,
            opacity=1.0,
            animate_scale=ft.Animation(CATALOG_ZOOM_IN_DURATION_MS, CATALOG_ZOOM_IN_CURVE),
            animate_opacity=ft.Animation(CATALOG_ZOOM_IN_DURATION_MS, CATALOG_ZOOM_IN_CURVE),
        )
        self.status_text = ft.Text(value=t("status.ready"), color=ft.Colors.OUTLINE)

        self.page.add(
            ft.Column(
                controls=[self.filter_area, self.catalog_area, self.status_text],
                expand=True,
                spacing=8,
            )
        )

        self._build_device_dialog()
        self._build_session_dialog()
        self._update_catalog_area()

    def _build_device_select_view(self) -> None:
        self.device_select_list = ft.ListView(controls=[], spacing=6, expand=True)
        self.device_select_empty_text = ft.Text(
            value=t("device_select.empty"),
            color=ft.Colors.OUTLINE,
        )
        self.device_select_view = ft.Column(
            controls=[
                ft.Container(height=16),
                ft.Icon(ft.Icons.SMARTPHONE, size=48, color=ft.Colors.PRIMARY),
                ft.Text(value=t("device_select.title"), size=20, weight=ft.FontWeight.BOLD),
                ft.Text(
                    value=t("device_select.description"),
                    color=ft.Colors.OUTLINE,
                ),
                ft.Container(height=8),
                self.device_select_empty_text,
                ft.Container(
                    content=self.device_select_list,
                    width=560,
                    expand=True,
                ),
                ft.OutlinedButton(
                    content=t("device_select.connect_button"),
                    icon=ft.Icons.WIFI,
                    on_click=self._on_show_device_manager,
                ),
            ],
            spacing=10,
            expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _build_loading_view(self) -> None:
        self.loading_status_text = ft.Text(value=t("status.fetching_apps"), color=ft.Colors.OUTLINE)
        self.loading_view = ft.Column(
            controls=[
                ft.ProgressRing(),
                ft.Container(height=12),
                self.loading_status_text,
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            expand=True,
        )

    def _build_catalog_slide_views(self) -> None:
        """Both catalog views stay mounted in a stack; the inactive one is parked
        off-screen (grid to the left, table to the right) at zero opacity, so a mode
        switch is a single simultaneous offset+opacity animation with no snapping."""
        def animation() -> ft.Animation:
            return ft.Animation(CATALOG_SLIDE_DURATION_MS, CATALOG_SLIDE_CURVE)

        # Which of the two starts on screen follows the saved default view mode.
        table_active = self.view_mode == "list"
        self.grid_slide_container = ft.Container(
            content=self.grid_view_column,
            left=0,
            top=0,
            right=0,
            bottom=0,
            offset=ft.Offset(-1 if table_active else 0, 0),
            opacity=0 if table_active else 1,
            animate_offset=animation(),
            animate_opacity=animation(),
        )
        self.table_slide_container = ft.Container(
            content=self.app_list,
            left=0,
            top=0,
            right=0,
            bottom=0,
            offset=ft.Offset(0 if table_active else 1, 0),
            opacity=1 if table_active else 0,
            animate_offset=animation(),
            animate_opacity=animation(),
        )
        self.catalog_stack = ft.Stack(
            controls=[self.grid_slide_container, self.table_slide_container], expand=True
        )

    def _set_appbar_actions_visible(self, visible: bool) -> None:
        """Show the app-bar actions with a slide-in from the right, hide them at once.

        Only the appearance is animated: hiding happens on the way back to the
        device-select screen, where the whole app bar is being emptied anyway, so
        there is nothing to animate against. Re-parking on hide is what leaves the
        row ready to slide in again next time.
        """
        if visible == self.appbar_actions.visible:
            return
        self.appbar_actions.visible = visible
        if not visible:
            self.appbar_actions_area.offset = ft.Offset(APPBAR_ACTIONS_SLIDE_OFFSET, 0)
            self.appbar_actions_area.opacity = 0
            return
        asyncio.create_task(self._settle_appbar_actions())

    async def _settle_appbar_actions(self) -> None:
        # Deliberately in step with the catalog zoom-in (see _zoom_catalog_area): the
        # actions belong to the catalogue, so they ride in with it rather than ahead
        # of it. Outside a zoom the wait is short enough to read as immediate.
        await asyncio.sleep(CATALOG_ZOOM_RESET_SETTLE)
        if not self.appbar_actions.visible:
            return
        self.appbar_actions_area.offset = ft.Offset(0, 0)
        self.appbar_actions_area.opacity = 1
        self.page.update()

    def _set_launcher_settings_shown(self, shown: bool) -> None:
        self.launcher_settings_area.offset = ft.Offset(0 if shown else APPBAR_SETTINGS_SLIDE_OFFSET, 0)
        self.launcher_settings_area.opacity = 1 if shown else 0
        # Pushed through the page rather than the container: a control-level update()
        # re-renders that subtree and the new offset lands without ever animating.
        self.page.update()

    def _set_settings_glow(self, on: bool) -> None:
        self.launcher_settings_area.bgcolor = ft.Colors.PRIMARY_CONTAINER if on else None
        self.launcher_settings_area.shadow = (
            ft.BoxShadow(spread_radius=1, blur_radius=14, color=ft.Colors.PRIMARY) if on else None
        )
        self.page.update()

    async def _flash_settings_hint(self) -> None:
        """Point the launcher-settings button out once, on the first run only.

        It normally only appears on hover, which nobody discovers by accident — so the
        first time the device-select screen comes up (right after the first-run
        language question) it slides out, pulses a glow to catch the eye, holds still
        long enough to be looked at, and leaves. If the pointer found it in the
        meantime, the hover state wins and it stays put.
        """
        # The client has to finish mounting the first screen before this can animate:
        # queued any earlier, the reveal is folded into the initial render (same reason
        # _zoom_catalog_area waits out its own mount, see CATALOG_ZOOM_RESET_SETTLE).
        await asyncio.sleep(CATALOG_ZOOM_RESET_SETTLE)
        self.settings_hint_active = True
        try:
            self._set_launcher_settings_shown(True)
            for _ in range(SETTINGS_HINT_PULSES):
                self._set_settings_glow(True)
                await asyncio.sleep(SETTINGS_HINT_PULSE_MS / 1000)
                self._set_settings_glow(False)
                await asyncio.sleep(SETTINGS_HINT_PULSE_MS / 1000)
            await asyncio.sleep(SETTINGS_HINT_DURATION)
        finally:
            self.settings_hint_active = False
        if self.title_hovered:
            return
        self._set_launcher_settings_shown(False)

    def _apply_catalog_area(self) -> None:
        """Pick which view fills the catalog area, without pushing an update."""
        on_device_select_screen = not self.selected_device or self.selected_device.state != "device"
        self._set_appbar_actions_visible(not on_device_select_screen)
        if on_device_select_screen and not self.settings_hint_done:
            self.settings_hint_done = True
            asyncio.create_task(self._flash_settings_hint())
        if on_device_select_screen:
            self.catalog_area.content = self.device_select_view
            self.filter_row.visible = False
        elif self.loading_apps:
            self.catalog_area.content = self.loading_view
            self.filter_row.visible = False
        else:
            self.catalog_area.content = self.catalog_stack
            self.filter_row.visible = True

    def _update_catalog_area(self) -> None:
        self._apply_catalog_area()
        self.page.update()

    def _set_zoom_animation(self, duration_ms: int, curve: ft.AnimationCurve) -> None:
        animation = ft.Animation(duration_ms, curve)
        for area in (self.catalog_area, self.filter_area):
            area.animate_scale = animation
            area.animate_opacity = animation

    def _set_zoom_state(self, scale: float, opacity: float) -> None:
        for area in (self.catalog_area, self.filter_area):
            area.scale = scale
            area.opacity = opacity

    async def _zoom_catalog_area(self, mount: Callable[[], None] | None = None) -> None:
        """Swap the catalog area's content with a zoom-in/fade-out transition.

        Used when leaving the device-select screen after picking a device — a
        different move than the grid/table cross-slide, so it animates the whole
        catalog_area rather than the two slide containers. Both halves zoom *up*:
        the outgoing view grows past 1.0 while fading out, then the incoming one
        grows into place from below 1.0 as it fades in.

        The scale reset between the two halves has to ride a zero-length animation,
        otherwise the container visibly travels back down from the zoomed-out scale.
        Swapping the Animation object like that is safe — only the value change it
        accompanies is animated. `mount` swaps in the new content and pushes its own
        update, for callers that rebuild controls in the same frame.

        This awaits the zoom-in phase to completion before returning — it used to
        return right as that animation started, so callers (load_apps chains two of
        these back to back: device-select -> loading, then loading -> catalog) would
        immediately fire the next transition on top of the first. On a slow device the
        real ADB round-trip in between happened to outlast the missing wait, so it
        looked fine by accident; on a fast device (e.g. a Pixel replying in well under
        300ms) the second transition collided with the first mid-flight and the two
        collapsed into what looked like no animation at all.
        """
        self._set_zoom_animation(CATALOG_ZOOM_OUT_DURATION_MS, CATALOG_ZOOM_OUT_CURVE)
        self._set_zoom_state(CATALOG_ZOOM_OUT_END_SCALE, 0)
        self.page.update()
        await asyncio.sleep(CATALOG_ZOOM_OUT_DURATION_MS / 1000)

        # Reset and re-content while invisible, so neither is visible to the user.
        self._set_zoom_animation(0, CATALOG_ZOOM_IN_CURVE)
        self._set_zoom_state(CATALOG_ZOOM_IN_START_SCALE, 0)
        if mount is None:
            self._apply_catalog_area()
            self.page.update()
        else:
            mount()
        await asyncio.sleep(CATALOG_ZOOM_RESET_SETTLE)

        self._set_zoom_animation(CATALOG_ZOOM_IN_DURATION_MS, CATALOG_ZOOM_IN_CURVE)
        self._set_zoom_state(1.0, 1.0)
        self.page.update()
        await asyncio.sleep(CATALOG_ZOOM_IN_DURATION_MS / 1000)

    def _slide_catalog_view(self, new_mode: str) -> None:
        """Cross-slide the two catalog views in a single animated update.

        Grid always lives on the left side of the table, so going to the table
        pushes the grid out to the left while the table rides in from the right,
        and going back reverses both. Offset and opacity share one symmetric
        ease-in-out bezier, so motion peaks and the cross-fade lands mid-flight.
        """
        if new_mode == "list":
            self.grid_slide_container.offset = ft.Offset(-1, 0)
            self.grid_slide_container.opacity = 0
            self.table_slide_container.offset = ft.Offset(0, 0)
            self.table_slide_container.opacity = 1
        else:
            self.table_slide_container.offset = ft.Offset(1, 0)
            self.table_slide_container.opacity = 0
            self.grid_slide_container.offset = ft.Offset(0, 0)
            self.grid_slide_container.opacity = 1
        self.page.update()

    def _build_device_dialog(self) -> None:
        self.device_ip_field = ft.TextField(label=t("device_manager.ip_label"), hint_text="192.168.1.25", width=200, dense=True)
        self.device_port_field = ft.TextField(label=t("device_manager.port_label"), hint_text="5555", width=120, dense=True)
        self.device_info_text = ft.Text(value=t("device_manager.no_device_selected"))
        self.device_list = ft.ListView(controls=[], expand=True, spacing=4)
        self.device_dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(value=t("device_manager.title")),
            content=ft.Container(
                width=880,
                height=520,
                content=ft.Column(
                    controls=[
                        ft.Row(
                            controls=[
                                ft.Row(
                                    controls=[
                                        self.device_ip_field,
                                        self.device_port_field,
                                        ft.FilledButton(content=t("device_manager.connect_button"), on_click=self._on_connect_from_manager),
                                        ft.OutlinedButton(
                                            content=t("action.pair"), icon=ft.Icons.WIFI, on_click=self._bind(self.pair_device)
                                        ),
                                    ],
                                    spacing=8,
                                ),
                                ft.IconButton(icon=ft.Icons.REFRESH, tooltip=t("common.refresh"), on_click=self._bind(self.refresh_devices)),
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        self.device_list,
                        self.device_info_text,
                    ],
                    expand=True,
                    spacing=10,
                ),
            ),
            actions=[ft.TextButton(content=t("common.close"), on_click=self._on_close_dialog)],
        )

    def _build_session_dialog(self) -> None:
        self.session_list = ft.ListView(controls=[], expand=True, spacing=4)
        self.session_dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(value=t("sessions.title")),
            content=ft.Container(
                width=820,
                height=420,
                content=self.session_list,
            ),
            actions=[ft.TextButton(content=t("common.close"), on_click=self._on_close_dialog)],
        )

    async def _reload_ui(self) -> None:
        """Rebuild every control from scratch so newly-set translations take effect.

        Flet controls carry their text as a fixed constructor argument, so a language
        switch can't just flip a value in place — the whole tree built by _build_ui
        has to be thrown away and reconstructed. Window geometry (configure_window)
        is deliberately not re-run here, since it would snap a resized window back to
        the default size. In-memory state (devices, apps, sessions, selected device)
        survives the rebuild untouched; only the controls that display it are new, so
        they have to be re-populated from that state afterwards.
        """
        search_value = self.search_field.value
        system_value = self.system_switch.value

        self.page.controls.clear()
        # self.view_mode survives the rebuild, and _build_ui reads it for both the
        # toggle and which slide container starts on screen.
        self._build_ui()

        self.search_field.value = search_value
        self.system_switch.value = system_value

        self._update_device_controls()
        if self.selected_device:
            self.device_info_text.value = self._device_info_label(self.selected_device)
        self._rebuild_session_list()
        if self.apps:
            self._render_apps()
            self._schedule_visible_icons()
        self.page.update()

    # -- top-level lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        await self.refresh_devices()

    async def _on_close_dialog(self, e: ft.ControlEvent) -> None:
        self.page.pop_dialog()

    async def _on_show_device_manager(self, e: ft.ControlEvent) -> None:
        self.page.show_dialog(self.device_dialog)

    async def _on_show_session_manager(self, e: ft.ControlEvent) -> None:
        self.page.show_dialog(self.session_dialog)

    async def _on_title_hover(self, e: ft.ControlEvent) -> None:
        entered = e.data if isinstance(e.data, bool) else str(e.data).lower() in {"true", "1"}
        self.title_hovered = entered
        # While the hint is running the button belongs to it: the app bar re-renders
        # several times during startup (devices arriving), and each rebuild delivers a
        # hover-exit that would otherwise cut the hint short after a second or two.
        if not entered and self.settings_hint_active:
            return
        self._set_launcher_settings_shown(entered)

    async def _apply_language(self, language: str) -> None:
        if language == get_language():
            return
        set_language(language)
        self.store.set_preference("language", language)
        await self._reload_ui()

    async def _on_connect_from_manager(self, e: ft.ControlEvent) -> None:
        host = (self.device_ip_field.value or "").strip()
        port = (self.device_port_field.value or "").strip()
        await self._connect_endpoint(f"{host}:{port}" if host and port else "")

    def _on_filter_changed(self, e: ft.ControlEvent) -> None:
        self._render_apps()
        self._schedule_visible_icons()

    def _on_view_toggle(self, e: ft.ControlEvent) -> None:
        new_mode = "list" if "list" in self.view_toggle.selected else "grid"
        if new_mode == self.view_mode:
            return
        self.view_mode = new_mode
        self._slide_catalog_view(new_mode)

    async def _on_system_switch_changed(self, e: ft.ControlEvent) -> None:
        self.loading_apps = True
        self._set_status(t("status.updating_apps"))
        self._update_catalog_area()
        await asyncio.sleep(0.05)

        # Re-attach the catalog *before* rebuilding the cards, and let the single
        # update from _render_apps carry both changes. Replacing the children of a
        # detached row and only re-attaching afterwards makes the Flet client
        # reconciler throw IndexError, after which that subtree never repaints.
        self.loading_apps = False
        self._apply_catalog_area()
        self._render_apps()
        self._schedule_visible_icons()
        self._set_status(t("status.apps_count", count=len(self.apps)))

    async def _on_device_dropdown_change(self, e: ft.ControlEvent) -> None:
        serial = self.device_dropdown.value
        if serial:
            await self._select_device(serial)

    def _set_status(self, message: str) -> None:
        self.status_text.value = message
        self.loading_status_text.value = message
        self.page.update()

    def _show_unsupported_device_message(self, device: DeviceInfo) -> None:
        """Bottom bar explaining why a device can't be opened.

        Built fresh each time: `show_dialog` refuses a control that is still open, and
        a new bar also restarts the timer when the user taps the same device twice.
        """
        message = t(UNSUPPORTED_FORM_FACTOR_MESSAGES.get(device.form_factor, "device.unsupported_generic"))
        self.page.show_dialog(
            ft.SnackBar(
                content=ft.Text(message),
                behavior=ft.SnackBarBehavior.FLOATING,
                duration=UNSUPPORTED_SNACK_DURATION,
                show_close_icon=True,
            )
        )
        self._set_status(message)

    async def _show_error(self, title: str, details: str) -> None:
        self._set_status(title)
        await show_message(self.page, title, details.strip() or t("error.unknown"))

    async def _require_device(self) -> bool:
        if self.selected_device and self.selected_device.state == "device":
            return True
        await self._show_error(t("device_manager.no_device_selected"), t("error.no_device_details"))
        return False

    def _adb_shell(self, serial: str, *command: str) -> Awaitable[tuple[int, str, str]]:
        return run_tool(self.adb, ["-s", serial, "shell", *command])

    def _serial_is_selected(self, serial: str) -> bool:
        return self.selected_device is not None and self.selected_device.serial == serial

    # -- devices ------------------------------------------------------------------

    @staticmethod
    def _state_label(state: str) -> str:
        return {
            "device": t("device.state_online"),
            "offline": t("device.state_offline"),
            "unauthorized": t("device.state_unauthorized"),
        }.get(state, state)

    def _device_info_label(self, device: DeviceInfo) -> str:
        return f"{device.model or device.serial} — {self._state_label(device.state)}"

    async def refresh_devices(self) -> None:
        self._set_status(t("status.searching_devices"))
        code, out, err = await run_tool(self.adb, ["devices", "-l"])
        if code:
            await self._show_error(t("error.adb_devices_failed"), err or out)
            return
        previously_selected = self.selected_device.serial if self.selected_device else ""
        self.devices = {device.serial: device for device in parse_adb_devices(out)}
        for device in self.devices.values():
            device.form_factor = self._known_form_factor(device)
        self._update_device_controls()
        for device in self.devices.values():
            if device.state == "device":
                asyncio.create_task(self._fetch_device_details(device))
                if not device.form_factor:
                    asyncio.create_task(self._fetch_form_factor(device))
        if previously_selected in self.devices:
            await self._select_device(previously_selected)
        self._set_status(t("status.devices_found", count=len(self.devices)))

    def _known_form_factor(self, device: DeviceInfo) -> str:
        """This device's classification without asking it anything.

        The stored answer is what makes an unsupported device render greyed out on the
        very first frame after a restart, instead of looking selectable for as long as
        the probe takes and then changing under the pointer.
        """
        cached = self.device_form_factors.get(device.serial)
        if cached:
            return cached
        stored = self.store.cached_form_factor(device.hardware_key, FORM_FACTOR_DETECTOR_VERSION)
        if stored:
            self.device_form_factors[device.serial] = stored
        return stored

    async def _fetch_form_factor(self, device: DeviceInfo) -> None:
        """Work out whether this is a phone, tablet, TV, watch, … over ADB.

        Runs as its own task rather than inside `_fetch_device_details` so it never
        delays the Android version, battery and stable id that dialog already
        resolves. The two authoritative probes go out together; the screen geometry
        needed for the phone/tablet split is only fetched when they came back
        inconclusive, which is the one case it can decide.
        """
        features_result, characteristics_result = await asyncio.gather(
            self._adb_shell(device.serial, "pm", "list", "features"),
            self._adb_shell(device.serial, "getprop", "ro.build.characteristics"),
        )
        if self.devices.get(device.serial) is not device:
            return
        features_code, features_out, _err = features_result
        characteristics_code, characteristics_out, _err = characteristics_result
        features = parse_pm_features(features_out) if not features_code else set()
        characteristics = characteristics_out.strip() if not characteristics_code else ""

        form_factor = detect_form_factor(features, characteristics)
        if not form_factor:
            size_result, density_result = await asyncio.gather(
                self._adb_shell(device.serial, "wm", "size"),
                self._adb_shell(device.serial, "wm", "density"),
            )
            if self.devices.get(device.serial) is not device:
                return
            size_code, size_out, _err = size_result
            density_code, density_out, _err = density_result
            form_factor = detect_form_factor(
                features,
                characteristics,
                parse_wm_size(size_out) if not size_code else None,
                parse_wm_density(density_out) if not density_code else None,
            )
        if not form_factor:
            return
        self.device_form_factors[device.serial] = form_factor
        self.store.remember_form_factor(device.hardware_key, form_factor, FORM_FACTOR_DETECTOR_VERSION)
        device.form_factor = form_factor
        # A device already picked before the probe finished can turn out to be one we
        # don't support — drop back to the device list rather than leaving the user on
        # a catalogue whose apps all refuse to launch.
        if not device.supports_launch and self.selected_device is device:
            self.selected_device = None
            self.apps = []
            self.loading_apps = False
            self._update_catalog_area()
            self._show_unsupported_device_message(device)
        self._update_device_controls()

    async def _fetch_device_details(self, device: DeviceInfo) -> None:
        code, out, _err = await self._adb_shell(device.serial, "getprop", "ro.build.version.release")
        if not code and self.devices.get(device.serial) is device:
            device.android_version = out.strip() or "?"
            self._update_device_controls()

        code, out, _err = await self._adb_shell(device.serial, "dumpsys", "battery")
        if not code and self.devices.get(device.serial) is device:
            device.battery_level, device.battery_charging = parse_battery_status(out)
            self._update_device_controls()

        code, out, _err = await self._adb_shell(device.serial, "getprop", "ro.serialno")
        if code or self.devices.get(device.serial) is not device or out.strip() in {"", "unknown"}:
            return
        old_key = device.profile_key
        device.stable_id = out.strip()
        if self._serial_is_selected(device.serial) and old_key != device.profile_key:
            self.store.merge_favorites(old_key, device.profile_key)
            self.favorite_device_key = device.profile_key
            self.favorite_packages = self.store.favorites(self.favorite_device_key)
            self._render_apps()
        self._update_device_controls()

    def _update_device_controls(self) -> None:
        # The dropdown is a switch-to-device control, so it only carries devices that
        # can actually be switched to. The lists below still show the rest, greyed out,
        # where there is room to say why.
        self.device_dropdown.options = [
            ft.DropdownOption(
                key=device.serial,
                text=f"{device.model or device.product or t('device.unknown')} ({device.transport})",
            )
            for device in self.devices.values()
            if device.supports_launch
        ]
        selected = self.selected_device
        if selected and selected.serial in self.devices and selected.supports_launch:
            self.device_dropdown.value = selected.serial
        else:
            # Leaving a stale serial behind would render the dropdown blank, since it
            # no longer matches any option.
            self.device_dropdown.value = None
        self.device_list.controls = [self._build_device_tile(device) for device in self.devices.values()]
        self.device_select_list.controls = [
            self._build_device_tile(device, hover_preview=True) for device in self.devices.values()
        ]
        self.device_select_empty_text.visible = not self.devices
        self._update_battery_indicator()
        self.page.update()

    @staticmethod
    def _battery_icon(level: int, charging: bool) -> str:
        if charging:
            return ft.Icons.BATTERY_CHARGING_FULL
        if level >= 95:
            return ft.Icons.BATTERY_FULL
        if level <= 10:
            return ft.Icons.BATTERY_ALERT
        bar = min(6, max(1, round(level / 100 * 6)))
        bar_icons = {
            1: ft.Icons.BATTERY_1_BAR,
            2: ft.Icons.BATTERY_2_BAR,
            3: ft.Icons.BATTERY_3_BAR,
            4: ft.Icons.BATTERY_4_BAR,
            5: ft.Icons.BATTERY_5_BAR,
            6: ft.Icons.BATTERY_6_BAR,
        }
        return bar_icons[bar]

    def _update_battery_indicator(self) -> None:
        device = self.selected_device
        level = device.battery_level if device else None
        if device is None or device.state != "device" or level is None:
            self.battery_indicator.visible = False
            return
        charging = device.battery_charging
        self.battery_icon.icon = self._battery_icon(level, charging)
        self.battery_icon.color = (
            ft.Colors.TERTIARY if charging else ft.Colors.ERROR if level <= 15 else ft.Colors.OUTLINE
        )
        self.battery_text.value = f"{level}%"
        self.battery_indicator.visible = True

    def _start_battery_polling(self, device: DeviceInfo) -> None:
        self.battery_poll_generation += 1
        asyncio.create_task(self._poll_battery(device, self.battery_poll_generation))

    async def _poll_battery(self, device: DeviceInfo, generation: int) -> None:
        while True:
            await asyncio.sleep(BATTERY_POLL_INTERVAL)
            if generation != self.battery_poll_generation:
                return
            if not self._serial_is_selected(device.serial) or device.state != "device":
                return
            code, out, _err = await self._adb_shell(device.serial, "dumpsys", "battery")
            if generation != self.battery_poll_generation or self.devices.get(device.serial) is not device:
                return
            if not code:
                device.battery_level, device.battery_charging = parse_battery_status(out)
                self._update_battery_indicator()
                self.page.update()

    def _build_device_tile(self, device: DeviceInfo, *, hover_preview: bool = False) -> ft.Control:
        android = f"Android {device.android_version}" if device.state == "device" and device.android_version else "—"
        subtitle = f"{device.serial} · {device.stable_id or '…'} · {android}"
        selected = self._serial_is_selected(device.serial)
        # A device we can't drive still gets a row — hiding it would read as "not
        # detected" and send the user hunting through cables and pairing codes — but
        # it is drawn entirely in the muted colour so it is visibly not on offer.
        supported = device.supports_launch
        online = device.state == "device"
        title_color = None if supported else ft.Colors.OUTLINE
        tile = ft.ListTile(
            leading=ft.Icon(
                FORM_FACTOR_ICONS.get(device.form_factor, UNKNOWN_FORM_FACTOR_ICON),
                color=ft.Colors.PRIMARY if online and supported else ft.Colors.OUTLINE,
            ),
            title=ft.Text(value=device.model or device.product or t("device.unknown"), color=title_color),
            subtitle=ft.Text(value=subtitle, size=12, color=ft.Colors.OUTLINE),
            trailing=ft.Text(
                value=self._state_label(device.state) if supported else t("device.state_unsupported"),
                color=title_color,
            ),
            selected=selected and supported,
            # Same rounding as the app rows: the hover/selected highlight reads as a
            # pill instead of a full-bleed band across the list.
            shape=_shape(TILE_RADIUS),
            # Deliberately still clickable: the tap is what surfaces the explanation.
            on_click=self._bind(self._select_device, device.serial),
        )
        if not hover_preview:
            return tile
        # An unsupported device never gets to tint the app either — the accent preview
        # is a promise that picking it does something.
        on_hover = self._make_device_hover_handler(device) if supported else None
        return ft.Container(content=tile, on_hover=on_hover)

    def _set_theme_seed_color_raw(self, seed_color: str) -> None:
        """Regenerate both theme slots from one seed, leaving theme_mode as it stands.

        Flutter derives a light scheme for `theme` and a dark one for `dark_theme`;
        theme_mode is what picks between them.
        """
        self.page.theme = build_theme(seed_color)
        self.page.dark_theme = build_theme(seed_color)
        self.page.update()

    def _set_theme_seed_color(self, seed_color: str) -> None:
        self.page.theme_mode = THEME_MODES[self.theme_mode]
        self._set_theme_seed_color_raw(seed_color)

    def _effective_seed_color(self) -> str:
        """The device's accent when one is known and the setting allows it, else the
        base M3 theme picked in the launcher settings."""
        if self.use_device_accent and self.device_seed_color:
            return self.device_seed_color
        return self.base_seed_color

    def _apply_theme_colors(self) -> None:
        self._set_theme_seed_color(self._effective_seed_color())

    def _make_device_hover_handler(self, device: DeviceInfo) -> Callable[[ft.ControlEvent], Awaitable[None]]:
        async def handler(e: ft.ControlEvent) -> None:
            if not self.use_device_accent:
                return
            entered = e.data if isinstance(e.data, bool) else str(e.data).lower() in {"true", "1"}
            if entered:
                if device.state == "device":
                    await self._preview_device_hover_color(device)
            else:
                await self._schedule_hover_color_revert()

        return handler

    async def _schedule_hover_color_revert(self) -> None:
        self.theme_color_generation += 1
        generation = self.theme_color_generation
        await asyncio.sleep(HOVER_COLOR_REVERT_DELAY)
        if generation != self.theme_color_generation:
            return
        # Back to the selected device's colour, not the app default: the pointer
        # leaving a tile says nothing about which device is selected, and the tile
        # under the cursor is destroyed (firing a hover-exit) whenever picking a
        # device swaps the device-select screen away.
        self._apply_theme_colors()

    async def _device_accent_seed(self, serial: str) -> str:
        code, out, _err = await self._adb_shell(serial, "cmd", "overlay", "lookup", "android", "android:color/system_accent1_600")
        return parse_accent_color(out) if not code else ""

    async def _preview_device_hover_color(self, device: DeviceInfo) -> None:
        self.theme_color_generation += 1
        generation = self.theme_color_generation
        seed_color = await self._device_accent_seed(device.serial)
        if generation != self.theme_color_generation:
            return
        self._set_theme_seed_color(seed_color or self.base_seed_color)

    async def _apply_device_accent_color(self, device: DeviceInfo) -> None:
        # Bumping the generation cancels any in-flight hover preview, but this call
        # deliberately does *not* check the generation itself afterwards. Picking a
        # device destroys the hovered tile, and the resulting hover-exit schedules a
        # revert that bumps the generation again — checking it here is what used to
        # drop the device's colour on a fast click and leave the app default.
        self.theme_color_generation += 1
        if not self.use_device_accent:
            return
        device_seed = await self._device_accent_seed(device.serial)
        if not self._serial_is_selected(device.serial):
            return
        self.device_seed_color = device_seed
        self._apply_theme_colors()

    async def _select_device(self, serial: str) -> None:
        device = self.devices.get(serial)
        if not device:
            return
        if not device.supports_launch:
            # Only reachable from the device lists — the dropdown doesn't offer these.
            self._show_unsupported_device_message(device)
            return
        same_device = self._serial_is_selected(serial)
        # Whether *this* click actually changes what's shown — not whether we happen
        # to be on the device-select screen right now. Gating on the latter meant the
        # zoom only ever played for the first device picked in a session: switching
        # between two already-known devices (catalog already visible) never left the
        # device-select screen, so it silently skipped the animation from then on.
        zoom_in = not same_device
        self.selected_device = device
        self.device_info_text.value = self._device_info_label(device)
        self._update_device_controls()
        if device.state != "device":
            self.theme_color_generation += 1
            self.battery_poll_generation += 1
            self.device_seed_color = ""
            self._apply_theme_colors()
            self.apps = []
            self.loading_apps = False
            self.favorite_device_key = device.profile_key
            self.favorite_packages = self.store.favorites(self.favorite_device_key)
            self._render_apps()
            if zoom_in:
                await self._zoom_catalog_area()
            else:
                self._update_catalog_area()
            self._set_status(t("status.device_state", serial=device.serial, state=device.state))
            return
        if not same_device:
            asyncio.create_task(self._apply_device_accent_color(device))
            self._start_battery_polling(device)
        if not same_device or not self.apps:
            await self.load_apps(device, zoom_in=zoom_in)

    # -- app catalogue --------------------------------------------------------------

    async def load_apps(self, device: DeviceInfo, *, zoom_in: bool = False) -> None:
        self.device_icon_generation += 1
        self.device_icon_cache_key = device.profile_key
        self.device_package_versions = {}
        self.device_cached_records = {}
        self.device_fully_cached_packages = set()
        self.favorite_device_key = device.profile_key
        self.favorite_packages = self.store.favorites(self.favorite_device_key)
        self.apps = []
        self.loading_apps = True
        # The loading view is short-lived, so zoom the finished catalogue in as well
        # rather than letting it snap over a half-played zoom of the spinner.
        self.zoom_catalog_on_render = zoom_in
        if zoom_in:
            await self._zoom_catalog_area()
        else:
            self._update_catalog_area()
        self._set_status(t("status.fetching_apps"))

        code, out, _err = await self._adb_shell(device.serial, "am", "get-current-user")
        if not self._serial_is_selected(device.serial):
            return
        current_user = out.strip() if not code and out.strip().isdigit() else "0"

        code, out, err = await self._adb_shell(
            device.serial, "pm", "list", "packages", "--show-versioncode", "--user", current_user
        )
        if code:
            self.loading_apps = False
            self.zoom_catalog_on_render = False
            self._update_catalog_area()
            await self._show_error(t("error.apps_list_failed"), err or out)
            return
        package_versions = parse_pm_package_versions(out)

        code, out, _err = await self._adb_shell(device.serial, "pm", "list", "packages", "-3", "--user", current_user)
        if not self._serial_is_selected(device.serial):
            return
        user_packages = parse_pm_packages(out) if not code else set()

        self.device_package_versions = package_versions
        loaded_records = load_cached_app_records(self.cache_directory, self.device_icon_cache_key)
        self.device_cached_records = {
            package: record for package, record in loaded_records.items() if package in package_versions
        }

        apps: list[AppInfo] = []
        for package in sorted(package_versions):
            is_system = bool(user_packages) and package not in user_packages
            metadata = self.device_cached_records.get(package)
            icon_revision, cached_icon = cached_rendered_icon(self.cache_directory, self.device_icon_cache_key, package)
            current_revision = package_versions.get(package, "")
            valid_metadata = bool(metadata and current_revision and metadata.revision == current_revision)
            valid_icon = bool(cached_icon and current_revision and icon_revision == current_revision)
            app = AppInfo(
                package=package,
                label=metadata.label if valid_metadata and metadata else package,
                is_system=is_system,
                icon_path=str(cached_icon) if valid_icon and cached_icon else "",
            )
            if valid_metadata and valid_icon:
                self.device_fully_cached_packages.add(package)
            apps.append(app)
        apps.sort(key=lambda app: (app.label.casefold(), app.package))
        self.apps = apps
        self.loading_apps = False
        # Re-attach before rendering — see _on_system_switch_changed for why the
        # reverse order corrupts the client-side control tree.
        def mount_catalog() -> None:
            self._apply_catalog_area()
            self._render_apps()

        if self.zoom_catalog_on_render:
            self.zoom_catalog_on_render = False
            await self._zoom_catalog_area(mount_catalog)
        else:
            mount_catalog()
        self._set_status(t("status.apps_count", count=len(self.apps)))
        await self._start_device_icon_loader(device)

    def _pending_device_icon_arguments(self) -> list[str]:
        arguments: list[str] = []
        for app in self.apps:
            if app.package in self.device_fully_cached_packages:
                continue
            revision, cached = cached_rendered_icon(self.cache_directory, self.device_icon_cache_key, app.package)
            if cached and not app.icon_path:
                app.icon_path = str(cached)
            current_revision = self.device_package_versions.get(app.package, "")
            known_revision = revision if revision and revision == current_revision else ""
            arguments.append(helper_package_argument(app.package, known_revision))
        return arguments

    def _apply_device_icon_records(self, records: list[DeviceAppRecord]) -> None:
        by_package = {app.package: app for app in self.apps}
        touched: set[str] = set()
        # Records carry both labels (which re-sort the catalogue) and is_system (which
        # can add or drop rows under the system-packages filter), so the visible list
        # is what has to be compared — not just self.apps' order.
        previous_visible = [app.package for app in self._filtered_apps()]
        for record in records:
            app = by_package.get(record.package)
            if not app:
                continue
            if record.label and record.label != record.package:
                app.label = record.label
            app.is_system = record.is_system
            rendered = write_rendered_icon(self.cache_directory, self.device_icon_cache_key, record)
            if rendered:
                app.icon_path = str(rendered)
            touched.add(record.package)
            self.device_cached_records[record.package] = DeviceAppRecord(record.package, app.label, app.is_system, record.revision)
            if rendered and record.revision == self.device_package_versions.get(record.package, ""):
                self.device_fully_cached_packages.add(record.package)
        write_cached_app_records(self.cache_directory, self.device_icon_cache_key, self.device_cached_records)
        self.apps.sort(key=lambda app: (app.label.casefold(), app.package))
        # A changed visible list genuinely needs the rows rebuilt. When it held (the
        # common case once labels are cached), update just the affected cells so the
        # grid does not flash.
        visible = self._filtered_apps()
        now_visible = [app.package for app in visible]
        if set(now_visible) != set(previous_visible):
            # Packages appeared or disappeared (is_system was refined) — the rows have
            # to be rebuilt for that.
            self._render_apps()
            return
        if now_visible != previous_visible:
            # Only the sort changed, which is the common case while labels stream in.
            favorites = [app for app in visible if app.package in self.favorite_packages]
            if not self._reorder_app_cells(visible, favorites):
                self._render_apps()
                return
        self._refresh_app_cells(touched, by_package)
        self.page.update()

    async def _run_device_icon_loader(self, device: DeviceInfo, arguments: list[str], generation: int) -> bool:
        batches = package_batches(arguments, 40)
        total = len(arguments)
        state = {"completed": 0, "any_record": False}
        semaphore = asyncio.Semaphore(3)

        async def process(batch: list[str]) -> None:
            async with semaphore:
                if generation != self.device_icon_generation:
                    return
                code, out, _err = await self._adb_shell(
                    device.serial, f"CLASSPATH={HELPER_REMOTE_PATH}", "app_process", "/system/bin", HELPER_MAIN_CLASS, *batch
                )
                if generation != self.device_icon_generation:
                    return
                state["completed"] += len(batch)
                records = parse_device_app_records(out) if not code else []
                if records:
                    state["any_record"] = True
                    self._apply_device_icon_records(records)
                self._set_status(t("status.icons_progress", completed=state["completed"], total=total))

        if batches:
            await asyncio.gather(*(process(batch) for batch in batches))
        return bool(state["any_record"])

    async def _start_device_icon_loader(self, device: DeviceInfo) -> None:
        if self.apps and len(self.device_fully_cached_packages) == len(self.apps):
            self._set_status(t("status.apps_icons_from_cache", count=len(self.apps)))
            return

        generation = self.device_icon_generation
        helper = self.project_root / "launcher" / "resources" / "icon-dumper.dex"
        used_helper = False
        if helper.is_file():
            code, _out, _err = await self._adb_shell(device.serial, "mkdir", "-p", "/data/local/tmp/scrcpy-launcher")
            if not code:
                code, _out, _err = await run_tool(
                    self.adb, ["-s", device.serial, "push", "-q", str(helper), HELPER_REMOTE_PATH]
                )
            if not code:
                arguments = self._pending_device_icon_arguments()
                if arguments:
                    self._set_status(t("status.icons_processing"))
                    used_helper = await self._run_device_icon_loader(device, arguments, generation)

        if generation != self.device_icon_generation or not self._serial_is_selected(device.serial):
            return
        self.apps.sort(key=lambda app: (app.label.casefold(), app.package))
        self._render_apps()
        source = "Android" if used_helper else t("status.source_cache_adb")
        self._set_status(t("status.apps_icons_loaded_via", count=len(self.apps), source=source))
        self._schedule_visible_icons()

    def _filtered_apps(self) -> list[AppInfo]:
        query = (self.search_field.value or "").casefold().strip()
        applications = [
            app
            for app in self.apps
            if (self.system_switch.value or not app.is_system)
            and (not query or query in app.package.casefold() or query in app.label.casefold())
        ]
        return sorted(applications, key=lambda app: (app.label.casefold(), app.package))

    def _schedule_visible_icons(self) -> None:
        for app in self._filtered_apps():
            if not app.icon_path and not app.icon_attempted:
                app.icon_attempted = True
                asyncio.create_task(self._load_app_icon(app))

    async def _load_app_icon(self, app: AppInfo) -> None:
        device = self.selected_device
        if not device:
            return
        async with self.icon_semaphore:
            code, out, _err = await self._adb_shell(device.serial, "pm", "path", app.package)
            remote_path = extract_package_path(out) if not code else ""
            if not remote_path:
                return
            local_path = cache_filename(self.cache_directory, device.profile_key, app.package, remote_path)
            cached = next(local_path.parent.glob(local_path.name + ".*"), None)
            if cached:
                app.icon_path = str(cached)
                self._refresh_app_cells({app.package})
                return
            temporary_dir = Path(tempfile.mkdtemp(prefix="scrcpy-launcher-apk-"))
            apk_path = temporary_dir / "base.apk"
            code, _out, _err = await run_tool(self.adb, ["-s", device.serial, "pull", "-q", remote_path, str(apk_path)])
            if code:
                shutil.rmtree(temporary_dir, ignore_errors=True)
                return
            result_path = await asyncio.to_thread(extract_icon_from_apk, apk_path, local_path)
            if result_path:
                app.icon_path = str(result_path)
                self._refresh_app_cells({app.package})

    def _refresh_app_cells(self, packages: set[str], apps: dict[str, AppInfo] | None = None) -> None:
        """Push icon/running changes into the existing cards and rows.

        The alternative — calling _render_apps — throws away and rebuilds every card,
        which flashes the whole grid. Icons stream in package by package while a
        device loads, so that flash would otherwise be near-continuous.
        """
        by_package = apps if apps is not None else {app.package: app for app in self.apps}
        running = self._running_packages()
        self.rendered_running_packages = running
        for package in packages:
            app = by_package.get(package)
            if app is None:
                continue
            is_running = package in running

            for card in self.app_cards.get(package, ()):
                if card.surface is None:
                    continue
                card.icon_slot.content = app_icon_control(app.icon_path, app.label)
                card.label.value = app.label
                card.badge.visible = is_running
                card.surface.bgcolor = ft.Colors.PRIMARY_CONTAINER if is_running else None
                card.surface.border = ft.Border.all(2, ft.Colors.PRIMARY) if is_running else None
                card.surface.tooltip = self._card_tooltip(app, is_running)
                if card.launch_item is not None:
                    card.launch_item.visible = not is_running
                if card.stop_item is not None:
                    card.stop_item.visible = is_running
                card.surface.update()

            favorite = package in self.favorite_packages
            for tile_cell in self.app_tiles.get(package, ()):
                if tile_cell.tile is None:
                    continue
                tile_cell.icon_slot.content = app_icon_control(app.icon_path, app.label, 36)
                tile_cell.label.value = f"★ {app.label}" if favorite else app.label
                tile_cell.badge.visible = is_running
                tile_cell.tile.bgcolor = ft.Colors.PRIMARY_CONTAINER if is_running else None
                if tile_cell.subtitle is not None:
                    tile_cell.subtitle.value = self._tile_subtitle(app, is_running)
                if tile_cell.launch_button is not None:
                    tile_cell.launch_button.visible = not is_running
                if tile_cell.show_button is not None:
                    tile_cell.show_button.visible = is_running
                tile_cell.tile.update()

    def _cell_in(self, package: str, section: str, cells: dict[str, list[AppCell]]) -> AppCell | None:
        for cell in cells.get(package, ()):
            if cell.section == section:
                return cell
        return None

    def _reorder_app_cells(self, visible: list[AppInfo], favorites: list[AppInfo]) -> bool:
        """Re-order the existing cards/rows to match a new sort, reusing the controls.

        Labels streaming in from the device re-sort the catalogue without changing
        which packages are shown. Rebuilding for that flashes the whole grid, so the
        same controls are re-ordered in their parents instead. Returns False when a
        control is missing, which means the caller must fall back to a full rebuild.
        """
        def ordered(apps: list[AppInfo], section: str, cells: dict[str, list[AppCell]]) -> list[ft.Control] | None:
            controls: list[ft.Control] = []
            for app in apps:
                cell = self._cell_in(app.package, section, cells)
                if cell is None:
                    return None
                controls.append(cell.root)
            return controls

        favorite_controls = ordered(favorites, "favorites", self.app_cards)
        all_controls = ordered(visible, "all", self.app_cards)
        table_apps = self._table_order(visible)
        table_controls = ordered(table_apps, "table", self.app_tiles)
        if favorite_controls is None or all_controls is None or table_controls is None:
            return False

        # Same length as before — only the order differs — so this is the one case the
        # reconciler handles without the shrink-time IndexError noted in _render_apps.
        self.favorites_row.controls = favorite_controls
        self.all_row.controls = all_controls
        self.app_list.controls = table_controls
        return True

    def _table_order(self, visible: list[AppInfo]) -> list[AppInfo]:
        """Row order for the table view — favorites first. Shared by the full rebuild
        and the in-place re-order so the two can never disagree."""
        return sorted(
            visible, key=lambda app: (app.package not in self.favorite_packages, app.label.casefold(), app.package)
        )

    def _render_apps(self) -> None:
        visible = self._filtered_apps()
        favorites = [app for app in visible if app.package in self.favorite_packages]
        # Favorites are only ever mentioned once at least one app is favorited.
        self.app_count_text.value = (
            t("catalog.app_count", count=len(visible), favorites=len(favorites))
            if favorites
            else t("catalog.app_count_plain", count=len(visible))
        )
        # Snapshot once per render — every card and row consults it.
        running = self._running_packages()
        self.rendered_running_packages = running
        # Rebuilt below; stale entries would otherwise be updated after detaching.
        self.app_cards = {}
        self.app_tiles = {}

        # The card rows are rebuilt as fresh controls rather than having their
        # `controls` list reassigned in place: the Flet client reconciler throws
        # IndexError when a large wrap Row shrinks (e.g. 305 -> 39 packages after
        # turning system packages back off) and then never repaints that subtree
        # again. ListView (the table) handles the same change fine, so only the
        # wrap rows need this treatment.
        self.favorites_row = self._build_app_card_row(
            favorites, running, section="favorites", visible=bool(favorites)
        )
        self.all_row = self._build_app_card_row(visible, running, section="all")
        self.favorites_label.visible = bool(favorites)
        # The "All" header only makes sense as the counterpart of the favorites
        # section — without favorites the grid is just the one list.
        self.all_label.visible = bool(favorites)
        self.grid_view_column.controls = [
            self.favorites_label,
            self.favorites_row,
            self.all_label,
            self.all_row,
        ]

        table_apps = self._table_order(visible)
        # Same IndexError-on-shrink issue as the card rows above hits ListView too
        # (e.g. table view mounted with system packages on, then toggled off) — so
        # it's rebuilt fresh and swapped into the slide container rather than having
        # its `controls` reassigned in place.
        self.app_list = ft.ListView(
            controls=[self._build_app_tile(app, app.package in running) for app in table_apps],
            expand=True,
            spacing=4,
        )
        self.table_slide_container.content = self.app_list
        self.page.update()

    def _build_app_card_row(
        self, apps: list[AppInfo], running: set[str], *, section: str = "all", visible: bool = True
    ) -> ft.Row:
        return ft.Row(
            controls=[
                self._build_app_card(app, app.package in self.favorite_packages, app.package in running, section)
                for app in apps
            ],
            wrap=True,
            spacing=12,
            run_spacing=12,
            visible=visible,
        )

    def _profile_hint(self, app: AppInfo) -> str:
        if not self.selected_device:
            return ""
        profile = self.store.get_profile(self.selected_device.profile_key, app.package)
        return profile.name or (t("profile.configured") if profile.settings or profile.extra_arguments else "")

    def _build_app_card(
        self, app: AppInfo, favorite: bool, running: bool = False, section: str = "all"
    ) -> ft.Control:
        # Both session items always exist and toggle `visible`, so a state change is
        # a property update on live controls rather than a rebuilt menu.
        launch_item = ft.PopupMenuItem(
            content=t("action.launch"), icon=ft.Icons.PLAY_ARROW, on_click=self._bind(self.launch_app, app), visible=not running
        )
        stop_item = ft.PopupMenuItem(
            content=t("action.stop"), icon=ft.Icons.STOP_CIRCLE, on_click=self._bind(self.stop_app, app), visible=running
        )
        menu = ft.PopupMenuButton(
            icon=ft.Icons.MORE_VERT,
            items=[
                launch_item,
                stop_item,
                ft.PopupMenuItem(
                    content=t("action.unfavorite") if favorite else t("action.favorite"),
                    icon=ft.Icons.STAR if favorite else ft.Icons.STAR_BORDER,
                    on_click=self._bind(self.toggle_favorite, app),
                ),
                ft.PopupMenuItem(content=t("action.edit_profile_ellipsis"), icon=ft.Icons.TUNE, on_click=self._bind(self.edit_profile, app)),
            ],
        )
        icon_slot = ft.Container(
            content=app_icon_control(app.icon_path, app.label), width=ICON_SIZE, height=ICON_SIZE
        )
        badge = ft.Icon(ft.Icons.PLAY_CIRCLE, size=14, color=ft.Colors.PRIMARY, visible=running)
        label = ft.Text(
            value=app.label,
            size=13,
            weight=ft.FontWeight.W_500,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
            expand=True,
        )
        surface = ft.Container(
            width=APP_CARD_WIDTH,
            height=APP_CARD_HEIGHT,
            padding=10,
            ink=True,
            border_radius=SHAPE_RADIUS,
            # A running app's card lights up in the accent colour so it reads as
            # live at a glance, and clicking it switches to that window instead
            # of starting a second one.
            bgcolor=ft.Colors.PRIMARY_CONTAINER if running else None,
            border=ft.Border.all(2, ft.Colors.PRIMARY) if running else None,
            tooltip=self._card_tooltip(app, running),
            on_click=self._bind(self.open_or_launch_app, app),
            content=ft.Column(
                controls=[
                    ft.Row(controls=[icon_slot, menu], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    ft.Row(
                        controls=[badge, label],
                        spacing=4,
                        tight=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=6,
                tight=True,
            ),
        )
        card = ft.Card(width=APP_CARD_WIDTH, height=APP_CARD_HEIGHT, content=surface)
        self.app_cards.setdefault(app.package, []).append(
            AppCell(
                icon_slot=icon_slot,
                badge=badge,
                label=label,
                root=card,
                section=section,
                launch_item=launch_item,
                stop_item=stop_item,
                surface=surface,
            )
        )
        return card

    @staticmethod
    def _card_tooltip(app: AppInfo, running: bool) -> str:
        base = f"{app.label}\n{app.package}"
        return f"{base}{t('card.running_hint')}" if running else base

    def _build_app_tile(self, app: AppInfo, running: bool = False) -> ft.Control:
        favorite = app.package in self.favorite_packages
        icon_slot = ft.Container(content=app_icon_control(app.icon_path, app.label, 36), width=36, height=36)
        badge = ft.Icon(ft.Icons.PLAY_CIRCLE, size=15, color=ft.Colors.PRIMARY, visible=running)
        label = ft.Text(value=f"★ {app.label}" if favorite else app.label)
        subtitle = ft.Text(value=self._tile_subtitle(app, running), size=12, color=ft.Colors.OUTLINE)
        launch_button = ft.FilledButton(
            content=t("action.launch"), on_click=self._bind(self.launch_app, app), visible=not running
        )
        show_button = ft.FilledButton(
            content=t("action.show"),
            icon=ft.Icons.OPEN_IN_FULL,
            on_click=self._bind(self.open_or_launch_app, app),
            visible=running,
        )
        tile = ft.ListTile(
            leading=icon_slot,
            title=ft.Row(
                controls=[badge, label],
                spacing=6,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            subtitle=subtitle,
            bgcolor=ft.Colors.PRIMARY_CONTAINER if running else None,
            # Rounded so the running row's highlight reads as a selected pill rather
            # than a full-bleed band across the list.
            shape=_shape(TILE_RADIUS),
            trailing=ft.Row(
                controls=[
                    ft.IconButton(
                        icon=ft.Icons.STAR if favorite else ft.Icons.STAR_BORDER,
                        tooltip=t("action.unfavorite") if favorite else t("action.favorite"),
                        on_click=self._bind(self.toggle_favorite, app),
                    ),
                    ft.IconButton(icon=ft.Icons.TUNE, tooltip=t("action.edit_profile"), on_click=self._bind(self.edit_profile, app)),
                    launch_button,
                    show_button,
                ],
                spacing=2,
                tight=True,
            ),
            on_click=self._bind(self.open_or_launch_app, app),
        )
        self.app_tiles.setdefault(app.package, []).append(
            AppCell(
                icon_slot=icon_slot,
                badge=badge,
                label=label,
                root=tile,
                section="table",
                tile=tile,
                subtitle=subtitle,
                launch_button=launch_button,
                show_button=show_button,
            )
        )
        return tile

    def _tile_subtitle(self, app: AppInfo, running: bool) -> str:
        profile_hint = self._profile_hint(app)
        subtitle = f"{app.package} · {profile_hint}" if profile_hint else app.package
        return t("tile.subtitle_running", subtitle=subtitle) if running else subtitle

    # -- favorites / profiles / launch ------------------------------------------------

    async def toggle_favorite(self, app: AppInfo) -> None:
        if not await self._require_device():
            return
        favorite = app.package not in self.favorite_packages
        self.store.set_favorite(self.favorite_device_key, app.package, favorite)
        if favorite:
            self.favorite_packages.add(app.package)
        else:
            self.favorite_packages.discard(app.package)
        self._render_apps()
        state = t("favorite.added") if favorite else t("favorite.removed")
        self._set_status(f"{app.label}: {state}")

    async def _confirm_dialog(self, title: str, content: ft.Control, confirm_label: str) -> bool:
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()

        def close(confirmed: bool) -> None:
            if not future.done():
                future.set_result(confirmed)
            self.page.pop_dialog()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(value=title),
            content=content,
            actions=[
                ft.TextButton(content=t("common.cancel"), on_click=lambda e: close(False)),
                ft.FilledButton(content=confirm_label, on_click=lambda e: close(True)),
            ],
        )
        self.page.show_dialog(dialog)
        return await future

    async def _show_settings_dialog(self, title: str, form: SettingsForm, *, profile: bool) -> bool:
        info_text = t("settings.profile_info") if profile else t("settings.global_info")
        return await self._confirm_dialog(
            title,
            ft.Container(
                width=780,
                height=580,
                content=ft.Column(controls=[ft.Text(value=info_text), form.content()], spacing=12, expand=True),
            ),
            t("common.save"),
        )

    async def edit_launcher_settings(self) -> None:
        """Launcher-wide preferences: language, default catalog view, M3 theming.

        Distinct from edit_global_settings, which edits scrcpy's own flags. The theme
        controls preview live while the dialog is open (a colour is hard to judge from
        a swatch alone), so cancelling has to put the real colours back.
        """
        language_dropdown = ft.Dropdown(
            value=get_language(),
            options=[ft.DropdownOption(key=code, text=name) for code, name in LANGUAGES.items()],
            width=220,
            dense=True,
        )
        view_toggle = ft.SegmentedButton(
            segments=view_mode_segments(),
            selected=[self._stored_view_mode()],
        )
        mode_toggle = ft.SegmentedButton(
            segments=[
                ft.Segment(value="system", icon=ft.Icons.BRIGHTNESS_AUTO, label=t("launcher_settings.theme_mode_system")),
                ft.Segment(value="light", icon=ft.Icons.LIGHT_MODE, label=t("launcher_settings.theme_mode_light")),
                ft.Segment(value="dark", icon=ft.Icons.DARK_MODE, label=t("launcher_settings.theme_mode_dark")),
            ],
            selected=[self.theme_mode],
        )
        accent_switch = ft.Switch(value=self.use_device_accent)
        picked_seed = stored_theme_seed(self.store)
        swatches: dict[str, ft.Container] = {}

        def picked_mode() -> str:
            selected = set(mode_toggle.selected or ())
            return next((mode for mode in THEME_MODES if mode in selected), self.theme_mode)

        def preview_theme() -> None:
            base = THEME_SEED_COLORS[picked_seed]
            use_accent = bool(accent_switch.value)
            for name, swatch in swatches.items():
                swatch.border = ft.Border.all(3, ft.Colors.ON_SURFACE) if name == picked_seed else None
            # Straight onto the page rather than through self.theme_mode: the choice is
            # only committed on save, and cancelling has to be able to put it back.
            self.page.theme_mode = THEME_MODES[picked_mode()]
            self._set_theme_seed_color_raw(
                self.device_seed_color if use_accent and self.device_seed_color else base
            )

        def pick_seed(name: str) -> None:
            nonlocal picked_seed
            picked_seed = name
            preview_theme()

        for name, color in THEME_SEED_COLORS.items():
            swatches[name] = ft.Container(
                width=36,
                height=36,
                bgcolor=color,
                border_radius=18,
                tooltip=t(f"launcher_settings.color_{name}"),
                border=ft.Border.all(3, ft.Colors.ON_SURFACE) if name == picked_seed else None,
                on_click=lambda e, chosen=name: pick_seed(chosen),
            )
        accent_switch.on_change = lambda e: preview_theme()
        mode_toggle.on_change = lambda e: preview_theme()

        def labelled(label: str, control: ft.Control) -> ft.Control:
            return ft.Row(
                controls=[
                    ft.Container(content=ft.Text(value=label, size=13), width=230),
                    ft.Container(content=control, expand=True, alignment=ft.Alignment.CENTER_RIGHT),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )

        saved = await self._confirm_dialog(
            t("launcher_settings.title"),
            ft.Container(
                # Wide enough for the three appearance segments to keep their labels
                # on one line in every locale.
                width=660,
                content=ft.Column(
                    controls=[
                        labelled(t("launcher_settings.language_label"), language_dropdown),
                        labelled(t("launcher_settings.default_view_label"), view_toggle),
                        ft.Divider(height=1),
                        labelled(t("launcher_settings.theme_mode_label"), mode_toggle),
                        ft.Text(value=t("launcher_settings.theme_label"), size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.PRIMARY),
                        ft.Row(controls=list(swatches.values()), spacing=10, wrap=True),
                        labelled(t("launcher_settings.device_accent_label"), accent_switch),
                        ft.Text(value=t("launcher_settings.device_accent_hint"), size=12, color=ft.Colors.OUTLINE),
                    ],
                    spacing=14,
                    tight=True,
                ),
            ),
            t("common.save"),
        )
        if not saved:
            self._apply_theme_colors()
            return

        view_mode = "list" if "list" in view_toggle.selected else "grid"
        use_accent = bool(accent_switch.value)
        accent_enabled_now = use_accent and not self.use_device_accent
        self.store.set_preference("default_view_mode", view_mode)
        self.store.set_preference("theme_seed", picked_seed)
        self.store.set_preference("theme_mode", picked_mode())
        self.store.set_preference("device_accent_enabled", use_accent)
        self.base_seed_color = THEME_SEED_COLORS[picked_seed]
        self.theme_mode = picked_mode()
        self.use_device_accent = use_accent
        if not use_accent:
            self.device_seed_color = ""
        # The default view is also what the user just said they want to look at, so
        # switch the catalogue over now instead of waiting for the next start.
        if view_mode != self.view_mode:
            self.view_mode = view_mode
            self.view_toggle.selected = [view_mode]
            self._slide_catalog_view(view_mode)
        self._apply_theme_colors()
        if accent_enabled_now and self.selected_device and self.selected_device.state == "device":
            asyncio.create_task(self._apply_device_accent_color(self.selected_device))
        await self._apply_language(language_dropdown.value or get_language())

    async def edit_global_settings(self) -> None:
        settings = dict(self.store.get_preference("global_settings", {}))
        extras = list(self.store.get_preference("global_extra_arguments", []))
        form = SettingsForm(settings, extras, profile=False)
        saved = await self._show_settings_dialog(t("settings.global_title"), form, profile=False)
        if not saved:
            return
        self.store.set_preference("global_settings", build_global_settings(form.raw_values()))
        self.store.set_preference("global_extra_arguments", form.extra_arguments())

    async def edit_profile(self, app: AppInfo) -> None:
        if not await self._require_device():
            return
        device = self.selected_device
        assert device is not None
        profile = self.store.get_profile(device.profile_key, app.package)
        form = SettingsForm(profile.settings, profile.extra_arguments, profile=True)
        form.force_stop_switch.value = profile.force_stop
        saved = await self._show_settings_dialog(t("settings.profile_title", label=app.label), form, profile=True)
        if not saved:
            return
        profile.settings = build_profile_settings(form.raw_values(), form.inherited_keys())
        profile.extra_arguments = form.extra_arguments()
        profile.force_stop = bool(form.force_stop_switch.value)
        profile.name = t("profile.configured_name") if profile.settings or profile.extra_arguments or profile.force_stop else ""
        self.store.save_profile(profile)
        self._render_apps()

    def _effective_settings(self, profile: LaunchProfile) -> tuple[dict[str, object], list[str]]:
        settings = dict(self.store.get_preference("global_settings", {}))
        settings.update(profile.settings)
        extras = list(self.store.get_preference("global_extra_arguments", [])) + profile.extra_arguments
        return settings, extras

    def _running_session_for(self, package: str) -> ScrcpySession | None:
        """Live session of `package` on the selected device, if any."""
        device = self.selected_device
        if device is None:
            return None
        for session in self.sessions.values():
            if session.info.package != package or not session.is_running:
                continue
            if session.info.device_key in {device.profile_key, device.serial}:
                return session
        return None

    def _running_packages(self) -> set[str]:
        device = self.selected_device
        if device is None:
            return set()
        return {
            session.info.package
            for session in self.sessions.values()
            if session.is_running and session.info.device_key in {device.profile_key, device.serial}
        }

    async def open_or_launch_app(self, app: AppInfo) -> None:
        """Focus the app's existing scrcpy window, or launch it if there is none.

        What a click on a lit-up tile should do — the running window is the thing
        the user is asking for, not a second copy of it.
        """
        session = self._running_session_for(app.package)
        if session is None:
            await self.launch_app(app)
            return
        if focus_process_window(session.pid):
            self._set_status(t("status.switching_to", label=app.label))
            return
        self._set_status(t("status.show_window_failed", label=app.label))

    async def launch_app(self, app: AppInfo) -> None:
        if not await self._require_device():
            return
        device = self.selected_device
        assert device is not None
        profile = self.store.get_profile(device.profile_key, app.package)
        settings, extras = self._effective_settings(profile)
        title = str(settings.get("window_title") or f"{app.label} — {device.model or device.serial}")
        settings["window_title"] = title
        try:
            arguments = build_scrcpy_arguments(
                device.serial,
                app.package,
                settings,
                force_stop=profile.force_stop,
                extra_arguments=extras,
                form_factor=device.form_factor,
            )
        except ValueError as error:
            await self._show_error(t("error.launch_failed_title"), str(error))
            return
        if device.profile_key not in self.virtual_device_ready:
            # On failure the server falls back to a plain display; retried next launch.
            code, _, _ = await self._adb_shell(device.serial, ENSURE_VIRTUAL_DEVICE_ASSOCIATION)
            if code == 0:
                self.virtual_device_ready.add(device.profile_key)
        session = ScrcpySession(
            str(self.scrcpy),
            arguments,
            device.serial,
            app.package,
            title,
            self._session_changed,
            device_key=device.profile_key,
        )
        self.sessions[session.info.session_id] = session
        await session.start()
        self._set_status(t("status.launching", label=app.label))
        if session.is_running:
            asyncio.create_task(self._focus_new_window(session))

    async def _focus_new_window(self, session: ScrcpySession) -> None:
        """Bring a just-launched scrcpy window forward once it actually exists.

        Windows would normally hand a new process's first window the foreground by
        itself, but scrcpy's window arrives seconds after the click that asked for it
        — long enough for that grace to lapse — so it can come up behind the launcher.
        Waiting here and then reusing the same path the "show" button takes is what
        makes launching an app land the user in it.
        """
        deadline = asyncio.get_running_loop().time() + LAUNCH_WINDOW_TIMEOUT
        while asyncio.get_running_loop().time() < deadline:
            if not session.is_running:
                return  # died before it ever drew anything; the log dialog explains why
            if process_windows(session.pid):
                focus_process_window(session.pid)
                return
            await asyncio.sleep(LAUNCH_WINDOW_POLL)

    # -- sessions ---------------------------------------------------------------------

    def _session_changed(self, info: SessionInfo) -> None:
        self._rebuild_session_list()
        # _changed fires on every stdout/stderr chunk, not just state transitions, so
        # only the packages whose running state actually moved are touched — and they
        # are updated in place, so starting an app does not flash the whole grid.
        running = self._running_packages()
        changed = running ^ self.rendered_running_packages
        if changed:
            self._refresh_app_cells(changed)
        self.page.update()

    def _rebuild_session_list(self) -> None:
        self.session_list.controls = [self._build_session_tile(session) for session in self.sessions.values()]

    def _build_session_tile(self, session: ScrcpySession) -> ft.Control:
        info = session.info
        # Match the icon the device list drew, so a session on the TV box doesn't read
        # as a phone. A session outliving the device's entry in the cache (reconnecting
        # over Wi-Fi mints a new serial) keeps the generic phone it has always had.
        form_factor = self.device_form_factors.get(info.device_serial, "")
        return ft.ListTile(
            leading=ft.Icon(FORM_FACTOR_ICONS.get(form_factor, ft.Icons.PHONE_ANDROID)),
            title=ft.Text(value=info.title),
            subtitle=ft.Text(value=f"{info.device_serial} · {info.package} · {info.state}", size=12, color=ft.Colors.OUTLINE),
            trailing=ft.Row(
                controls=[
                    ft.IconButton(icon=ft.Icons.ARTICLE, tooltip=t("session.log_tooltip"), on_click=self._bind(self._show_session_log, session)),
                    ft.IconButton(icon=ft.Icons.STOP_CIRCLE, tooltip=t("action.stop"), on_click=self._bind(session.stop)),
                ],
                spacing=0,
                tight=True,
            ),
        )

    async def stop_app(self, app: AppInfo) -> None:
        session = self._running_session_for(app.package)
        if session is None:
            self._set_status(t("status.app_not_running", label=app.label))
            return
        await session.stop()
        self._set_status(t("status.stopped", label=app.label))

    async def _show_session_log(self, session: ScrcpySession) -> None:
        await show_message(self.page, session.info.title, session.info.log[-4000:] or t("common.empty_placeholder"))

    # -- connect / pair -----------------------------------------------------------------

    async def pair_device(self) -> None:
        host_field = ft.TextField(label=t("device_manager.ip_label"), hint_text="192.168.1.25")
        port_field = ft.TextField(label=t("pair.port_label"), hint_text="37123")
        code_field = ft.TextField(label=t("pair.code_label"), password=True, can_reveal_password=True, max_length=6)
        if not await self._confirm_dialog(
            t("pair.title"),
            ft.Column(
                controls=[
                    ft.Text(value=t("pair.instructions")),
                    host_field,
                    port_field,
                    code_field,
                ],
                tight=True,
                width=380,
            ),
            t("action.pair"),
        ):
            return

        host = (host_field.value or "").strip()
        port = (port_field.value or "").strip()
        code = (code_field.value or "").strip()
        if not host or not port or len(code) < 4:
            await self._show_error(t("error.pair_invalid_title"), t("error.pair_invalid_details"))
            return
        endpoint = f"{host}:{port}"
        self._set_status(t("status.pairing"))
        result_code, out, err = await run_tool(self.adb, ["pair", endpoint, code])
        if result_code:
            await self._show_error(t("error.pair_failed_title"), err or out)
            return
        self._set_status(t("status.pair_done_searching_mdns"))
        result_code, out, _err = await run_tool(self.adb, ["mdns", "services"])
        services = parse_mdns_services(out) if not result_code else []
        connect_service = next((item for item in services if item["service"] == "_adb-tls-connect._tcp"), None)
        if connect_service:
            await self._connect_endpoint(connect_service["endpoint"])
            return
        await show_message(
            self.page,
            t("pair.done_title"),
            t("pair.done_no_mdns_message"),
        )
        await self.refresh_devices()

    async def _connect_endpoint(self, endpoint: str) -> None:
        if ":" not in endpoint:
            await self._show_error(t("error.invalid_address_title"), t("error.invalid_address_details"))
            return
        self._set_status(t("status.connecting_to", endpoint=endpoint))
        code, out, err = await run_tool(self.adb, ["connect", endpoint])
        if code or "failed" in (out + err).lower():
            await self._show_error(t("error.adb_connect_failed"), err or out)
            return
        self.store.remember_endpoint(endpoint)
        self._set_status(t("status.connected", endpoint=endpoint))
        await self.refresh_devices()

    def close(self) -> None:
        for session in self.sessions.values():
            if session.info.state in {"running", "stopping"}:
                asyncio.create_task(session.stop())
        self.store.close()


async def main_async(page: ft.Page, project_root: Path) -> None:
    # Read ahead of LauncherApp (which owns the long-lived store connection) so the
    # very first frame is already the user's theme and language: the bootstrap dialogs
    # below run before the app exists and would otherwise open on the defaults.
    startup_store = ProfileStore(_data_directory() / "profiles.sqlite3")
    stored_language = startup_store.get_preference("language", "")
    first_run = not (isinstance(stored_language, str) and stored_language in LANGUAGES)
    theme_mode = stored_theme_mode(startup_store)
    seed_color = stored_seed_color(startup_store)

    # The client starts on Flutter's stock theme and only then receives ours, and a
    # theme change is animated by default — which is what made the very first frames
    # visibly morph (square dialog corners rounding themselves, colours drifting).
    # Nothing is on screen worth cross-fading yet, so the opening theme lands instantly
    # and the transition is handed back over once the app is up (see below).
    page.theme_animation_style = ft.AnimationStyle.no_animation()
    page.theme_mode = THEME_MODES[theme_mode]
    page.theme = build_theme(seed_color)
    page.dark_theme = build_theme(seed_color)
    # Hides and sizes the window in this same first update, so the client's first paint
    # is already the final size and the user never sees it resize itself.
    configure_window(page, theme_mode)
    page.update()

    if not first_run:
        set_language(stored_language)
    else:
        # No stored choice means a first run: guess from the OS, then let the user
        # confirm or override it. The answer is persisted, so this asks exactly once.
        set_language(detect_system_language())
        # Anything that waits for an answer has to be on screen to be answered.
        await reveal_window(page)
        chosen_language = await ask_language(page)
        set_language(chosen_language)
        startup_store.set_preference("language", chosen_language)
    startup_store.close()

    if missing_scrcpy_files(project_root):
        # Only the download flow talks to the user; when scrcpy is already unpacked
        # ensure_scrcpy is silent and the window can stay hidden a moment longer.
        await reveal_window(page)
    if not await ensure_scrcpy(project_root, page):
        await page.window.close()
        return

    try:
        launcher = LauncherApp(page, project_root, first_run=first_run)
    except FileNotFoundError as error:
        await reveal_window(page)
        await show_message(page, "Scrcpy Launcher", str(error))
        await page.window.close()
        return
    except Exception:
        # Never leave a hidden window behind: without this the process would live on
        # with nothing on screen and no way to reach it.
        await reveal_window(page)
        raise

    def on_window_event(e: ft.WindowEvent) -> None:
        if e.type == ft.WindowEventType.CLOSE:
            launcher.close()

    page.window.on_event = on_window_event
    # Every theme change from here on is a reaction to something the user did (picking
    # a base theme, hovering a device, connecting one), so those go back to fading.
    page.theme_animation_style = None
    # The finished UI is the first thing the window ever shows.
    await reveal_window(page)
    await launcher.start()


def run_application(project_root: Path) -> int:
    # FLET_APP_HIDDEN, not FLET_APP: the desktop client otherwise creates and shows
    # its window the moment it starts — before any Python code runs — so a bare
    # untitled window flashes in the top-left corner while the app is still loading.
    # Hiding it from Python is too late; this starts the client with the window
    # already hidden and reveal_window brings it up when the UI is ready.
    # FLET_FORCE_WEB_SERVER still overrides this (see CLAUDE.md).
    ft.run(
        main=partial(main_async, project_root=project_root),
        name="Scrcpy Launcher",
        view=ft.AppView.FLET_APP_HIDDEN,
    )
    return 0
