import unittest

import winui

SCREEN = (0, 0, 1920, 1080)


class PlacementTests(unittest.TestCase):
    def test_bottom_taskbar_anchors_bottom_right(self):
        area = (0, 0, 1920, 1032)  # 48px taskbar at the bottom
        x, y = winui.place_popup(area, SCREEN, 560, 400, gap=12)
        self.assertEqual((x, y), (1920 - 560 - 12, 1032 - 400 - 12))

    def test_taller_taskbar_moves_it_up(self):
        low = winui.place_popup((0, 0, 1920, 1032), SCREEN, 560, 400)[1]
        high = winui.place_popup((0, 0, 1920, 980), SCREEN, 560, 400)[1]
        self.assertLess(high, low)

    def test_top_taskbar_drops_from_top_right(self):
        area = (0, 48, 1920, 1080)
        x, y = winui.place_popup(area, SCREEN, 560, 400, gap=12)
        self.assertEqual((x, y), (1920 - 560 - 12, 48 + 12))

    def test_right_taskbar_keeps_clear_of_it(self):
        area = (0, 0, 1850, 1080)  # 70px taskbar on the right edge
        x, _ = winui.place_popup(area, SCREEN, 560, 400, gap=12)
        self.assertEqual(x + 560 + 12, 1850)

    def test_oversized_popup_is_clamped_on_screen(self):
        x, y = winui.place_popup((0, 0, 1920, 1032), SCREEN, 560, 5000)
        self.assertGreaterEqual(y, 0)
        self.assertGreaterEqual(x, 0)

    def test_secondary_monitor_offsets_respected(self):
        screen = (-1920, 0, 0, 1080)
        x, y = winui.place_popup((-1920, 0, 0, 1032), screen, 560, 400)
        self.assertGreaterEqual(x, -1920)
        self.assertLessEqual(x + 560, 0)


class ScaleTests(unittest.TestCase):
    def test_scale_factor(self):
        self.assertEqual(winui.scale_factor(96), 1.0)
        self.assertEqual(winui.scale_factor(144), 1.5)
        self.assertEqual(winui.scale_factor(48), 1.0)  # never shrinks


class LiveTests(unittest.TestCase):
    def test_work_area_is_inside_screen(self):
        left, top, right, bottom = winui.work_area()
        s = winui.screen_bounds()
        self.assertLess(left, right)
        self.assertLess(top, bottom)
        self.assertLessEqual(bottom, s[3])


if __name__ == "__main__":
    unittest.main()
