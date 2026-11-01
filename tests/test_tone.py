import struct
import unittest

import numpy as np

import tone


class MakeToneTests(unittest.TestCase):
    def test_length_matches_rate_channels_and_format(self):
        data = tone.make_tone(48000, 2, 32, seconds=0.5)
        self.assertEqual(len(data), 24000 * 2 * 4)
        data16 = tone.make_tone(44100, 1, 16, seconds=0.1)
        self.assertEqual(len(data16), 4410 * 2)

    def test_amplitude_bounded_and_faded(self):
        data = tone.make_tone(48000, 2, 32, amplitude=0.25)
        samples = np.frombuffer(data, "<f4")
        self.assertLessEqual(float(np.abs(samples).max()), 0.25 + 1e-6)
        self.assertEqual(float(samples[0]), 0.0)    # starts at silence
        self.assertEqual(float(samples[-1]), 0.0)   # ends at silence (no click)

    def test_channels_carry_identical_signal(self):
        samples = np.frombuffer(tone.make_tone(48000, 2, 32), "<f4").reshape(-1, 2)
        self.assertTrue((samples[:, 0] == samples[:, 1]).all())

    def test_int16_in_range(self):
        samples = np.frombuffer(tone.make_tone(48000, 2, 16), "<i2")
        self.assertLessEqual(int(np.abs(samples.astype(int)).max()), 32767)

    def test_unsupported_bit_depth(self):
        self.assertIsNone(tone.make_tone(48000, 2, 24))


class InterpretTests(unittest.TestCase):
    def test_delivered(self):
        ok, msg = tone.interpret(0.25)
        self.assertTrue(ok)
        self.assertIn("delivered", msg)

    def test_not_delivered(self):
        ok, msg = tone.interpret(0.0)
        self.assertFalse(ok)
        self.assertIn("NOT delivered", msg)

    def test_background_audio_makes_meter_inconclusive(self):
        ok, msg = tone.interpret(1.0, baseline=0.6)
        self.assertTrue(ok)
        self.assertIn("other audio", msg)

    def test_no_meter(self):
        ok, msg = tone.interpret(None)
        self.assertFalse(ok)
        self.assertIn("no level meter", msg)


class PlayTests(unittest.TestCase):
    def test_bad_device_reports_failure_not_exception(self):
        ok, msg = tone.play("{no-such-endpoint}", seconds=0.1)
        self.assertFalse(ok)
        self.assertIn("could not play", msg)


if __name__ == "__main__":
    unittest.main()
