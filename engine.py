"""Turns a list of checked devices into actual routing.

Normally the first checked device is the primary: it becomes the Windows
default and plays natively, so its path is untouched. Every other checked
device is a mirror, fed by the router from a loopback capture of the primary.
Checking exactly one device is therefore just a fast default-device switch,
with no capture running at all.

In "boost all" mode (settings["boost_all"]) that changes: WASAPI has no way
to add gain to a device that plays natively, since nothing of ours is in that
path to scale. So instead a spare device nobody listens to -- the "anchor",
settings["boost_anchor_id"] -- quietly becomes the real Windows default, and
every checked device becomes a mirror of *that*, gaining boost control at the
cost of the small mirror buffer latency on all of them, including what would
otherwise have been the native primary.
"""

import router as router_mod
import winaudio

STATUS_IDLE = "idle"
STATUS_SINGLE = "single"
STATUS_MIRRORING = "mirroring"
STATUS_ERROR = "error"


class Engine:
    def __init__(self, settings):
        self.settings = settings
        self.router = router_mod.Router()
        self.endpoints = []
        self.status = STATUS_IDLE
        self.message = "nothing selected"

    # -- devices -----------------------------------------------------------

    def refresh(self):
        self.endpoints = winaudio.list_outputs()
        return self.endpoints

    def by_id(self, endpoint_id):
        for endpoint in self.endpoints:
            if endpoint.id == endpoint_id:
                return endpoint
        return None

    @property
    def max_outputs(self):
        # No engine limit any more -- every endpoint Windows exposes can be
        # driven. Kept so the panel can still warn about silly selections.
        return max(len(self.endpoints), 1)

    @property
    def active(self):
        return self.router.active

    def stats(self):
        return self.router.stats()

    # -- applying ----------------------------------------------------------

    def apply(self, selected_ids):
        """Route to the selected devices. Returns (ok, message, detail)."""
        self.refresh()
        chosen = [self.by_id(i) for i in selected_ids]
        chosen = [endpoint for endpoint in chosen if endpoint is not None]

        if not chosen:
            self.router.stop()
            self.status, self.message = STATUS_IDLE, "nothing selected"
            return False, self.message, []

        detail = []
        boost_all = self.settings.get("boost_all")
        anchor = self._resolve_anchor(selected_ids) if boost_all else None
        if boost_all and anchor is None:
            detail.append(("--", "boost", False,
                          "boost-all needs a spare device free to use "
                          "internally -- uncheck one, or turn boost-all off"))

        if anchor is not None:
            primary, mirrors = anchor, chosen
        else:
            primary, mirrors = chosen[0], chosen[1:]

        if self.settings.get("set_windows_default", True) and not primary.is_default:
            ok, note = winaudio.set_default_output(primary.id)
            detail.append(("PRIM", primary.name, ok, note))
            if ok:
                self.refresh()

        if not mirrors:
            self.router.stop()
            self.status = STATUS_SINGLE
            self.message = f"{primary.name} only -- native path, no added latency"
            return True, self.message, detail

        ok, message = self.router.start(
            primary, mirrors,
            delays=self.settings.get("delays", {}),
            gains=self.settings.get("gains", {}),
            target_fill_ms=self.settings.get("target_fill_ms"))

        if not ok:
            self.status, self.message = STATUS_ERROR, message
            detail.append(("--", "router", False, message))
            return False, message, detail

        if anchor is not None:
            message = (f"boosting {len(mirrors)} device(s) -- {anchor.name} is "
                      "used internally, silently, as the capture source")
        self.status, self.message = STATUS_MIRRORING, message
        for mirror in mirrors:
            detail.append(("MIR", mirror.name, True, "mirroring"))
        return True, message, detail

    def _resolve_anchor(self, selected_ids):
        """The hidden device used as a silent capture source in boost-all mode.

        It must not be a device the user actually wants to hear: loopback
        captures whatever plays on it, and it becomes the real Windows
        default, so anything genuinely connected there would start playing
        the full mix too. Sticks with the saved anchor across calls; only
        re-picks if it has gone away or the user has started actually using
        it (checked it themselves).
        """
        anchor_id = self.settings.get("boost_anchor_id")
        anchor = self.by_id(anchor_id) if anchor_id else None
        if anchor is None or anchor.id in selected_ids:
            candidates = [e for e in self.endpoints if e.id not in selected_ids]
            # A digital/optical output is far more likely to genuinely be
            # sitting unused than something that looks like real speakers --
            # prefer one of those over a blind first-available pick, which
            # could otherwise silently start playing audio out of a monitor
            # or speaker set nobody meant to touch.
            anchor = next(
                (e for e in candidates if any(
                    k in e.name.lower() for k in ("s/pdif", "spdif", "digital", "optical"))),
                candidates[0] if candidates else None)
            self.settings["boost_anchor_id"] = anchor.id if anchor else None
        return anchor

    def set_delay(self, endpoint_id, delay_ms):
        """Live delay change -- no need to tear the router down."""
        self.settings.setdefault("delays", {})[endpoint_id] = delay_ms
        return self.router.set_delay(endpoint_id, delay_ms)

    def set_gain(self, endpoint_id, gain):
        """Live digital boost change for a mirror, 1.0 = unity, up to MAX_GAIN.

        Only works on mirrors -- the primary plays through Windows' own
        native path, which this process never touches, so there is nothing
        of ours to scale. See winaudio.set_volume for the OS-level 0-100%
        volume, which works on any device including the primary.
        """
        gain = max(0.0, min(router_mod.MAX_GAIN, float(gain)))
        self.settings.setdefault("gains", {})[endpoint_id] = gain
        return self.router.set_gain(endpoint_id, gain)

    def get_volume(self, endpoint_id):
        """Current master volume for one endpoint, 0.0-1.0, or None.

        This is the Windows per-device volume (the same value the OS itself
        remembers), not a gain on our mirror stream -- so it works for the
        primary too, which has no stream of ours to scale.
        """
        return winaudio.get_volume(endpoint_id)

    def set_volume(self, endpoint_id, level):
        return winaudio.set_volume(endpoint_id, level)

    def stop(self):
        self.router.stop()
        self.status, self.message = STATUS_IDLE, "stopped"
