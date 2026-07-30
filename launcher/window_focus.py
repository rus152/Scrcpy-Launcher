"""Bring an already-running scrcpy window to the foreground.

Pure Win32 via ctypes — the launcher ships no pywin32 dependency, and a window
handle is the one thing scrcpy gives us no other way to reach. Everything here is
best-effort: a caller that gets `False` should say so rather than assume failure
means the session is gone.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

GW_OWNER = 4
SW_RESTORE = 9
SW_SHOW = 5


def _user32() -> ctypes.WinDLL | None:
    if sys.platform != "win32":
        return None
    return ctypes.windll.user32


def process_windows(pid: int) -> list[int]:
    """Top-level, visible, unowned windows belonging to `pid`, in Z-order."""
    user32 = _user32()
    if user32 is None or pid <= 0:
        return []

    handles: list[int] = []
    # WINFUNCTYPE (stdcall) is required for EnumWindows callbacks; CFUNCTYPE would
    # corrupt the stack. The object is kept alive by staying referenced below.
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def collect(hwnd: int, _lparam: int) -> bool:
        window_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if window_pid.value != pid:
            return True
        if not user32.IsWindowVisible(hwnd):
            return True
        # Skip tool/child windows so we focus scrcpy's actual mirror window.
        if user32.GetWindow(hwnd, GW_OWNER):
            return True
        handles.append(hwnd)
        return True

    user32.EnumWindows(callback_type(collect), 0)
    return handles


def focus_process_window(pid: int) -> bool:
    """Restore and foreground the first window of `pid`. False if there is none.

    SetForegroundWindow is refused by Windows when the calling process does not
    own the current foreground window, which happens whenever the user reaches the
    launcher by other means. SwitchToThisWindow is the documented-enough fallback
    that still works in that case, so a refusal is not treated as fatal.
    """
    user32 = _user32()
    if user32 is None:
        return False
    handles = process_windows(pid)
    if not handles:
        return False

    hwnd = handles[0]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    else:
        user32.ShowWindow(hwnd, SW_SHOW)
    if user32.SetForegroundWindow(hwnd):
        return True
    try:
        user32.SwitchToThisWindow(hwnd, True)
    except (AttributeError, OSError):
        return False
    return True
