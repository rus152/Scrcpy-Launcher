"""Interface translations.

Adding a language is one file: drop `launcher/locales/<code>.json` next to the existing
ones, give it a `language.name` (the language's own endonym, e.g. "Deutsch") and the
same keys as `en.json`. It is then discovered at import and appears everywhere on its
own — the app-bar language menu, the first-run question, OS-language detection and the
saved preference all read the registry built here, and nothing hardcodes a language code.

Keys missing from a locale fall back to `DEFAULT_LANGUAGE`, so a partial translation
degrades to mixed text rather than blank UI; `tests/test_i18n.py` fails on such a gap so
it is caught while developing instead of silently at runtime.
"""

from __future__ import annotations

import json
import locale
import os
from pathlib import Path

# English doubles as the fallback for keys a locale is missing, so it must stay the
# most complete file in `locales/`.
DEFAULT_LANGUAGE = "en"
LOCALES_DIRECTORY = Path(__file__).parent / "locales"
# Endonym key every locale file carries, so the language registry can be derived from
# the files themselves instead of a second list that has to be kept in sync.
LANGUAGE_NAME_KEY = "language.name"


def _load_locales() -> dict[str, dict[str, str]]:
    tables: dict[str, dict[str, str]] = {}
    for path in sorted(LOCALES_DIRECTORY.glob("*.json")):
        # A malformed locale raises rather than being skipped: silently dropping it
        # would show up much later as untranslated UI with no hint of the real cause.
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise RuntimeError(f"Cannot read locale file {path}: {error}") from error
        if not isinstance(payload, dict):
            raise RuntimeError(f"Locale file {path} must contain a JSON object.")
        tables[path.stem] = {str(key): str(value) for key, value in payload.items()}
    if DEFAULT_LANGUAGE not in tables:
        raise RuntimeError(
            f"Missing the fallback locale {LOCALES_DIRECTORY / (DEFAULT_LANGUAGE + '.json')}; "
            "the interface cannot be rendered without it."
        )
    return tables


TRANSLATIONS: dict[str, dict[str, str]] = _load_locales()
# code -> endonym, in the order the languages are offered in the UI.
LANGUAGES: dict[str, str] = {
    code: table.get(LANGUAGE_NAME_KEY, code) for code, table in TRANSLATIONS.items()
}

_current_language = DEFAULT_LANGUAGE


def detect_system_language() -> str:
    """Best guess at the OS UI language, narrowed to the languages we actually ship.

    Only used to preselect an answer in the first-run language question — the user is
    still asked, so a wrong guess costs one click. Falls back to DEFAULT_LANGUAGE
    (English) whenever the system language isn't one we translate into.
    """
    candidates: list[str] = []
    try:
        import ctypes

        # The Windows *UI* language, which is what the user reads menus in — the
        # regional-format locale (GetUserDefaultLocaleName) can differ, e.g. an
        # English Windows set to Russian date/number formats.
        language_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
        candidates.append(locale.windows_locale.get(language_id, ""))
    except (AttributeError, OSError, ImportError):  # non-Windows, or no UI language
        pass
    candidates.extend(os.environ.get(name, "") for name in ("LANGUAGE", "LC_ALL", "LANG"))

    for candidate in candidates:
        # "ru_RU.UTF-8" / "ru-RU" / "ru" all reduce to "ru".
        code = candidate.replace("-", "_").split("_", 1)[0].split(".", 1)[0].casefold()
        if code in LANGUAGES:
            return code
    return DEFAULT_LANGUAGE


def get_language() -> str:
    return _current_language


def set_language(language: str) -> None:
    global _current_language
    if language in LANGUAGES:
        _current_language = language


def t(key: str, **kwargs: object) -> str:
    """Translate `key` into the current language, formatting any placeholders."""
    table = TRANSLATIONS[_current_language]
    text = table.get(key)
    if text is None:
        text = TRANSLATIONS[DEFAULT_LANGUAGE].get(key, key)
    return text.format(**kwargs) if kwargs else text
