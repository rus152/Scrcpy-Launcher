from __future__ import annotations

import html
import shutil
import tempfile
from collections import deque
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, QProcess, QRunnable, QSize, QStandardPaths, QThreadPool, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QMenu,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .adb import (
    bundled_tools,
    extract_package_path,
    parse_adb_devices,
    parse_mdns_services,
    parse_pm_package_versions,
    parse_pm_packages,
)
from .bootstrap import ensure_scrcpy
from .icons import cache_filename, extract_icon_from_apk
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
from .models import AppInfo, DeviceInfo, LaunchProfile
from .options import OPTION_SPECS, build_scrcpy_arguments, parse_extra_arguments
from .profiles import ProfileStore
from .session import ScrcpySession


def generic_icon() -> QIcon:
    pixmap = QPixmap(144, 144)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#4f86d9"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(12, 12, 120, 120, 28, 28)
    painter.setPen(QColor("white"))
    font = QFont()
    font.setPixelSize(48)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "A")
    painter.end()
    return QIcon(pixmap)


class ProcessCall(QObject):
    """One short-lived, shell-free QProcess request."""

    def __init__(self, program: Path, arguments: list[str], done: Callable[[int, str, str], None], parent: QWidget) -> None:
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProgram(str(program))
        self.process.setArguments(arguments)
        self._done = done
        self._called = False
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._errored)
        self.process.start()

    def _finished(self, code: int, _status: QProcess.ExitStatus) -> None:
        if self._called:
            return
        self._called = True
        stdout = bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        stderr = bytes(self.process.readAllStandardError()).decode(errors="replace")
        self._done(code, stdout, stderr)
        self.deleteLater()

    def _errored(self, _error: QProcess.ProcessError) -> None:
        if not self._called and self.process.state() == QProcess.ProcessState.NotRunning:
            self._called = True
            self._done(1, "", self.process.errorString())
            self.deleteLater()


class IconExtractionSignals(QObject):
    finished = pyqtSignal(object, str)


class IconExtractionJob(QRunnable):
    """Parse a downloaded APK away from the Qt GUI thread."""

    def __init__(self, app: AppInfo, apk_path: Path, cache_path: Path) -> None:
        super().__init__()
        self.app = app
        self.apk_path = apk_path
        self.cache_path = cache_path
        self.signals = IconExtractionSignals()

    def run(self) -> None:
        result = extract_icon_from_apk(self.apk_path, self.cache_path)
        self.signals.finished.emit(self.app, str(result) if result else "")


class SettingsDialog(QDialog):
    def __init__(self, settings: dict[str, object], extra_arguments: list[str], *, profile: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Профиль запуска" if profile else "Глобальные параметры scrcpy")
        self.resize(760, 620)
        self.profile_mode = profile
        self.widgets: dict[str, QWidget] = {}
        self.inherit_widgets: dict[str, QCheckBox] = {}
        layout = QVBoxLayout(self)
        info = QLabel(
            "В профиле флажок «Глобальные» означает: взять это значение из общих параметров. "
            "Снимите флажок, чтобы задать индивидуальное значение для приложения. "
            if profile
            else "Пустое поле использует значение scrcpy по умолчанию. Виртуальный дисплей и выбранное приложение добавляются автоматически."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        tabs = QTabWidget()
        groups: dict[str, QFormLayout] = {}
        pages: dict[str, QWidget] = {}
        for spec in OPTION_SPECS:
            page = pages.get(spec.group)
            if page is None:
                page = QWidget()
                pages[spec.group] = page
                groups[spec.group] = QFormLayout(page)
                tabs.addTab(page, spec.group)
            if spec.kind == "bool":
                widget: QWidget = QCheckBox()
                widget.setChecked(bool(settings.get(spec.key, False)))
            elif spec.kind == "choice":
                combo = QComboBox()
                combo.addItem("")
                combo.addItems(spec.choices)
                combo.setCurrentText(str(settings.get(spec.key, "")))
                widget = combo
            else:
                edit = QLineEdit(str(settings.get(spec.key, "")))
                edit.setPlaceholderText(spec.placeholder)
                if spec.kind == "int":
                    edit.setInputMask("0000000000;_")
                widget = edit
            if profile and spec.windows_supported:
                field = widget
                wrapper = QWidget()
                wrapper_layout = QHBoxLayout(wrapper)
                wrapper_layout.setContentsMargins(0, 0, 0, 0)
                inherit = QCheckBox("Глобальные")
                inherit.setToolTip("Использовать значение из глобальных параметров")
                inherit.setChecked(spec.key not in settings)
                field.setEnabled(not inherit.isChecked())
                inherit.toggled.connect(lambda checked, target=field: target.setEnabled(not checked))
                wrapper_layout.addWidget(field, 1)
                wrapper_layout.addWidget(inherit)
                self.inherit_widgets[spec.key] = inherit
                widget = wrapper
            if not spec.windows_supported:
                widget.setEnabled(False)
                label = f"{spec.label} (недоступно в безопасном Windows-режиме)"
            else:
                label = spec.label
            groups[spec.group].addRow(label, widget)
            self.widgets[spec.key] = field if profile and spec.windows_supported else widget
        layout.addWidget(tabs, 1)
        self.force_stop = QCheckBox("Принудительно завершать приложение перед запуском")
        self.force_stop.setVisible(profile)
        layout.addWidget(self.force_stop)
        extra_group = QGroupBox("Экспертные аргументы (один аргумент или группа аргументов на строку)")
        extra_layout = QVBoxLayout(extra_group)
        self.extra = QTextEdit("\n".join(extra_arguments))
        self.extra.setPlaceholderText("--window-title=Моё окно\n# --serial, --new-display и --start-app запрещены")
        self.extra.setFixedHeight(90)
        extra_layout.addWidget(self.extra)
        layout.addWidget(extra_group)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> tuple[dict[str, object], list[str]]:
        values: dict[str, object] = {}
        for spec in OPTION_SPECS:
            if not spec.windows_supported:
                continue
            if self.profile_mode and spec.windows_supported and self.inherit_widgets[spec.key].isChecked():
                continue
            widget = self.widgets[spec.key]
            if spec.kind == "bool":
                value: object = widget.isChecked()  # type: ignore[attr-defined]
            elif spec.kind == "choice":
                value = widget.currentText()  # type: ignore[attr-defined]
            else:
                value = widget.text().strip()  # type: ignore[attr-defined]
            if self.profile_mode or value not in (False, "", None):
                values[spec.key] = value
        return values, parse_extra_arguments(self.extra.toPlainText())


class PairDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pair: беспроводная отладка Android")
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "На телефоне: Параметры разработчика → Беспроводная отладка → «Подключить устройство с кодом». "
            "Введите IP, порт сопряжения и код. Порт Pair отличается от порта подключения."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        form = QFormLayout()
        self.host = QLineEdit()
        self.host.setPlaceholderText("192.168.1.25")
        self.port = QLineEdit()
        self.port.setPlaceholderText("37123")
        self.code = QLineEdit()
        self.code.setMaxLength(6)
        self.code.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("IP-адрес:", self.host)
        form.addRow("Порт Pair:", self.port)
        form.addRow("Код сопряжения:", self.code)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def endpoint(self) -> str:
        return f"{self.host.text().strip()}:{self.port.text().strip()}"


class MainWindow(QMainWindow):
    def __init__(self, project_root: Path) -> None:
        super().__init__()
        self.project_root = project_root
        self.adb, self.scrcpy = bundled_tools(project_root)
        data_directory = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
        self.store = ProfileStore(data_directory / "profiles.sqlite3")
        self.cache_directory = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation))
        self.devices: dict[str, DeviceInfo] = {}
        self.apps: list[AppInfo] = []
        self.selected_device: DeviceInfo | None = None
        self.sessions: dict[str, ScrcpySession] = {}
        self.icon_queue: deque[AppInfo] = deque()
        self.icon_pool = QThreadPool(self)
        self.icon_pool.setMaxThreadCount(4)
        self.icon_jobs: list[IconExtractionJob] = []
        self.active_icon_loads = 0
        self.max_icon_loads = 6
        self.device_icon_batches: deque[list[str]] = deque()
        self.device_icon_loader_active = False
        self.device_icon_device_serial = ""
        self.device_icon_active_batches = 0
        self.device_icon_completed = 0
        self.device_icon_total = 0
        self.max_device_icon_workers = 3
        self.device_icon_cache_key = ""
        self.device_package_versions: dict[str, str] = {}
        self.device_cached_records: dict[str, DeviceAppRecord] = {}
        self.device_fully_cached_packages: set[str] = set()
        self.favorite_device_key = ""
        self.favorite_packages: set[str] = set()
        self._setup_ui()
        self.refresh_devices()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        for session in self.sessions.values():
            if session.info.state in {"running", "stopping"}:
                session.stop()
        self.icon_pool.clear()
        self.icon_pool.waitForDone(1500)
        self.store.close()
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        if hasattr(self, "grid_scroll"):
            self._update_grid_heights()

    def _setup_ui(self) -> None:
        self.setWindowTitle("Scrcpy Launcher")
        self.resize(1120, 720)
        self.setMinimumSize(820, 520)
        self.setStyleSheet(
            """
            QMainWindow, QDialog { background: #292b2f; color: #f1f3f5; }
            QMenu::item:selected { background: #3b3e43; }
            QMenu { background: #292b2f; color: #f1f3f5; border: 1px solid #4b4f55; }
            QPushButton, QToolButton { background: #34373b; border: 1px solid #565a60; border-radius: 3px; color: #f1f3f5; padding: 5px 10px; }
            QPushButton:hover, QToolButton:hover { background: #45494e; }
            QPushButton:pressed, QToolButton:pressed { background: #285b36; border-color: #4b9b5f; }
            QLineEdit, QComboBox, QTextEdit { background: #303236; border: 1px solid #555960; border-radius: 2px; color: #f1f3f5; padding: 4px 7px; }
            QTableWidget, QListWidget { background: #292b2f; alternate-background-color: #303236; border: 1px solid #484c52; color: #f1f3f5; }
            QTableWidget::item:selected, QListWidget::item:selected { background: #365f9c; }
            QHeaderView::section { background: #34363a; color: #f1f3f5; border: 0; border-right: 1px solid #50545a; padding: 5px; }
            QGroupBox { border: 1px solid #555960; border-radius: 5px; margin-top: 12px; color: #e8eaed; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QLabel { color: #d7dadf; }
            QScrollBar:vertical { background: #292b2f; width: 10px; } QScrollBar::handle:vertical { background: #555a60; min-height: 24px; }
            """
        )
        self._create_device_manager()
        self._create_session_manager()
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(4, 2, 4, 4)
        root.setSpacing(5)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(210)
        self.device_combo.setPlaceholderText("Выберите устройство")
        self.device_combo.currentIndexChanged.connect(self._device_combo_selected)
        devices_button = QToolButton()
        devices_button.setText("Устройства")
        devices_button.setToolTip("Диспетчер устройств")
        devices_button.clicked.connect(self.show_device_manager)
        refresh_button = QToolButton()
        refresh_button.setText("↻")
        refresh_button.setToolTip("Обновить устройства")
        refresh_button.clicked.connect(self.refresh_devices)
        toolbar.addWidget(self.device_combo)
        toolbar.addWidget(refresh_button)
        toolbar.addWidget(devices_button)
        toolbar.addStretch(1)
        sessions_button = QToolButton()
        sessions_button.setText("Сеансы")
        sessions_button.setToolTip("Открыть активные сеансы scrcpy")
        sessions_button.clicked.connect(self.show_session_manager)
        toolbar.addWidget(sessions_button)
        global_button = QToolButton()
        global_button.setText("Параметры")
        global_button.setToolTip("Глобальные параметры scrcpy")
        global_button.clicked.connect(self.edit_global_settings)
        toolbar.addWidget(global_button)
        root.addLayout(toolbar)
        app_tools = QHBoxLayout()
        app_tools.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Фильтр")
        self.search.setMaximumWidth(235)
        self.search.textChanged.connect(self._filter_changed)
        self.show_system = QCheckBox("Системные пакеты")
        self.show_system.toggled.connect(self._filter_changed)
        self.app_count = QLabel("0 всего пакетов")
        self.grid_button = QToolButton()
        self.grid_button.setText("▦")
        self.grid_button.setToolTip("Плитки")
        self.grid_button.clicked.connect(lambda: self._set_app_view(0))
        self.list_button = QToolButton()
        self.list_button.setText("☷")
        self.list_button.setToolTip("Таблица")
        self.list_button.clicked.connect(lambda: self._set_app_view(1))
        app_tools.addWidget(self.search)
        app_tools.addWidget(self.show_system)
        app_tools.addWidget(self.app_count)
        app_tools.addStretch(1)
        app_tools.addWidget(self.grid_button)
        app_tools.addWidget(self.list_button)
        root.addLayout(app_tools)
        self.app_stack = QStackedWidget()
        self.grid_scroll = QScrollArea()
        self.grid_scroll.setWidgetResizable(True)
        self.grid_scroll.setStyleSheet("QScrollArea { border: 0; background: #292b2f; }")
        grid_page = QWidget()
        grid_page.setStyleSheet("background: #292b2f;")
        grid_layout = QVBoxLayout(grid_page)
        grid_layout.setContentsMargins(4, 4, 4, 4)
        grid_layout.setSpacing(4)

        self.favorite_group_label = QLabel("★ Избранные")
        favorite_font = self.favorite_group_label.font()
        favorite_font.setBold(True)
        favorite_font.setPointSize(favorite_font.pointSize() + 1)
        self.favorite_group_label.setFont(favorite_font)
        self.favorite_group_label.setStyleSheet("color: #f2c94c; padding: 4px 4px 2px 4px;")
        self.favorite_empty_label = QLabel("Нет избранных приложений")
        self.favorite_empty_label.setStyleSheet("color: #8f949c; padding: 8px 12px 12px 12px;")
        self.favorite_grid = QListWidget()
        self.app_grid = QListWidget()

        def configure_grid(grid: QListWidget) -> None:
            grid.setViewMode(QListView.ViewMode.IconMode)
            grid.setResizeMode(QListView.ResizeMode.Adjust)
            grid.setMovement(QListView.Movement.Static)
            grid.setIconSize(QSize(56, 56))
            grid.setGridSize(QSize(96, 112))
            grid.setSpacing(8)
            grid.setWordWrap(True)
            grid.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            grid.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            grid.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            grid.setStyleSheet("QListWidget { border: 0; background: transparent; }")
            grid.currentItemChanged.connect(
                lambda current, _previous, source=grid: self._grid_selection_changed(source, current)
            )
            grid.itemDoubleClicked.connect(
                lambda item: self.launch_app_by_package(str(item.data(Qt.ItemDataRole.UserRole)))
            )
            grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            grid.customContextMenuRequested.connect(
                lambda position, source=grid: self._app_context_menu(source, position)
            )

        configure_grid(self.favorite_grid)
        configure_grid(self.app_grid)
        self.all_group_label = QLabel("Все")
        all_font = self.all_group_label.font()
        all_font.setBold(True)
        all_font.setPointSize(all_font.pointSize() + 1)
        self.all_group_label.setFont(all_font)
        self.all_group_label.setStyleSheet("padding: 8px 4px 2px 4px;")
        grid_layout.addWidget(self.favorite_group_label)
        grid_layout.addWidget(self.favorite_empty_label)
        grid_layout.addWidget(self.favorite_grid)
        grid_layout.addWidget(self.all_group_label)
        grid_layout.addWidget(self.app_grid)
        grid_layout.addStretch(1)
        self.grid_scroll.setWidget(grid_page)
        self.app_stack.addWidget(self.grid_scroll)
        self.app_table = QTableWidget(0, 5)
        self.app_table.setHorizontalHeaderLabels(["", "Приложение", "Пакет", "Профиль", ""])
        self.app_table.setIconSize(QPixmap(32, 32).size())
        self.app_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.app_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.app_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.app_table.customContextMenuRequested.connect(self._app_table_context_menu)
        self.app_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.app_table.verticalHeader().setDefaultSectionSize(44)
        header = self.app_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.app_stack.addWidget(self.app_table)
        root.addWidget(self.app_stack, 1)
        self.status = QLabel("Готово")
        root.addWidget(self.status)

    def _create_device_manager(self) -> None:
        self.device_dialog = QDialog(self)
        self.device_dialog.setWindowTitle("Диспетчер устройств")
        self.device_dialog.resize(960, 600)
        layout = QVBoxLayout(self.device_dialog)
        controls = QHBoxLayout()
        self.device_ip = QLineEdit()
        self.device_ip.setPlaceholderText("IP-адрес")
        self.device_port = QLineEdit()
        self.device_port.setPlaceholderText("Порт")
        connect = QPushButton("Подключиться")
        connect.clicked.connect(self._connect_from_manager)
        pair = QPushButton("Сопряжение")
        pair.clicked.connect(self.pair_device)
        refresh = QToolButton()
        refresh.setText("↻")
        refresh.setToolTip("Обновить список устройств")
        refresh.clicked.connect(self.refresh_devices)
        controls.addWidget(self.device_ip, 2)
        controls.addWidget(self.device_port)
        controls.addWidget(connect)
        controls.addWidget(pair)
        controls.addStretch(1)
        controls.addWidget(refresh)
        layout.addLayout(controls)
        self.device_table = QTableWidget(0, 5)
        self.device_table.setHorizontalHeaderLabels(["ID", "Серийный номер", "Имя", "Версия Android", "Статус"])
        self.device_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.device_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.device_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.device_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.device_table.itemSelectionChanged.connect(self._device_selected)
        layout.addWidget(self.device_table, 1)
        self.device_info = QLabel("Устройство не выбрано")
        self.device_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.device_info)

    def _create_session_manager(self) -> None:
        self.session_dialog = QDialog(self)
        self.session_dialog.setWindowTitle("Сеансы scrcpy")
        self.session_dialog.resize(900, 360)
        session_layout = QVBoxLayout(self.session_dialog)
        self.session_table = QTableWidget(0, 5)
        self.session_table.setHorizontalHeaderLabels(["Окно", "Устройство", "Пакет", "Статус", "Журнал"])
        self.session_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.session_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.session_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.session_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        session_layout.addWidget(self.session_table)
        stop = QPushButton("Остановить выбранный сеанс")
        stop.clicked.connect(self.stop_selected_session)
        session_layout.addWidget(stop, alignment=Qt.AlignmentFlag.AlignRight)

    def show_device_manager(self) -> None:
        self.device_dialog.show()
        self.device_dialog.raise_()
        self.device_dialog.activateWindow()

    def show_session_manager(self) -> None:
        self.session_dialog.show()
        self.session_dialog.raise_()
        self.session_dialog.activateWindow()

    def _set_app_view(self, index: int) -> None:
        self.app_stack.setCurrentIndex(index)

    def _run(self, program: Path, arguments: list[str], done: Callable[[int, str, str], None]) -> None:
        ProcessCall(program, arguments, done, self)

    def _set_status(self, message: str) -> None:
        self.status.setText(message)

    def refresh_devices(self) -> None:
        self._set_status("Поиск устройств ADB…")
        self._run(self.adb, ["devices", "-l"], self._devices_received)

    def _devices_received(self, code: int, stdout: str, stderr: str) -> None:
        if code:
            self._show_error("Не удалось получить устройства ADB", stderr or stdout)
            return
        previously_selected = self.selected_device.serial if self.selected_device else ""
        self.devices = {device.serial: device for device in parse_adb_devices(stdout)}
        self.device_table.setRowCount(0)
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        for row, device in enumerate(self.devices.values()):
            self.device_table.insertRow(row)
            model = device.model or device.product or "Неизвестное устройство"
            values = [device.serial, "…", model, "Android …" if device.state == "device" else "—", self._state_label(device.state)]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, device.serial)
                self.device_table.setItem(row, column, item)
            self.device_combo.addItem(f"{model} ({device.transport})", device.serial)
            if device.state == "device":
                self._run(self.adb, ["-s", device.serial, "shell", "getprop", "ro.build.version.release"], lambda c, o, e, serial=device.serial: self._android_version_received(serial, c, o, e))
                self._run(self.adb, ["-s", device.serial, "shell", "getprop", "ro.serialno"], lambda c, o, e, serial=device.serial: self._stable_id_received(serial, c, o, e))
        self.device_combo.blockSignals(False)
        if previously_selected in self.devices:
            self._select_device(previously_selected)
        self._set_status(f"Найдено устройств: {len(self.devices)}")

    def _android_version_received(self, serial: str, code: int, stdout: str, _stderr: str) -> None:
        device = self.devices.get(serial)
        if not device or code:
            return
        device.android_version = stdout.strip() or "?"
        for row in range(self.device_table.rowCount()):
            if self.device_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == serial:
                self.device_table.item(row, 3).setText(f"Android {device.android_version}")

    def _stable_id_received(self, serial: str, code: int, stdout: str, _stderr: str) -> None:
        device = self.devices.get(serial)
        if device and not code and stdout.strip() not in {"", "unknown"}:
            old_key = device.profile_key
            device.stable_id = stdout.strip()
            if self.selected_device and self.selected_device.serial == serial and old_key != device.profile_key:
                self.store.merge_favorites(old_key, device.profile_key)
                self.favorite_device_key = device.profile_key
                self.favorite_packages = self.store.favorites(self.favorite_device_key)
                if hasattr(self, "app_grid"):
                    self._render_apps()
            for row in range(self.device_table.rowCount()):
                if self.device_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == serial:
                    self.device_table.item(row, 1).setText(device.stable_id)

    def _device_selected(self) -> None:
        rows = self.device_table.selectionModel().selectedRows()
        if not rows:
            return
        serial = self.device_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        self._select_device(serial)

    def _device_combo_selected(self, _index: int) -> None:
        serial = self.device_combo.currentData()
        if serial:
            self._select_device(str(serial))

    def _select_device(self, serial: str) -> None:
        device = self.devices.get(serial)
        if not device:
            return
        same_device = self.selected_device is not None and self.selected_device.serial == serial
        self.selected_device = device
        self.device_info.setText(f"{device.model or device.serial} — {self._state_label(device.state)}")
        combo_index = self.device_combo.findData(serial)
        if combo_index >= 0 and combo_index != self.device_combo.currentIndex():
            self.device_combo.blockSignals(True)
            self.device_combo.setCurrentIndex(combo_index)
            self.device_combo.blockSignals(False)
        if device.state != "device":
            self.apps = []
            self.favorite_device_key = device.profile_key
            self.favorite_packages = self.store.favorites(self.favorite_device_key)
            self._render_apps()
            self._set_status(f"Устройство {device.serial}: {device.state}")
            return
        if not same_device or not self.apps:
            self.load_apps(device)

    @staticmethod
    def _state_label(state: str) -> str:
        return {"device": "Онлайн", "offline": "Офлайн", "unauthorized": "Не авторизован"}.get(state, state)

    def load_apps(self, device: DeviceInfo) -> None:
        self.icon_queue.clear()
        self.device_icon_batches.clear()
        self.device_icon_loader_active = False
        self.device_icon_device_serial = device.serial
        self.device_icon_active_batches = 0
        self.device_icon_completed = 0
        self.device_icon_total = 0
        self.device_icon_cache_key = device.profile_key
        self.device_package_versions = {}
        self.device_cached_records = {}
        self.device_fully_cached_packages = set()
        self.favorite_device_key = device.profile_key
        self.favorite_packages = self.store.favorites(self.favorite_device_key)
        self.apps = []
        self._render_apps()
        self._set_status("Получение списка приложений…")
        self._run(
            self.adb,
            ["-s", device.serial, "shell", "am", "get-current-user"],
            lambda c, o, e, source=device: self._current_user_received(source, c, o, e),
        )

    def _current_user_received(self, device: DeviceInfo, code: int, stdout: str, _stderr: str) -> None:
        if not self.selected_device or self.selected_device.serial != device.serial:
            return
        current_user = stdout.strip() if not code and stdout.strip().isdigit() else "0"
        self._run(
            self.adb,
            ["-s", device.serial, "shell", "pm", "list", "packages", "--show-versioncode", "--user", current_user],
            lambda c, o, e, source=device, user=current_user: self._all_packages_received(source, user, c, o, e),
        )

    def _all_packages_received(self, device: DeviceInfo, current_user: str, code: int, stdout: str, stderr: str) -> None:
        if code:
            self._show_error("Не удалось получить список приложений", stderr or stdout)
            return
        package_versions = parse_pm_package_versions(stdout)
        self._run(
            self.adb,
            ["-s", device.serial, "shell", "pm", "list", "packages", "-3", "--user", current_user],
            lambda c, o, e, source=device, versions=package_versions: self._user_packages_received(source, versions, c, o, e),
        )

    def _user_packages_received(self, device: DeviceInfo, package_versions: dict[str, str], code: int, stdout: str, _stderr: str) -> None:
        if not self.selected_device or self.selected_device.serial != device.serial:
            return
        user_packages = parse_pm_packages(stdout) if not code else set()
        self.device_package_versions = package_versions
        loaded_records = load_cached_app_records(self.cache_directory, self.device_icon_cache_key)
        self.device_cached_records = {package: record for package, record in loaded_records.items() if package in package_versions}
        self.apps = []
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
            self.apps.append(app)
        self.apps.sort(key=lambda app: (app.label.casefold(), app.package))
        self._render_apps()
        self._set_status(f"Приложений: {len(self.apps)}")
        self._start_device_icon_loader(device)

    def _start_device_icon_loader(self, device: DeviceInfo) -> None:
        if self.apps and len(self.device_fully_cached_packages) == len(self.apps):
            self._set_status(f"Приложений: {len(self.apps)} — иконки загружены из кэша")
            return
        helper = self.project_root / "launcher" / "resources" / "icon-dumper.dex"
        if not helper.is_file():
            self._start_legacy_icon_loader(device, "DEX-помощник не найден")
            return
        self.device_icon_loader_active = True
        self._set_status("Подготовка Android-загрузчика иконок…")
        self._run(
            self.adb,
            ["-s", device.serial, "shell", "mkdir", "-p", "/data/local/tmp/scrcpy-launcher"],
            lambda c, o, e, source=device, local=helper: self._device_icon_directory_ready(source, local, c, o, e),
        )

    def _device_icon_directory_ready(self, device: DeviceInfo, helper: Path, code: int, stdout: str, stderr: str) -> None:
        if code:
            self._start_legacy_icon_loader(device, stderr or stdout)
            return
        self._run(
            self.adb,
            ["-s", device.serial, "push", "-q", str(helper), HELPER_REMOTE_PATH],
            lambda c, o, e, source=device: self._device_icon_helper_pushed(source, c, o, e),
        )

    def _device_icon_helper_pushed(self, device: DeviceInfo, code: int, stdout: str, stderr: str) -> None:
        if code:
            self._start_legacy_icon_loader(device, stderr or stdout)
            return
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
        self.device_icon_total = len(arguments)
        self.device_icon_completed = 0
        self.device_icon_active_batches = 0
        self.device_icon_batches = deque(package_batches(arguments, 40))
        self._pump_device_icon_batches(device)

    def _pump_device_icon_batches(self, device: DeviceInfo) -> None:
        if not self.selected_device or self.selected_device.serial != device.serial:
            self.device_icon_loader_active = False
            return
        if not self.device_icon_loader_active:
            return
        while self.device_icon_batches and self.device_icon_active_batches < self.max_device_icon_workers:
            batch = self.device_icon_batches.popleft()
            self.device_icon_active_batches += 1
            self._run(
                self.adb,
                [
                    "-s",
                    device.serial,
                    "shell",
                    f"CLASSPATH={HELPER_REMOTE_PATH}",
                    "app_process",
                    "/system/bin",
                    HELPER_MAIN_CLASS,
                    *batch,
                ],
                lambda c, o, e, source=device, count=len(batch): self._device_icon_batch_received(source, count, c, o, e),
            )
        self._set_status(
            f"Android обрабатывает иконки: {self.device_icon_completed}/{self.device_icon_total} "
            f"({self.device_icon_active_batches} параллельных процесса)"
        )
        if not self.device_icon_batches and self.device_icon_active_batches == 0:
            self.device_icon_loader_active = False
            self.apps.sort(key=lambda app: (app.label.casefold(), app.package))
            self._render_apps()
            self._set_status(f"Приложений: {len(self.apps)} — иконки загружены через Android")
            self._schedule_visible_icons()

    def _device_icon_batch_received(self, device: DeviceInfo, batch_size: int, code: int, stdout: str, stderr: str) -> None:
        if not self.selected_device or self.selected_device.serial != device.serial:
            return
        if not self.device_icon_loader_active:
            return
        self.device_icon_active_batches = max(0, self.device_icon_active_batches - 1)
        self.device_icon_completed += batch_size
        records = parse_device_app_records(stdout)
        if not records:
            self._start_legacy_icon_loader(device, stderr or stdout)
            return
        by_package = {app.package: app for app in self.apps}
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
            self.device_cached_records[record.package] = DeviceAppRecord(
                record.package,
                app.label,
                app.is_system,
                record.revision,
            )
            if rendered and record.revision == self.device_package_versions.get(record.package, ""):
                self.device_fully_cached_packages.add(record.package)
        write_cached_app_records(self.cache_directory, self.device_icon_cache_key, self.device_cached_records)
        self.apps.sort(key=lambda app: (app.label.casefold(), app.package))
        self._render_apps()
        self._pump_device_icon_batches(device)

    def _start_legacy_icon_loader(self, device: DeviceInfo, reason: str) -> None:
        self.device_icon_loader_active = False
        self.device_icon_batches.clear()
        self.device_icon_active_batches = 0
        self._set_status("Android-помощник недоступен, используется ADB APK fallback")
        self._schedule_visible_icons()

    def _filtered_apps(self) -> list[AppInfo]:
        query = self.search.text().casefold().strip()
        applications = [
            app for app in self.apps
            if (self.show_system.isChecked() or not app.is_system)
            and (not query or query in app.package.casefold() or query in app.label.casefold())
        ]
        return sorted(applications, key=lambda app: (app.label.casefold(), app.package))

    def _render_apps(self) -> None:
        selected = self._current_app() if hasattr(self, "app_stack") else None
        selected_package = selected.package if selected else ""
        visible = self._filtered_apps()
        favorites = [app for app in visible if app.package in self.favorite_packages]
        table_apps = sorted(
            visible,
            key=lambda app: (app.package not in self.favorite_packages, app.label.casefold(), app.package),
        )
        self.app_count.setText(f"{len(visible)} пакетов · {len(favorites)} избранных")
        self.favorite_grid.blockSignals(True)
        self.app_grid.blockSignals(True)
        self.favorite_grid.clear()
        self.app_grid.clear()

        for app in favorites:
            item = self._grid_item(app, favorite=True, mark_favorite=False)
            self.favorite_grid.addItem(item)
            if app.package == selected_package:
                self.favorite_grid.setCurrentItem(item)

        for app in visible:
            favorite = app.package in self.favorite_packages
            item = self._grid_item(app, favorite=favorite, mark_favorite=True)
            self.app_grid.addItem(item)
            if app.package == selected_package and not favorite:
                self.app_grid.setCurrentItem(item)
        self.favorite_grid.blockSignals(False)
        self.app_grid.blockSignals(False)
        self.favorite_grid.setVisible(bool(favorites))
        self.favorite_empty_label.setVisible(not favorites)
        self._update_grid_heights()
        self.app_table.setRowCount(0)
        for row, app in enumerate(table_apps):
            favorite = app.package in self.favorite_packages
            self.app_table.insertRow(row)
            icon_item = QTableWidgetItem()
            icon_item.setIcon(QIcon(app.icon_path) if app.icon_path else generic_icon())
            icon_item.setData(Qt.ItemDataRole.UserRole, app.package)
            self.app_table.setItem(row, 0, icon_item)
            self.app_table.setItem(row, 1, QTableWidgetItem(f"★ {app.label}" if favorite else app.label))
            self.app_table.setItem(row, 2, QTableWidgetItem(app.package))
            profile_name = ""
            if self.selected_device:
                profile = self.store.get_profile(self.selected_device.profile_key, app.package)
                profile_name = profile.name or ("настроен" if profile.settings or profile.extra_arguments else "")
            self.app_table.setItem(row, 3, QTableWidgetItem(profile_name))
            actions = QWidget()
            actions_layout = QHBoxLayout(actions)
            actions_layout.setContentsMargins(0, 0, 0, 0)
            edit = QToolButton()
            edit.setText("⚙")
            edit.setToolTip("Профиль приложения")
            edit.clicked.connect(lambda _checked=False, item=app: self.edit_profile(item))
            favorite_action = QToolButton()
            favorite_action.setText("★" if favorite else "☆")
            favorite_action.setToolTip("Убрать из избранного" if favorite else "Добавить в избранное")
            favorite_action.clicked.connect(lambda _checked=False, item=app: self.toggle_favorite(item))
            launch = QPushButton("Запустить")
            launch.clicked.connect(lambda _checked=False, item=app: self.launch_app(item))
            actions_layout.addWidget(favorite_action)
            actions_layout.addWidget(edit)
            actions_layout.addWidget(launch)
            self.app_table.setCellWidget(row, 4, actions)
            if app.package == selected_package:
                self.app_table.selectRow(row)

    def _grid_item(self, app: AppInfo, favorite: bool, mark_favorite: bool) -> QListWidgetItem:
        item = QListWidgetItem(
            QIcon(app.icon_path) if app.icon_path else generic_icon(),
            f"★ {app.label}" if favorite and mark_favorite else app.label,
        )
        item.setData(Qt.ItemDataRole.UserRole, app.package)
        item.setToolTip(
            f"{app.label}\n{app.package}\n"
            f"{'В избранном' if favorite else 'Не в избранном'}\n\nДвойной щелчок — запустить"
        )
        item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
        return item

    def _update_grid_heights(self) -> None:
        if not hasattr(self, "grid_scroll") or not hasattr(self, "app_grid"):
            return
        available_width = max(96, self.grid_scroll.viewport().width() - 16)
        cell_width = self.app_grid.gridSize().width() + self.app_grid.spacing()
        cell_height = self.app_grid.gridSize().height() + self.app_grid.spacing()
        columns = max(1, available_width // cell_width)

        def resize_grid(grid: QListWidget) -> None:
            rows = (grid.count() + columns - 1) // columns
            grid.setFixedHeight(max(0, rows * cell_height + 4))

        resize_grid(self.favorite_grid)
        resize_grid(self.app_grid)

    def _grid_selection_changed(self, source: QListWidget, current: QListWidgetItem | None) -> None:
        if current is not None:
            other = self.app_grid if source is self.favorite_grid else self.favorite_grid
            other.blockSignals(True)
            other.clearSelection()
            other.setCurrentItem(None)
            other.blockSignals(False)

    def _filter_changed(self) -> None:
        self._render_apps()
        self._schedule_visible_icons()

    def _schedule_visible_icons(self) -> None:
        if self.device_icon_loader_active:
            return
        queued = {app.package for app in self.icon_queue}
        for app in self._filtered_apps():
            if not app.icon_path and not app.icon_attempted and app.package not in queued:
                self.icon_queue.append(app)
        self._pump_icon_queue()

    def _app_by_package(self, package: str) -> AppInfo | None:
        return next((app for app in self.apps if app.package == package), None)

    def _current_app(self) -> AppInfo | None:
        if self.app_stack.currentIndex() == 0:
            focused_grid = next(
                (grid for grid in (self.favorite_grid, self.app_grid) if grid.hasFocus() and grid.currentItem()),
                None,
            )
            item = focused_grid.currentItem() if focused_grid else (
                self.favorite_grid.currentItem() or self.app_grid.currentItem()
            )
            package = str(item.data(Qt.ItemDataRole.UserRole)) if item else ""
        else:
            rows = self.app_table.selectionModel().selectedRows()
            package = str(self.app_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)) if rows else ""
        return self._app_by_package(package)

    def toggle_favorite(self, app: AppInfo) -> None:
        if not self._require_device():
            return
        favorite = app.package not in self.favorite_packages
        self.store.set_favorite(self.favorite_device_key, app.package, favorite)
        if favorite:
            self.favorite_packages.add(app.package)
        else:
            self.favorite_packages.discard(app.package)
        self._render_apps()
        self._set_status(f"{app.label}: {'добавлено в избранное' if favorite else 'удалено из избранного'}")

    def launch_app_by_package(self, package: str) -> None:
        app = self._app_by_package(package)
        if app:
            self.launch_app(app)

    def _app_context_menu(self, grid: QListWidget, position) -> None:  # type: ignore[no-untyped-def]
        item = grid.itemAt(position)
        if item is None:
            return
        app = self._app_by_package(str(item.data(Qt.ItemDataRole.UserRole)))
        if app is None:
            return
        self._show_app_context_menu(app, grid.viewport().mapToGlobal(position))

    def _app_table_context_menu(self, position) -> None:  # type: ignore[no-untyped-def]
        item = self.app_table.itemAt(position)
        if item is None:
            return
        package_item = self.app_table.item(item.row(), 0)
        app = self._app_by_package(str(package_item.data(Qt.ItemDataRole.UserRole))) if package_item else None
        if app is None:
            return
        self._show_app_context_menu(app, self.app_table.viewport().mapToGlobal(position))

    def _show_app_context_menu(self, app: AppInfo, global_position) -> None:  # type: ignore[no-untyped-def]
        menu = QMenu(self)
        menu.addAction("Запустить", lambda: self.launch_app(app))
        menu.addAction(
            "Убрать из избранного" if app.package in self.favorite_packages else "Добавить в избранное",
            lambda: self.toggle_favorite(app),
        )
        menu.addAction("Профиль запуска…", lambda: self.edit_profile(app))
        menu.exec(global_position)

    def _pump_icon_queue(self) -> None:
        if not self.selected_device:
            return
        while self.active_icon_loads < self.max_icon_loads and self.icon_queue:
            app = self.icon_queue.popleft()
            if app.icon_attempted or app.icon_path:
                continue
            device = self.selected_device
            app.icon_attempted = True
            self.active_icon_loads += 1
            self._run(self.adb, ["-s", device.serial, "shell", "pm", "path", app.package], lambda c, o, e, source=device, item=app: self._icon_path_received(source, item, c, o, e))

    def _finish_icon_task(self) -> None:
        self.active_icon_loads = max(0, self.active_icon_loads - 1)
        self._pump_icon_queue()

    def _icon_path_received(self, device: DeviceInfo, app: AppInfo, code: int, stdout: str, _stderr: str) -> None:
        remote_path = extract_package_path(stdout) if not code else ""
        if not remote_path:
            self._finish_icon_task()
            return
        local_path = cache_filename(self.cache_directory, device.profile_key, app.package, remote_path)
        cached = next(local_path.parent.glob(local_path.name + ".*"), None)
        if cached:
            app.icon_path = str(cached)
            self._render_apps()
            self._finish_icon_task()
            return
        temporary_dir = Path(tempfile.mkdtemp(prefix="scrcpy-launcher-apk-"))
        apk_path = temporary_dir / "base.apk"
        self._run(self.adb, ["-s", device.serial, "pull", "-q", remote_path, str(apk_path)], lambda c, o, e: self._icon_pulled(app, apk_path, local_path, c, o, e))

    def _icon_pulled(self, app: AppInfo, apk_path: Path, cache_path: Path, code: int, _stdout: str, _stderr: str) -> None:
        if code:
            if apk_path.parent.exists():
                shutil.rmtree(apk_path.parent, ignore_errors=True)
            self._finish_icon_task()
            return
        job = IconExtractionJob(app, apk_path, cache_path)
        self.icon_jobs.append(job)
        job.signals.finished.connect(lambda item, path, current=job: self._icon_extracted(current, item, path))
        self.icon_pool.start(job)

    def _icon_extracted(self, job: IconExtractionJob, app: AppInfo, path: str) -> None:
        if job in self.icon_jobs:
            self.icon_jobs.remove(job)
        if path:
            app.icon_path = path
            self._render_apps()
        self._finish_icon_task()

    def edit_global_settings(self) -> None:
        settings = self.store.get_preference("global_settings", {})
        extras = self.store.get_preference("global_extra_arguments", [])
        dialog = SettingsDialog(dict(settings), list(extras), profile=False, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            values, arguments = dialog.values()
            self.store.set_preference("global_settings", values)
            self.store.set_preference("global_extra_arguments", arguments)

    def edit_profile(self, app: AppInfo) -> None:
        if not self._require_device():
            return
        assert self.selected_device
        profile = self.store.get_profile(self.selected_device.profile_key, app.package)
        dialog = SettingsDialog(profile.settings, profile.extra_arguments, profile=True, parent=self)
        dialog.force_stop.setChecked(profile.force_stop)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        profile.settings, profile.extra_arguments = dialog.values()
        profile.force_stop = dialog.force_stop.isChecked()
        profile.name = "Настроенный профиль" if profile.settings or profile.extra_arguments or profile.force_stop else ""
        self.store.save_profile(profile)
        self._render_apps()

    def _effective_settings(self, profile: LaunchProfile) -> tuple[dict[str, object], list[str]]:
        settings = dict(self.store.get_preference("global_settings", {}))
        settings.update(profile.settings)
        extras = list(self.store.get_preference("global_extra_arguments", [])) + profile.extra_arguments
        return settings, extras

    def launch_app(self, app: AppInfo) -> None:
        if not self._require_device():
            return
        assert self.selected_device
        profile = self.store.get_profile(self.selected_device.profile_key, app.package)
        settings, extras = self._effective_settings(profile)
        title = str(settings.get("window_title") or f"{app.label} — {self.selected_device.model or self.selected_device.serial}")
        settings["window_title"] = title
        try:
            arguments = build_scrcpy_arguments(self.selected_device.serial, app.package, settings, force_stop=profile.force_stop, extra_arguments=extras)
        except ValueError as error:
            self._show_error("Нельзя запустить приложение", str(error))
            return
        session = ScrcpySession(str(self.scrcpy), arguments, self.selected_device.serial, app.package, title, self)
        session.changed.connect(self._session_changed)
        self.sessions[session.info.session_id] = session
        session.start()
        self._session_changed(session.info)
        self._set_status(f"Запуск: {app.label}")

    def _session_changed(self, info) -> None:  # type: ignore[no-untyped-def]
        row = next((index for index in range(self.session_table.rowCount()) if self.session_table.item(index, 0).data(Qt.ItemDataRole.UserRole) == info.session_id), None)
        if row is None:
            row = self.session_table.rowCount()
            self.session_table.insertRow(row)
            identifier = QTableWidgetItem(info.title)
            identifier.setData(Qt.ItemDataRole.UserRole, info.session_id)
            self.session_table.setItem(row, 0, identifier)
            self.session_table.setItem(row, 1, QTableWidgetItem(info.device_serial))
            self.session_table.setItem(row, 2, QTableWidgetItem(info.package))
            self.session_table.setItem(row, 3, QTableWidgetItem(info.state))
            self.session_table.setItem(row, 4, QTableWidgetItem(info.log[-800:]))
        else:
            self.session_table.item(row, 3).setText(info.state)
            self.session_table.item(row, 4).setText(info.log[-800:])

    def stop_selected_session(self) -> None:
        rows = self.session_table.selectionModel().selectedRows()
        if not rows:
            return
        session_id = self.session_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        session = self.sessions.get(session_id)
        if session:
            session.stop()

    def pair_device(self) -> None:
        dialog = PairDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        endpoint, code = dialog.endpoint(), dialog.code.text().strip()
        if not dialog.host.text().strip() or not dialog.port.text().strip() or len(code) < 4:
            self._show_error("Неверные данные Pair", "Введите IP, порт сопряжения и код с экрана телефона.")
            return
        self._set_status("Сопряжение ADB…")
        self._run(self.adb, ["pair", endpoint, code], lambda c, o, e: self._paired(endpoint, c, o, e))

    def _paired(self, endpoint: str, code: int, stdout: str, stderr: str) -> None:
        if code:
            self._show_error("Pair не выполнен", stderr or stdout)
            return
        self._set_status("Pair выполнен. Поиск порта подключения mDNS…")
        self._run(self.adb, ["mdns", "services"], lambda c, o, e: self._mdns_after_pair(endpoint, c, o, e))

    def _mdns_after_pair(self, pairing_endpoint: str, code: int, stdout: str, _stderr: str) -> None:
        services = parse_mdns_services(stdout) if not code else []
        connect_service = next((item for item in services if item["service"] == "_adb-tls-connect._tcp"), None)
        if connect_service:
            endpoint = connect_service["endpoint"]
            self._run(self.adb, ["connect", endpoint], lambda c, o, e: self._connected(endpoint, c, o, e))
            return
        QMessageBox.information(
            self,
            "Pair выполнен",
            "Устройство сопряжено, но ADB не нашёл порт подключения через mDNS. "
            "Введите IP и отдельный порт подключения в диспетчере устройств.",
        )
        self.refresh_devices()

    def _connect_from_manager(self) -> None:
        host = self.device_ip.text().strip()
        port = self.device_port.text().strip()
        self._connect_endpoint(f"{host}:{port}" if host and port else "")

    def _connect_endpoint(self, endpoint: str) -> None:
        if ":" not in endpoint:
            self._show_error("Неверный адрес", "Введите IP:порт подключения ADB.")
            return
        self._set_status(f"Подключение к {endpoint}…")
        self._run(self.adb, ["connect", endpoint], lambda c, o, e: self._connected(endpoint, c, o, e))

    def _connected(self, endpoint: str, code: int, stdout: str, stderr: str) -> None:
        if code or "failed" in (stdout + stderr).lower():
            self._show_error("Не удалось подключить ADB", stderr or stdout)
            return
        self.store.remember_endpoint(endpoint)
        self._set_status(f"Подключено: {endpoint}")
        self.refresh_devices()

    def _require_device(self) -> bool:
        if self.selected_device and self.selected_device.state == "device":
            return True
        self._show_error("Устройство не выбрано", "Выберите подключённое устройство со статусом device.")
        return False

    def _show_error(self, title: str, details: str) -> None:
        self._set_status(title)
        QMessageBox.critical(self, title, html.escape(details.strip() or "Неизвестная ошибка"))

    def show_about(self) -> None:
        QMessageBox.information(
            self,
            "О программе",
            "Scrcpy Launcher\n\nЗапускает выбранные Android-приложения в отдельных виртуальных дисплеях scrcpy. "
            "Не устанавливает ничего на телефон и использует bundled scrcpy/adb.",
        )


def run_application(project_root: Path) -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Scrcpy Launcher")
    app.setOrganizationName("ScrcpyLauncher")
    app.setFont(QFont("Segoe UI", 9))
    if not ensure_scrcpy(project_root):
        return 0
    try:
        window = MainWindow(project_root)
    except FileNotFoundError as error:
        QMessageBox.critical(None, "Scrcpy Launcher", str(error))
        return 1
    window.show()
    return app.exec()
