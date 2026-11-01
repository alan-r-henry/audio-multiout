import unittest

import winaudio


class MeterMathTests(unittest.TestCase):
    def test_silence_and_none_are_empty(self):
        self.assertEqual(winaudio.peak_to_fraction(0.0), 0.0)
        self.assertEqual(winaudio.peak_to_fraction(None), 0.0)

    def test_full_scale_is_full(self):
        self.assertEqual(winaudio.peak_to_fraction(1.0), 1.0)
        self.assertEqual(winaudio.peak_to_fraction(2.0), 1.0)  # clamped

    def test_below_floor_is_empty(self):
        self.assertEqual(winaudio.peak_to_fraction(0.0001), 0.0)

    def test_db_scale_midpoint(self):
        # -30 dB is half-way on a 60 dB scale.
        self.assertAlmostEqual(winaudio.peak_to_fraction(10 ** (-30 / 20)), 0.5)

    def test_monotonic(self):
        values = [winaudio.peak_to_fraction(p / 100) for p in range(0, 101)]
        self.assertEqual(values, sorted(values))


class VtableOrderTests(unittest.TestCase):
    """COM calls go by slot number; a mis-ordered declaration calls the wrong
    method. These pin the documented IAudioEndpointVolume layout."""

    def test_endpoint_volume_slots(self):
        names = [spec[1] for spec in winaudio.IAudioEndpointVolume._methods_]
        self.assertEqual(names[:7], [
            "RegisterControlChangeNotify", "UnregisterControlChangeNotify",
            "GetChannelCount", "SetMasterVolumeLevel",
            "SetMasterVolumeLevelScalar", "GetMasterVolumeLevel",
            "GetMasterVolumeLevelScalar"])
        self.assertEqual(names.index("SetMute"), 11)
        self.assertEqual(names.index("GetMute"), 12)

    def test_policy_config_set_default_slot(self):
        names = [spec[1] for spec in winaudio.IPolicyConfig._methods_]
        self.assertEqual(names.index("SetDefaultEndpoint"), 10)


class PeakMeterTests(unittest.TestCase):
    def test_bad_device_id_degrades_to_none(self):
        meter = winaudio.PeakMeter("{not-a-real-endpoint}")
        self.assertIsNone(meter.peak())


class LiveDeviceTests(unittest.TestCase):
    """Runs against the real machine; skipped where there is no audio."""

    def test_default_device_meter_reads_a_number(self):
        import comtypes
        comtypes.CoInitialize()
        default = winaudio.current_default()
        if default is None:
            self.skipTest("no default output device")
        peak = winaudio.PeakMeter(default.id).peak()
        self.assertIsNotNone(peak)
        self.assertTrue(0.0 <= peak <= 1.0)


if __name__ == "__main__":
    unittest.main()
