"""Read-only discovery and validation of MMD top-level windows."""

from dataclasses import asdict, dataclass

import psutil
import pywintypes
import win32con
import win32gui
import win32process

MAIN_WINDOW_CLASS = "Polygon Movie Maker"
RENDER_WINDOW_CLASS = "MicWindow"


@dataclass(frozen=True)
class MMDWindow:
    hwnd: int
    pid: int
    title: str
    minimized: bool
    process_started: float

    def public_info(self) -> dict:
        return {key: value for key, value in asdict(self).items()
                if key != "process_started"}


def list_windows() -> list[MMDWindow]:
    """Expose one visible main/control window per MMD instance.

    Separate-view render windows are also unowned top-level windows, but have
    are not independent editor instances and must not be counted as such.
    """
    result = []

    def visit(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            if win32gui.GetWindow(hwnd, win32con.GW_OWNER):
                return
            if win32gui.GetClassName(hwnd) != MAIN_WINDOW_CLASS:
                return
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            process = psutil.Process(pid)
            if process.name().casefold() != "mikumikudance.exe":
                return
            result.append(MMDWindow(
                hwnd, pid, win32gui.GetWindowText(hwnd),
                bool(win32gui.IsIconic(hwnd)), process.create_time(),
            ))
        except (psutil.Error, pywintypes.error):
            # A process/window can disappear while EnumWindows is running.
            return

    win32gui.EnumWindows(visit, None)
    return sorted(result, key=lambda window: (window.pid, window.hwnd))


def select_window(hwnd: int | None = None) -> MMDWindow:
    windows = list_windows()
    candidates = windows if hwnd is None else [w for w in windows if w.hwnd == hwnd]
    if not candidates:
        raise ValueError("MMD window not found. Open MikuMikuDance and list windows again.")
    if len(candidates) != 1:
        raise ValueError("Multiple MMD windows found. Specify hwnd from mmd_list_windows.")
    window = candidates[0]
    if window.minimized:
        raise ValueError("MMD is minimized. Restore its window before capturing.")
    if not win32gui.IsWindowEnabled(window.hwnd):
        raise ValueError("MMD has a modal dialog open. Close it before capturing.")
    render = find_render_window(window)
    if render is not None and not win32gui.IsWindowEnabled(render.hwnd):
        raise ValueError("MMD has a modal dialog open in separate view. Close it before continuing.")
    return window


def find_render_window(window: MMDWindow) -> MMDWindow | None:
    """Find the related viewport even while a modal dialog disables it."""
    renders = []

    def visit(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            if win32gui.GetWindow(hwnd, win32con.GW_OWNER):
                return
            if win32gui.GetClassName(hwnd) != RENDER_WINDOW_CLASS:
                return
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid != window.pid:
                return
            renders.append(MMDWindow(
                hwnd, pid, win32gui.GetWindowText(hwnd),
                bool(win32gui.IsIconic(hwnd)), window.process_started,
            ))
        except pywintypes.error:
            return

    win32gui.EnumWindows(visit, None)
    if len(renders) > 1:
        raise ValueError("Multiple MMD render windows found for this instance.")
    return renders[0] if renders else None


def capture_window(window: MMDWindow, view: str = "auto") -> MMDWindow:
    """Resolve a capture surface within an already validated MMD instance."""
    if view not in {"auto", "main", "render"}:
        raise ValueError("view must be auto, main or render.")
    if view == "main":
        return window
    render = find_render_window(window)
    if render is None:
        if view == "render":
            raise ValueError("MMD separate-view render window not found. Enable separate view first.")
        return window
    if render.minimized:
        raise ValueError("MMD render window is minimized. Restore it before capturing.")
    if not win32gui.IsWindowEnabled(render.hwnd):
        raise ValueError("MMD render window is disabled. Close modal dialogs before capturing.")
    return render
