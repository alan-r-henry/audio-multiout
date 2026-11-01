import unittest
from unittest import mock

import config
import engine as engine_mod
import winaudio
from tests.fakes import endpoint


class EngineCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(config, "save", return_value=(True, ""))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.live = []
        patcher = mock.patch.object(winaudio, "list_outputs", lambda: self.live)
        patcher.start()
        self.addCleanup(patcher.stop)

    def engine(self, **settings):
        base = {"selected": [], "delays": {}, "gains": {}, "device_names": {},
                "boost_anchor_id": None}
        base.update(settings)
        return engine_mod.Engine(base)


class ReconcileTests(EngineCase):
    def test_names_recorded_on_refresh(self):
        self.live = [endpoint("A", "Headphones"), endpoint("B", "BenQ")]
        eng = self.engine(selected=["A"])
        eng.refresh()
        self.assertEqual(eng.settings["device_names"],
                         {"A": "Headphones", "B": "BenQ"})

    def test_renumbered_device_is_rematched(self):
        self.live = [endpoint("A", "Headphones"), endpoint("C", "S/PDIF")]
        eng = self.engine(selected=["A", "C"], delays={"A": 5},
                          gains={"C": 1.5}, boost_anchor_id="C")
        eng.refresh()
        selected = eng.settings["selected"]
        self.live = [endpoint("A2", "Headphones"), endpoint("C2", "S/PDIF")]
        eng.refresh()
        self.assertEqual(eng.settings["selected"], ["A2", "C2"])
        self.assertIs(eng.settings["selected"], selected)  # mutated in place
        self.assertEqual(eng.settings["delays"], {"A2": 5})
        self.assertEqual(eng.settings["gains"], {"C2": 1.5})
        self.assertEqual(eng.settings["boost_anchor_id"], "C2")

    def test_ambiguous_name_not_remapped(self):
        self.live = [endpoint("X", "Speakers")]
        eng = self.engine(selected=["X"])
        eng.refresh()
        self.live = [endpoint("Y", "Speakers"), endpoint("Z", "Speakers")]
        eng.refresh()
        self.assertEqual(eng.settings["selected"], ["X"])

    def test_windows_numeric_prefix_ignored(self):
        self.live = [endpoint("P", "3 - BenQ")]
        eng = self.engine(selected=["P"])
        eng.refresh()
        self.live = [endpoint("Q", "2 - BenQ")]
        eng.refresh()
        self.assertEqual(eng.settings["selected"], ["Q"])


class HealthTests(EngineCase):
    def test_dead_router_turns_mirroring_into_error(self):
        eng = self.engine()
        eng._status, eng._message = engine_mod.STATUS_MIRRORING, "mirroring"
        eng.router.error = "all mirror streams were lost"
        self.assertFalse(eng.active)
        self.assertEqual(eng.status, engine_mod.STATUS_ERROR)
        self.assertEqual(eng.message, "all mirror streams were lost")

    def test_other_statuses_unaffected(self):
        eng = self.engine()
        eng._status, eng._message = engine_mod.STATUS_SINGLE, "native"
        self.assertEqual(eng.status, engine_mod.STATUS_SINGLE)
        self.assertEqual(eng.message, "native")


class FakeRouter:
    def __init__(self):
        self.active = False
        self.error = None
        self.failed = []
        self.starts = 0
        self.start_ok = True

    def start(self, primary, mirrors, **kw):
        self.starts += 1
        self.active = self.start_ok
        if not self.start_ok:
            self.error = "boom"
        return self.start_ok, "mirroring" if self.start_ok else "boom"

    def stop(self):
        self.active = False

    def stats(self):
        return [{"failed": f} for f in self.failed]


class MaintainTests(EngineCase):
    def setUp(self):
        super().setUp()
        for target, value in (("set_default_output", (True, "ok")),):
            p = mock.patch.object(winaudio, target, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        self.A, self.B = endpoint("A", "Headphones", True), endpoint("B", "BenQ")
        self.live = [self.A, self.B]
        self.eng = self.engine(selected=["A", "B"])
        self.eng.router = FakeRouter()
        self.eng.apply(["A", "B"])

    def test_stable_state_does_nothing(self):
        self.assertFalse(self.eng.maintain(now=100))
        self.assertEqual(self.eng.router.starts, 1)

    def test_not_started_means_hands_off(self):
        eng = self.engine(selected=["A"])
        self.assertFalse(eng.maintain(now=100))

    def test_unplugged_device_triggers_reapply(self):
        self.live = [self.A]
        self.assertTrue(self.eng.maintain(now=100))
        self.assertEqual(self.eng.status, engine_mod.STATUS_SINGLE)

    def test_replugged_device_is_picked_up_again(self):
        self.live = [self.A]
        self.eng.maintain(now=100)
        self.live = [self.A, self.B]
        self.assertTrue(self.eng.maintain(now=101))
        self.assertEqual(self.eng.router.starts, 2)

    def test_dead_stream_triggers_reapply(self):
        self.eng.router.failed = ["lost"]
        self.assertTrue(self.eng.maintain(now=100))
        self.assertEqual(self.eng.router.starts, 2)

    def test_dead_router_triggers_reapply(self):
        self.eng.router.active = False
        self.assertTrue(self.eng.maintain(now=100))

    def test_failed_recovery_backs_off(self):
        self.eng.router.active = False
        self.eng.router.start_ok = False
        self.assertTrue(self.eng.maintain(now=100))   # tries, fails
        self.assertFalse(self.eng.maintain(now=101))  # inside the 3s backoff
        self.assertTrue(self.eng.maintain(now=104))   # retries
        self.assertFalse(self.eng.maintain(now=108))  # backoff grew to 6s
        self.assertTrue(self.eng.maintain(now=111))

    def test_success_resets_backoff(self):
        self.eng.router.active = False
        self.eng.router.start_ok = False
        self.eng.maintain(now=100)
        self.eng.router.start_ok = True
        self.assertTrue(self.eng.maintain(now=104))
        self.assertEqual(self.eng._failures, 0)

    def test_stop_disables_maintenance(self):
        self.eng.stop()
        self.live = [self.A]
        self.assertFalse(self.eng.maintain(now=100))


class PromoteTests(unittest.TestCase):
    def test_moves_to_front_preserving_rest(self):
        self.assertEqual(engine_mod.Engine.promote(["a", "b", "c"], "c"), ["c", "a", "b"])

    def test_already_first(self):
        self.assertEqual(engine_mod.Engine.promote(["a", "b"], "a"), ["a", "b"])

    def test_unselected_is_not_added(self):
        self.assertEqual(engine_mod.Engine.promote(["a", "b"], "z"), ["a", "b"])

    def test_returns_a_copy(self):
        original = ["a", "b"]
        self.assertIsNot(engine_mod.Engine.promote(original, "a"), original)


class ApplyTests(EngineCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(winaudio, "set_default_output",
                                    return_value=(True, "ok"))
        self.set_default = patcher.start()
        self.addCleanup(patcher.stop)

    def test_nothing_selected(self):
        self.live = [endpoint("A", "A")]
        ok, msg, _ = self.engine().apply([])
        self.assertFalse(ok)
        self.assertEqual(msg, "nothing selected")

    def test_single_device_makes_it_default_without_router(self):
        self.live = [endpoint("A", "A"), endpoint("B", "B", True)]
        eng = self.engine(selected=["A"])
        ok, msg, detail = eng.apply(["A"])
        self.assertTrue(ok)
        self.set_default.assert_called_once_with("A")
        self.assertEqual(eng.status, engine_mod.STATUS_SINGLE)
        self.assertFalse(eng.active)

    def test_stop_hands_default_back_from_anchor(self):
        self.live = [endpoint("A", "Headphones"), endpoint("S", "Digital S/PDIF", True)]
        eng = self.engine(selected=["A"], boost_all=True)
        eng._anchor_is_default = True  # as set by apply() in boost-all mode
        eng.stop()
        self.set_default.assert_called_once_with("A")
        eng.stop()  # idempotent: no second switch
        self.set_default.assert_called_once()

    def test_stop_without_anchor_leaves_default_alone(self):
        self.live = [endpoint("A", "Headphones", True)]
        self.engine(selected=["A"]).stop()
        self.set_default.assert_not_called()

    def test_stale_ids_are_dropped(self):
        self.live = [endpoint("A", "A", True)]
        ok, _, _ = self.engine(selected=["ghost", "A"]).apply(["ghost", "A"])
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
