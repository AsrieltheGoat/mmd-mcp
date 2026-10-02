"""Window discovery and capture routing for MMD's separate-view mode."""

import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from mmd_mcp import windows


class WindowRoutingTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # One instance with a detached viewport, a second normal instance,
        # an owned dialog, an unrelated process, and a hidden render window.
        self.entries = {
            10: (100, windows.MAIN_WINDOW_CLASS, True, 0),
            11: (100, windows.RENDER_WINDOW_CLASS, True, 0),
            20: (200, windows.MAIN_WINDOW_CLASS, True, 0),
            12: (100, "#32770", True, 10),
            30: (300, windows.MAIN_WINDOW_CLASS, True, 0),
            21: (200, windows.RENDER_WINDOW_CLASS, False, 0),
        }
        def enumerate_windows(callback, arg):
            for handle in self.entries:
                callback(handle, arg)
        gui = windows.win32gui
        for name, side_effect in {
            "EnumWindows": enumerate_windows,
            "IsWindowVisible": lambda h: self.entries[h][2],
            "GetWindow": lambda h, _: self.entries[h][3],
            "GetClassName": lambda h: self.entries[h][1],
            "GetWindowText": lambda h: f"window {h}",
        }.items():
            self.stack.enter_context(patch.object(gui, name, side_effect=side_effect))
        self.iconic = self.stack.enter_context(patch.object(gui, "IsIconic", return_value=False))
        self.enabled = self.stack.enter_context(patch.object(gui, "IsWindowEnabled", return_value=True))
        self.stack.enter_context(patch.object(windows.win32process, "GetWindowThreadProcessId",
                                             side_effect=lambda h: (1, self.entries[h][0])))
        def process(pid):
            result = Mock()
            result.name.return_value = "other.exe" if pid == 300 else "MikuMikuDance.exe"
            result.create_time.return_value = float(pid)
            return result
        self.stack.enter_context(patch.object(windows.psutil, "Process", side_effect=process))

    def test_listing_counts_instances_not_render_windows(self):
        self.assertEqual([w.hwnd for w in windows.list_windows()], [10, 20])
        with self.assertRaisesRegex(ValueError, "Multiple"):
            windows.select_window()

    def test_single_detached_instance_is_selected_without_hwnd(self):
        del self.entries[20]
        self.assertEqual(windows.select_window().hwnd, 10)

    def test_render_handle_cannot_be_used_for_editing(self):
        with self.assertRaisesRegex(ValueError, "not found"):
            windows.select_window(11)

    def test_capture_routes_to_render_of_selected_process_only(self):
        main = windows.select_window(10)
        self.assertEqual(windows.capture_window(main).hwnd, 11)
        self.assertEqual(windows.capture_window(main, "render").hwnd, 11)
        self.assertEqual(windows.capture_window(main, "main").hwnd, 10)
        other = windows.select_window(20)
        self.assertEqual(windows.capture_window(other).hwnd, 20)
        with self.assertRaisesRegex(ValueError, "not found"):
            windows.capture_window(other, "render")

    def test_minimized_render_is_reported_without_falling_back(self):
        main = windows.select_window(10)
        self.iconic.side_effect = lambda h: h == 11
        with self.assertRaisesRegex(ValueError, "minimized"):
            windows.capture_window(main)
        self.assertEqual(windows.capture_window(main, "main").hwnd, 10)

    def test_disabled_render_is_reported(self):
        main = windows.select_window(10)
        self.enabled.side_effect = lambda h: h != 11
        with self.assertRaisesRegex(ValueError, "disabled"):
            windows.capture_window(main)
        with self.assertRaisesRegex(ValueError, "modal dialog"):
            windows.select_window(10)

    def test_ambiguous_render_windows_are_rejected(self):
        self.entries[13] = self.entries[11]
        with self.assertRaisesRegex(ValueError, "Multiple MMD render"):
            windows.capture_window(windows.select_window(10))

    def test_unknown_capture_view_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "view must be"):
            windows.capture_window(windows.select_window(10), "invalid")


if __name__ == "__main__":
    unittest.main()
