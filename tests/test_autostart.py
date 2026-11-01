import os
import sys
import unittest

import autostart

TEST_NAME = "AudioMultiOut-UnitTest"


class AutostartTests(unittest.TestCase):
    def tearDown(self):
        autostart.disable(TEST_NAME)

    def test_command_is_quoted_absolute_and_windowless(self):
        cmd = autostart.launch_command(r"C:\apps\audio multiout\main.py")
        self.assertTrue(cmd.endswith('"C:\\apps\\audio multiout\\main.py"'))
        self.assertTrue(cmd.startswith('"'))
        exe = cmd.split('"')[1]
        self.assertTrue(os.path.isabs(exe))
        if os.path.exists(os.path.join(os.path.dirname(sys.executable), "pythonw.exe")):
            self.assertTrue(exe.lower().endswith("pythonw.exe"))

    def test_enable_disable_roundtrip(self):
        self.assertFalse(autostart.is_enabled(TEST_NAME))
        ok, _ = autostart.enable("notepad.exe", name=TEST_NAME)
        self.assertTrue(ok)
        self.assertTrue(autostart.is_enabled(TEST_NAME))
        ok, _ = autostart.disable(TEST_NAME)
        self.assertTrue(ok)
        self.assertFalse(autostart.is_enabled(TEST_NAME))

    def test_disable_when_absent_is_ok(self):
        ok, note = autostart.disable(TEST_NAME)
        self.assertTrue(ok)
        self.assertEqual(note, "was not enabled")

    def test_startup_shortcut_counts_and_is_removed_on_disable(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as appdata:
            folder = os.path.join(appdata, "Microsoft", "Windows", "Start Menu",
                                  "Programs", "Startup")
            os.makedirs(folder)
            lnk = os.path.join(folder, autostart.SHORTCUT_NAME)
            open(lnk, "w").close()
            with mock.patch.dict(os.environ, {"APPDATA": appdata}):
                self.assertTrue(autostart.is_enabled())            # real app name
                self.assertFalse(autostart.is_enabled(TEST_NAME))  # other names ignore it
                ok, note = autostart.enable()
                self.assertIn("already", note)
                autostart.disable()
                self.assertFalse(os.path.exists(lnk))

    def test_toggle_flips_state(self):
        autostart.enable("notepad.exe", name=TEST_NAME)
        autostart.toggle(TEST_NAME)
        self.assertFalse(autostart.is_enabled(TEST_NAME))


if __name__ == "__main__":
    unittest.main()
