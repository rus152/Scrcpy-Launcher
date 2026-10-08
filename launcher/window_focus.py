"""Bring an already-running scrcpy window to the foreground.

Pure Win32 via ctypes — the launcher ships no pywin32 dependency, and a window
handle is the one thing scrcpy gives us no other way to reach.

Why a bare SetForegroundWindow is not enough
--------------------------------------------
Windows honours that call only from a process that already holds the foreground
window, was started by it, or received the last input event (see the Remarks under
`SetForegroundWindow` on MSDN). This launcher satisfies none of them: Flet runs its
UI in a separate `flet.exe` child process, so the Python process making the call owns
no windows at all and never sees an input event. A refused call is not reported as an
error — Windows silently flashes the target's taskbar button instead, which is exactly
the "I clicked show and nothing happened" the user experiences.

`AttachThreadInput` is the way out: attaching our thread's input queue to the thread
that *does* own the foreground window makes the two share one input context, and the
call is then honoured. The attachment is undone immediately — leaving it in place
would tie this process's input to another application's for the rest of the session.

Every strategy below is verified against `GetForegroundWindow` rather than trusted,
because `SetForegroundWindow` reports success in the taskbar-flash case too, which is
what let the old fallback chain go unused.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

GW_OWNER = 4
SW_RESTORE = 9
SW_SHOW = 5
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

_ENUM_WINDOWS_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

_user32_cache: ctypes.WinDLL | None = None


def _user32() -> ctypes.WinDLL | None:
    """user32 with every signature declared, or None off Windows.

    The declarations are not cosmetic: ctypes defaults an undeclared argument to a
    32-bit C int, which silently truncates the 64-bit HWNDs and thread ids these
    functions take on a 64-bit build.
    """
    global _user32_cache
    if sys.platform != "win32":
        return None
    if _user32_cache is not None:
        return _user32_cache

    user32 = ctypes.windll.user32
    user32.EnumWindows.argtypes = [_ENUM_WINDOWS_PROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    # SwitchToThisWindow is exported but not in the SDK headers; guard its absence.
    if hasattr(user32, "SwitchToThisWindow"):
        user32.SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
        user32.SwitchToThisWindow.restype = None
    _user32_cache = user32
    return user32


def process_windows(pid: int) -> list[int]:
    """Top-level, visible, unowned windows belonging to `pid`, in Z-order."""
    user32 = _user32()
    if user32 is None or pid <= 0:
        return []

    handles: list[int] = []

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

    # WINFUNCTYPE (stdcall) is required for EnumWindows callbacks; CFUNCTYPE would
    # corrupt the stack. The object stays referenced for the duration of the call.
    user32.EnumWindows(_ENUM_WINDOWS_PROC(collect), 0)
    return handles


def _is_foreground(user32: ctypes.WinDLL, hwnd: int) -> bool:
    return bool(user32.GetForegroundWindow() == hwnd)


def _try_direct(user32: ctypes.WinDLL, hwnd: int) -> bool:
    """The plain call, which succeeds on the paths where Windows grants the right.

    Worth trying first: it is the only strategy with no side effects at all, and it
    does land when no window holds the foreground, or when the user reached us from
    the scrcpy window itself.
    """
    user32.SetForegroundWindow(hwnd)
    return _is_foreground(user32, hwnd)


def _try_attached(user32: ctypes.WinDLL, hwnd: int) -> bool:
    """Borrow the foreground window's input context for the length of one call."""
    foreground = user32.GetForegroundWindow()
    if not foreground:
        return False
    foreground_thread = user32.GetWindowThreadProcessId(foreground, None)
    our_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    if not foreground_thread or foreground_thread == our_thread:
        return False
    if not user32.AttachThreadInput(our_thread, foreground_thread, True):
        return False
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        return _is_foreground(user32, hwnd)
    finally:
        # Detaching matters more than the result, so it happens even on the way out
        # of an exception: an input queue left attached to another application's
        # thread outlives this call and can hang both of them.
        user32.AttachThreadInput(our_thread, foreground_thread, False)


def _try_switch_to(user32: ctypes.WinDLL, hwnd: int) -> bool:
    """SwitchToThisWindow — what Alt-Tab itself uses. Undocumented but stable."""
    if not hasattr(user32, "SwitchToThisWindow"):
        return False
    try:
        user32.SwitchToThisWindow(hwnd, True)
    except OSError:
        return False
    return _is_foreground(user32, hwnd)


def _raise_without_focus(user32: ctypes.WinDLL, hwnd: int) -> None:
    """Pull the window to the top of the Z-order without asking for focus.

    SetWindowPos needs no foreground rights, so this is the one step that always
    lands. Topmost is set and immediately dropped again, which leaves the window
    above the others without pinning it there permanently.
    """
    flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW
    for insert_after in (wintypes.HWND(-1), wintypes.HWND(-2)):  # HWND_TOPMOST, HWND_NOTOPMOST
        user32.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0, flags)


def focus_process_window(pid: int) -> bool:
    """Show the first window of `pid` and try to give it focus.

    Returns False only when there is nothing to show — no window for that pid, or it
    could not even be brought on screen. A window that ends up visible and on top but
    without keyboard focus still counts as shown: Windows refusing to move focus away
    from whatever the user is typing into is a deliberate protection, and from the
    user's side the window they asked for did appear.
    """
    user32 = _user32()
    if user32 is None:
        return False
    handles = process_windows(pid)
    if not handles:
        return False

    hwnd = handles[0]
    # Restoring comes first: foregrounding a minimised window leaves it minimised,
    # so the taskbar entry lights up and nothing else happens.
    user32.ShowWindow(hwnd, SW_RESTORE if user32.IsIconic(hwnd) else SW_SHOW)

    if _try_direct(user32, hwnd) or _try_attached(user32, hwnd) or _try_switch_to(user32, hwnd):
        return True

    _raise_without_focus(user32, hwnd)
    return bool(user32.IsWindowVisible(hwnd)) and not bool(user32.IsIconic(hwnd))
