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

import re
import time

import applog
import config
import router as router_mod
import winaudio

log = applog.get("engine")

STATUS_IDLE = "idle"
STATUS_SINGLE = "single"
STATUS_MIRRORING = "mirroring"
STATUS_ERROR = "error"

# Seconds to wait before re-trying a failed recovery; the last value repeats.
RETRY_BACKOFF_S = (3, 6, 12, 30, 60)


def _base_name(name):
    """Friendly name without Windows' "2 - " de-duplication prefix, which
    shifts as other devices come and go."""
    return re.sub(r"^\d+ - ", "", name)


class Engine:
    def __init__(self, settings):
        self.settings = settings
        self.router = router_mod.Router()
        self.endpoints = []
        self._status = STATUS_IDLE
        self._message = "nothing selected"
        # True while the silent anchor is the Windows default, so stopping can
        # hand the default back instead of leaving the user with no sound.
        self._anchor_is_default = False
        # What maintain() compares against: the devices the last apply() tried
        # to route, and whether routing is meant to be running at all.
        self._routed_ids = ()
        self._applied = False
        self._failures = 0
        self._next_retry = 0.0

    # -- devices -----------------------------------------------------------

    def refresh(self):
        self.endpoints = winaudio.list_outputs()
        self._reconcile_ids()
        return self.endpoints

    def _reconcile_ids(self):
        """Re-attach saved settings to a device whose endpoint id changed.

        Windows regenerates endpoint ids on driver and OS updates, which used
        to leave the saved selection pointing at nothing -- the device was
        silently dropped. A saved id that no longer exists is matched by its
        last-seen name instead, if that name belongs to exactly one current
        device that isn't already referenced.
        """
        names = self.settings.setdefault("device_names", {})
        live = {e.id: e.name for e in self.endpoints}
        by_name = {}
        for endpoint in self.endpoints:
            by_name.setdefault(_base_name(endpoint.name), []).append(endpoint.id)

        s = self.settings
        referenced = set(s.get("selected", [])) | set(s.get("delays", {})) \
            | set(s.get("gains", {})) | {s.get("boost_anchor_id")}
        changed = False
        for old_id in [i for i in referenced if i and i not in live]:
            candidates = by_name.get(_base_name(names.get(old_id, "")), [])
            if len(candidates) != 1 or candidates[0] in referenced:
                continue
            new_id = candidates[0]
            # In place: callers hold the list they passed to apply().
            selected = s.setdefault("selected", [])
            selected[:] = [new_id if i == old_id else i for i in selected]
            for key in ("delays", "gains"):
                if old_id in s.get(key, {}):
                    s[key][new_id] = s[key].pop(old_id)
            if s.get("boost_anchor_id") == old_id:
                s["boost_anchor_id"] = new_id
            referenced.discard(old_id)
            referenced.add(new_id)
            changed = True

        # Drop names of ids that are gone and unreferenced, remember live ones.
        for old_id in [i for i in names if i not in live and i not in referenced]:
            del names[old_id]
        for endpoint_id, name in live.items():
            if names.get(endpoint_id) != name:
                names[endpoint_id] = name
                changed = True
        if changed:
            config.save(self.settings)

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

    @property
    def status(self):
        """What the engine is doing *now*.

        "mirroring" is only true while the router thread is alive; if it died
        (device unplugged, driver reset) the stored status would otherwise keep
        claiming audio is flowing.
        """
        if self._status == STATUS_MIRRORING and not self.router.active:
            return STATUS_ERROR
        return self._status

    @property
    def message(self):
        if self._status == STATUS_MIRRORING and not self.router.active:
            return self.router.error or "mirroring stopped unexpectedly"
        return self._message

    def stats(self):
        return self.router.stats()

    # -- applying ----------------------------------------------------------

    def apply(self, selected_ids):
        """Route to the selected devices. Returns (ok, message, detail)."""
        self.refresh()
        chosen = [self.by_id(i) for i in selected_ids]
        chosen = [endpoint for endpoint in chosen if endpoint is not None]

        self._routed_ids = tuple(e.id for e in chosen)
        self._applied = bool(chosen)
        if not chosen:
            self.router.stop()
            self._status, self._message = STATUS_IDLE, "nothing selected"
            return False, self.message, []

        detail = []
        boost_all = self.settings.get("boost_all")
        anchor = self._resolve_anchor(selected_ids) if boost_all else None
        if boost_all and anchor is None:
            detail.append(("--", "boost", False,
                          "boost-all needs a spare device free to use "
                          "internally -- uncheck one, or turn boost-all off"))

        self._anchor_is_default = anchor is not None
        if anchor is not None:
            primary, mirrors = anchor, chosen
        else:
            primary, mirrors = chosen[0], chosen[1:]

        if self.settings.get("set_windows_default", True) and not primary.is_default:
            ok, note = winaudio.set_default_output(primary.id)
            detail.append(("PRIM", primary.name, ok, note))
            log.info("set default -> %s: %s (%s)", primary.name, ok, note)
            if ok:
                self.refresh()

        if not mirrors:
            self.router.stop()
            self._status = STATUS_SINGLE
            self._message = f"{primary.name} only -- native path, no added latency"
            return True, self.message, detail

        ok, message = self.router.start(
            primary, mirrors,
            delays=self.settings.get("delays", {}),
            gains=self.settings.get("gains", {}),
            target_fill_ms=self.settings.get("target_fill_ms"))

        if not ok:
            self._status, self._message = STATUS_ERROR, message
            log.error("apply failed: %s", message)
            detail.append(("--", "router", False, message))
            return False, message, detail

        if anchor is not None:
            message = (f"boosting {len(mirrors)} device(s) -- {anchor.name} is "
                      "used internally, silently, as the capture source")
        self._status, self._message = STATUS_MIRRORING, message
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

    @staticmethod
    def promote(selected_ids, endpoint_id):
        """`selected_ids` with `endpoint_id` moved to the front (the primary).

        The rest keep their relative order. Unselected ids are left out, so a
        promote can't silently add a device the user hadn't checked.
        """
        if endpoint_id not in selected_ids:
            return list(selected_ids)
        return [endpoint_id] + [i for i in selected_ids if i != endpoint_id]

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

    def get_mute(self, endpoint_id):
        return winaudio.get_mute(endpoint_id)

    def set_mute(self, endpoint_id, muted):
        return winaudio.set_mute(endpoint_id, muted)

    def maintain(self, now=None):
        """Periodic self-heal. Returns True if it re-applied the routing.

        Re-applies when the set of selected devices that currently exist has
        changed (headphones plugged or pulled) or when routing is meant to be
        running but a stream has died. Failures back off, so a device that is
        genuinely gone doesn't get hammered every few seconds.
        """
        if not self._applied:
            return False  # nothing started, or the user stopped it on purpose
        now = time.monotonic() if now is None else now
        if now < self._next_retry:
            return False

        self.refresh()
        selected = self.settings.get("selected", [])
        wanted = tuple(i for i in selected if self.by_id(i))
        # .status (not ._status): a dead router thread reads as ERROR there.
        healthy = (self.status != STATUS_ERROR
                   and not any(s["failed"] for s in self.router.stats()))
        if wanted == self._routed_ids and healthy:
            self._failures = 0
            return False

        log.info("maintain: re-applying (devices changed=%s, healthy=%s)",
                 wanted != self._routed_ids, healthy)
        ok, _message, _detail = self.apply(selected)
        if ok:
            self._failures = 0
        else:
            delay = RETRY_BACKOFF_S[min(self._failures, len(RETRY_BACKOFF_S) - 1)]
            self._failures += 1
            self._next_retry = now + delay
        return True

    def stop(self):
        self._applied = False
        self.router.stop()
        self._restore_default_from_anchor()
        self._status, self._message = STATUS_IDLE, "stopped"

    def _restore_default_from_anchor(self):
        """Undo boost-all's hidden default: the anchor is silent by design.

        Left in place after quitting or stopping, the whole PC would play
        into an unused S/PDIF port. Hand the default to the first checked
        device that still exists -- the one the user was listening to.
        """
        if not self._anchor_is_default:
            return
        self._anchor_is_default = False
        if not self.settings.get("set_windows_default", True):
            return
        self.refresh()
        for endpoint_id in self.settings.get("selected", []):
            target = self.by_id(endpoint_id)
            if target is not None:
                ok, note = winaudio.set_default_output(target.id)
                log.info("anchor released, default -> %s: %s (%s)",
                         target.name, ok, note)
                return
