import json
import os
import tempfile
import unittest
from unittest import mock

import config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        path = os.path.join(self.dir.name, "config.json")
        for name, value in (("APP_DIR", self.dir.name), ("CONFIG_PATH", path)):
            patcher = mock.patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.path = path

    def test_missing_file_gives_defaults(self):
        self.assertEqual(config.load()["target_fill_ms"], 20)

    def test_roundtrip(self):
        s = config.load()
        s["selected"] = ["a", "b"]
        ok, _ = config.save(s)
        self.assertTrue(ok)
        self.assertEqual(config.load()["selected"], ["a", "b"])

    def test_unknown_keys_dropped(self):
        with open(self.path, "w") as f:
            json.dump({"selected": ["a"], "junk": 1}, f)
        self.assertNotIn("junk", config.load())

    def test_corrupt_file_gives_defaults(self):
        with open(self.path, "w") as f:
            f.write("{not json")
        self.assertEqual(config.load()["selected"], [])

    def write(self, data):
        with open(self.path, "w") as f:
            json.dump(data, f)

    def test_wrong_types_fall_back_per_key(self):
        self.write({"selected": "oops", "boost_all": "yes", "gains": [1],
                    "target_fill_ms": "fast", "delays": {"a": 5}})
        s = config.load()
        self.assertEqual(s["selected"], [])
        self.assertIs(s["boost_all"], False)
        self.assertEqual(s["gains"], {})
        self.assertEqual(s["target_fill_ms"], 20)
        self.assertEqual(s["delays"], {"a": 5})  # good keys survive

    def test_out_of_range_values_clamped(self):
        self.write({"target_fill_ms": 99999, "delays": {"a": -5, "b": 9999},
                    "gains": {"a": 50, "b": "x"}})
        s = config.load()
        self.assertEqual(s["target_fill_ms"], 200)
        self.assertEqual(s["delays"], {"a": 0, "b": 500})
        self.assertEqual(s["gains"], {"a": config.MAX_GAIN})

    def test_bad_hotkey_resets(self):
        self.write({"hotkey": {"modifiers": "ctrl"}})
        self.assertEqual(config.load()["hotkey"]["vk"], 0x56)

    def test_defaults_are_not_shared_between_loads(self):
        config.load()["selected"].append("x")
        self.assertEqual(config.load()["selected"], [])

    def test_save_leaves_no_temp_file(self):
        config.save(config.load())
        self.assertEqual(os.listdir(self.dir.name), ["config.json"])

    def test_max_gain_matches_router(self):
        import router
        self.assertEqual(config.MAX_GAIN, router.MAX_GAIN)


if __name__ == "__main__":
    unittest.main()
