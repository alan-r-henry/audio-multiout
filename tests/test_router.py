import struct
import unittest

import router
from tests.fakes import FakeRenderer, endpoint, floats, unfloats


class ApplyGainTests(unittest.TestCase):
    def test_unity_returns_input_untouched(self):
        data = floats(0.1, -0.2)
        self.assertIs(router._apply_gain(data, 1.0, 32), data)

    def test_float_gain_scales(self):
        out = unfloats(router._apply_gain(floats(0.25, -0.25), 2.0, 32))
        self.assertAlmostEqual(out[0], 0.5, places=5)
        self.assertAlmostEqual(out[1], -0.5, places=5)

    def test_float_never_exceeds_full_scale(self):
        out = unfloats(router._apply_gain(floats(0.9, -0.9, 1.0), 3.0, 32))
        self.assertTrue(all(-1.0 <= v <= 1.0 for v in out))

    def test_below_knee_is_exactly_linear(self):
        data, limited = router._boost(floats(0.1, -0.2, 0.25), 3.0, 32)
        self.assertEqual(limited, 0)
        for got, want in zip(unfloats(data), (0.3, -0.6, 0.75)):
            self.assertAlmostEqual(got, want, places=5)

    def test_soft_limit_is_monotonic_and_bounded(self):
        import numpy as np
        xs = np.linspace(0.0, 3.0, 400, dtype=np.float32)
        ys = unfloats(router._boost(xs.astype("<f4").tobytes(), 1.0001, 32)[0])
        ys = np.array(ys)
        self.assertTrue((np.diff(ys) >= -1e-6).all())
        self.assertLessEqual(float(ys.max()), 1.0)

    def test_soft_limit_continuous_at_knee(self):
        k = router.LIMITER_KNEE
        below = unfloats(router._boost(floats(k - 1e-4), 1.5, 32)[0])[0]
        # feed so that scaled sample sits just either side of the knee
        lo = unfloats(router._boost(floats((k - 1e-4) / 1.5), 1.5, 32)[0])[0]
        hi = unfloats(router._boost(floats((k + 1e-4) / 1.5), 1.5, 32)[0])[0]
        self.assertAlmostEqual(lo, k - 1e-4, places=4)
        self.assertAlmostEqual(hi, k + 1e-4, places=3)

    def test_limiting_is_symmetric_and_counted(self):
        data, limited = router._boost(floats(0.9, -0.9), 2.0, 32)
        hi, lo = unfloats(data)
        self.assertAlmostEqual(hi, -lo, places=6)
        self.assertEqual(limited, 2)
        self.assertLess(hi, 1.0)
        self.assertGreater(hi, router.LIMITER_KNEE)

    def test_attenuation_never_limits(self):
        data, limited = router._boost(floats(0.99, -0.99), 0.5, 32)
        self.assertEqual(limited, 0)
        self.assertAlmostEqual(unfloats(data)[0], 0.495, places=5)

    def test_int16_soft_limits_instead_of_wrapping(self):
        data = struct.pack("<3h", 30000, -30000, 1000)
        a, b, c = struct.unpack("<3h", router._apply_gain(data, 3.0, 16))
        self.assertGreater(a, 0)
        self.assertLessEqual(a, 32767)
        self.assertEqual(a, -b)
        self.assertEqual(c, 3000)

    def test_mirror_reports_limiting(self):
        mirror = router.Mirror(endpoint("m", "M"), FakeRenderer(), 48000, 8, 32,
                               gain=3.0)
        self.assertFalse(mirror.limiting_now())
        mirror.feed(floats(0.9, 0.9))
        self.assertTrue(mirror.limiting_now())
        self.assertEqual(mirror.limited_samples, 2)

    def test_int16_clips_to_range(self):
        data = struct.pack("<2h", 20000, -20000)
        out = struct.unpack("<2h", router._apply_gain(data, 3.0, 16))
        self.assertEqual(out, (32767, -32767))  # symmetric

    def test_unknown_bit_depth_is_left_alone(self):
        data = b"\x01\x02\x03\x04\x05\x06"
        self.assertEqual(router._apply_gain(data, 2.0, 24), data)

    def test_empty_data(self):
        self.assertEqual(router._apply_gain(b"", 2.0, 32), b"")


class MirrorTests(unittest.TestCase):
    def make(self, **kw):
        renderer = FakeRenderer()
        mirror = router.Mirror(endpoint("m", "Mirror"), renderer, 48000, 8, 32, **kw)
        return mirror, renderer

    def test_prime_writes_target_depth_of_silence(self):
        mirror, renderer = self.make(target_fill_ms=20, delay_ms=10)
        mirror.prime()
        self.assertEqual(renderer.silence_frames, 48000 * 30 // 1000)

    def test_delay_shifts_target_frames(self):
        mirror, _ = self.make(target_fill_ms=20, delay_ms=0)
        base = mirror.target_frames
        mirror.delay_ms = 50
        self.assertEqual(mirror.target_frames - base, 48000 * 50 // 1000)

    def test_feed_applies_gain(self):
        mirror, _ = self.make(gain=2.0)
        mirror.feed(floats(0.25, 0.25) * 2)
        self.assertAlmostEqual(unfloats(bytes(mirror.queue))[0], 0.5, places=5)

    def test_service_writes_queue_to_renderer(self):
        mirror, renderer = self.make()
        mirror.prime()
        mirror.feed(floats(*([0.1] * 16)))
        self.assertTrue(mirror.service())
        self.assertEqual(len(renderer.written), 64)
        self.assertEqual(len(mirror.queue), 0)

    def test_renderer_exception_marks_mirror_failed(self):
        mirror, renderer = self.make()
        mirror.prime()
        renderer.fail_on_write = OSError("gone")
        mirror.feed(floats(*([0.1] * 16)))
        self.assertFalse(mirror.service())
        self.assertIn("stream lost", mirror.failed)

    def test_failed_mirror_ignores_feed(self):
        mirror, _ = self.make()
        mirror.failed = "dead"
        mirror.feed(b"\x00" * 64)
        self.assertEqual(len(mirror.queue), 0)

    def test_silence_at_rest_is_not_an_underrun(self):
        mirror, renderer = self.make()
        for _ in range(5):
            self.assertTrue(mirror.service())  # PC silent: no packets at all
        self.assertEqual(mirror.underruns, 0)
        self.assertGreater(renderer.silence_frames, 0)  # still kept fed

    def test_running_dry_while_source_playing_is_an_underrun(self):
        mirror, renderer = self.make()
        mirror.feed(floats(*([0.1] * 16)))
        mirror.service()                 # writes the audio out
        renderer.drain(10 ** 6)          # device empties, source still "active"
        mirror.service()
        self.assertEqual(mirror.underruns, 1)


class RouterSettersTests(unittest.TestCase):
    def setUp(self):
        self.router = router.Router()
        self.mirror = router.Mirror(endpoint("m", "M"), FakeRenderer(), 48000, 8, 32)
        self.router.mirrors = [self.mirror]

    def test_set_gain_clamps(self):
        self.assertTrue(self.router.set_gain("m", 99))
        self.assertEqual(self.mirror.gain, router.MAX_GAIN)
        self.assertTrue(self.router.set_gain("m", -1))
        self.assertEqual(self.mirror.gain, 0.0)
        self.assertFalse(self.router.set_gain("nope", 2))

    def test_set_delay_clamps(self):
        self.router.set_delay("m", 9999)
        self.assertEqual(self.mirror.delay_ms, 500)


if __name__ == "__main__":
    unittest.main()
