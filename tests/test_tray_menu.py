import queue
import types
import unittest

import main
from tests.fakes import endpoint


class TrayDeviceMenuTests(unittest.TestCase):
    def build(self, selected):
        app = types.SimpleNamespace(
            settings={"selected": selected}, events=queue.Queue(),
            engine=types.SimpleNamespace(endpoints=[
                endpoint("a", "Headphones"), endpoint("b", "BenQ"),
                endpoint("c", "S/PDIF")]))
        app._toggle_action = lambda eid: main.App._toggle_action(app, eid)
        app._checked_state = main.App._checked_state
        return app, list(main.App._device_menu_items(app))

    def test_one_item_per_device_with_check_and_primary_marker(self):
        _app, items = self.build(["a", "b"])
        self.assertEqual([i.text for i in items],
                         ["Headphones   [primary]", "BenQ", "S/PDIF"])
        self.assertEqual([bool(i.checked) for i in items], [True, True, False])

    def test_click_posts_toggle_event(self):
        app, items = self.build([])
        items[2](None)  # pystray invokes the item with the icon
        self.assertEqual(app.events.get_nowait(), ("toggle-device", "c"))

    def test_no_devices_shows_disabled_placeholder(self):
        app, _ = self.build([])
        app.engine.endpoints = []
        items = list(main.App._device_menu_items(app))
        self.assertEqual(len(items), 1)
        self.assertFalse(items[0].enabled)


if __name__ == "__main__":
    unittest.main()
