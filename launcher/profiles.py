from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import LaunchProfile


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProfileStore:
    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                device_key TEXT NOT NULL,
                package TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                force_stop INTEGER NOT NULL DEFAULT 0,
                settings_json TEXT NOT NULL DEFAULT '{}',
                extra_arguments_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (device_key, package)
            );
            CREATE TABLE IF NOT EXISTS preferences (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS endpoints (
                endpoint TEXT PRIMARY KEY,
                label TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS favorites (
                device_key TEXT NOT NULL,
                package TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (device_key, package)
            );
            CREATE TABLE IF NOT EXISTS device_facts (
                hardware TEXT PRIMARY KEY,
                form_factor TEXT NOT NULL,
                detector_version INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def _execute(self, sql: str, params: tuple = ()) -> None:
        self.connection.execute(sql, params)
        self.connection.commit()

    def get_profile(self, device_key: str, package: str) -> LaunchProfile:
        row = self.connection.execute(
            "SELECT * FROM profiles WHERE device_key = ? AND package = ?", (device_key, package)
        ).fetchone()
        if row is None:
            return LaunchProfile(device_key=device_key, package=package)
        return LaunchProfile(
            device_key=row["device_key"],
            package=row["package"],
            name=row["name"],
            force_stop=bool(row["force_stop"]),
            settings=json.loads(row["settings_json"]),
            extra_arguments=json.loads(row["extra_arguments_json"]),
        )

    def save_profile(self, profile: LaunchProfile) -> None:
        now = _utc_now()
        self._execute(
            """
            INSERT INTO profiles(device_key, package, name, force_stop, settings_json, extra_arguments_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_key, package) DO UPDATE SET
              name=excluded.name, force_stop=excluded.force_stop,
              settings_json=excluded.settings_json,
              extra_arguments_json=excluded.extra_arguments_json, updated_at=excluded.updated_at
            """,
            (
                profile.device_key,
                profile.package,
                profile.name,
                int(profile.force_stop),
                json.dumps(profile.settings, ensure_ascii=False),
                json.dumps(profile.extra_arguments, ensure_ascii=False),
                now,
            ),
        )

    def delete_profile(self, device_key: str, package: str) -> None:
        self._execute("DELETE FROM profiles WHERE device_key = ? AND package = ?", (device_key, package))

    def get_preference(self, key: str, default: object) -> object:
        row = self.connection.execute("SELECT value_json FROM preferences WHERE key = ?", (key,)).fetchone()
        return default if row is None else json.loads(row["value_json"])

    def set_preference(self, key: str, value: object) -> None:
        self._execute(
            "INSERT INTO preferences(key, value_json) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def remember_endpoint(self, endpoint: str, label: str = "") -> None:
        self._execute(
            """
            INSERT INTO endpoints(endpoint, label, last_used_at) VALUES (?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET label=excluded.label, last_used_at=excluded.last_used_at
            """,
            (endpoint, label, _utc_now()),
        )

    def endpoints(self) -> list[tuple[str, str]]:
        return [
            (row["endpoint"], row["label"])
            for row in self.connection.execute("SELECT endpoint, label FROM endpoints ORDER BY last_used_at DESC")
        ]

    def favorites(self, device_key: str) -> set[str]:
        return {
            row["package"]
            for row in self.connection.execute(
                "SELECT package FROM favorites WHERE device_key = ? ORDER BY created_at", (device_key,)
            )
        }

    def set_favorite(self, device_key: str, package: str, favorite: bool) -> None:
        if favorite:
            self._execute(
                "INSERT OR IGNORE INTO favorites(device_key, package, created_at) VALUES (?, ?, ?)",
                (device_key, package, _utc_now()),
            )
        else:
            self._execute("DELETE FROM favorites WHERE device_key = ? AND package = ?", (device_key, package))

    def cached_form_factor(self, hardware: str, detector_version: int) -> str:
        """What this model was last classified as, or "" if we have to go ask again.

        Rows written by an older detector are ignored rather than deleted: bumping
        `FORM_FACTOR_DETECTOR_VERSION` is what re-runs the probe after the rules
        change, and the stale row is overwritten by the fresh verdict anyway.
        """
        if not hardware:
            return ""
        row = self.connection.execute(
            "SELECT form_factor FROM device_facts WHERE hardware = ? AND detector_version = ?",
            (hardware, detector_version),
        ).fetchone()
        return row["form_factor"] if row is not None else ""

    def remember_form_factor(self, hardware: str, form_factor: str, detector_version: int) -> None:
        # An empty verdict is the probe saying "couldn't tell", which must not be
        # cached — the next run may have better luck, and a stored "" would read as a
        # hit and stop it from ever trying.
        if not hardware or not form_factor:
            return
        self._execute(
            """
            INSERT INTO device_facts(hardware, form_factor, detector_version, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(hardware) DO UPDATE SET
              form_factor=excluded.form_factor, detector_version=excluded.detector_version,
              updated_at=excluded.updated_at
            """,
            (hardware, form_factor, detector_version, _utc_now()),
        )

    def merge_favorites(self, source_device_key: str, target_device_key: str) -> None:
        if not source_device_key or source_device_key == target_device_key:
            return
        self.connection.execute(
            """
            INSERT OR IGNORE INTO favorites(device_key, package, created_at)
            SELECT ?, package, created_at FROM favorites WHERE device_key = ?
            """,
            (target_device_key, source_device_key),
        )
        self.connection.execute("DELETE FROM favorites WHERE device_key = ?", (source_device_key,))
        self.connection.commit()
