"""The popup panel -- a replacement for the Windows sound flyout that allows
more than one output to be checked at once.

Deliberately shaped like the flyout it stands in for: frameless, dark, anchored
above the tray, dismissed with Escape or by clicking away.

The first checked device is the primary and carries a PRIM tag; the rest are
mirrors. Only mirrors get a delay box and a boost slider, because the primary
plays through the untouched native path and there is nothing in it for us to
delay or scale.
"""

import threading
import tkinter as tk
from tkinter import ttk

import router as router_mod
import tone
import winaudio
import winui

BG = "#202020"
BG_ROW = "#2b2b2b"
BG_ROW_ON = "#323a45"
FG = "#f0f0f0"
FG_DIM = "#9a9a9a"
ACCENT = "#4cc2ff"
GOOD = "#6fcf7f"
WARN = "#ffb454"
BAD = "#ff6b6b"

WIDTH = 560  # at 96 DPI; scaled by the display's DPI in Panel.__init__
STATS_INTERVAL_MS = 700
METER_INTERVAL_MS = 80


class Panel:
    def __init__(self, root, engine, settings, on_change):
        self.root = root
        self.engine = engine
        self.settings = settings
        self.on_change = on_change
        self.visible = False
        self._meter_job = None
        self.rows = []
        self._suspend_apply = False
        self._stats_job = None

        # Fixed pixel sizes below are authored at 96 DPI; Tk scales fonts on
        # its own, so only the pixel dimensions need this.
        self.scale = winui.scale_factor(root.winfo_fpixels("1i"))
        self.width = int(WIDTH * self.scale)

        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg=BG)

        self._build_styles()
        self._build_chrome()

        self.window.bind("<Escape>", lambda _e: self.hide())
        self.window.bind("<FocusOut>", self._maybe_hide)

    # -- construction ------------------------------------------------------

    def _build_styles(self):
        style = ttk.Style(self.window)
        style.theme_use("clam")
        style.configure("Panel.TCheckbutton", background=BG_ROW, foreground=FG,
                        focuscolor=BG_ROW, borderwidth=0)
        style.map("Panel.TCheckbutton",
                  background=[("active", BG_ROW_ON)],
                  foreground=[("disabled", FG_DIM)])
        style.configure("Panel.TSpinbox", fieldbackground=BG, foreground=FG,
                        background=BG_ROW, arrowcolor=FG, borderwidth=0)
        style.configure("Panel.Horizontal.TScale", background=BG_ROW,
                        troughcolor=BG, sliderthickness=12, sliderlength=12)
        style.configure("PanelTop.TCheckbutton", background=BG, foreground=FG,
                        focuscolor=BG, borderwidth=0)
        style.map("PanelTop.TCheckbutton",
                  background=[("active", BG)],
                  foreground=[("disabled", FG_DIM)])

    def _build_chrome(self):
        header = tk.Frame(self.window, bg=BG)
        header.pack(fill="x", padx=14, pady=(12, 6))
        tk.Label(header, text="Sound output", bg=BG, fg=FG,
                 font=("Segoe UI Semibold", 11)).pack(side="left")
        self.hint = tk.Label(header, text="", bg=BG, fg=FG_DIM,
                             font=("Segoe UI", 8))
        self.hint.pack(side="right")

        tk.Label(self.window,
                 text="OUTPUT DEVICES  --  first checked plays natively, rest mirror it  (click MIR to make primary)",
                 bg=BG, fg=FG_DIM, font=("Segoe UI", 8)).pack(anchor="w", padx=14)

        boost_all_row = tk.Frame(self.window, bg=BG)
        boost_all_row.pack(fill="x", padx=14, pady=(2, 2))
        self.boost_all_var = tk.BooleanVar(value=self.settings.get("boost_all", False))
        ttk.Checkbutton(
            boost_all_row,
            text="Boost every device, including the native one (~20ms added to all)",
            variable=self.boost_all_var, style="PanelTop.TCheckbutton",
            command=self._boost_all_toggled
        ).pack(side="left")

        self.device_area = tk.Frame(self.window, bg=BG)
        self.device_area.pack(fill="x", padx=10, pady=(4, 6))

        self.status = tk.Label(self.window, text="", bg=BG, fg=FG_DIM,
                               font=("Segoe UI", 8), wraplength=self.width - int(28 * self.scale),
                               justify="left", anchor="w")
        self.status.pack(fill="x", padx=14, pady=(0, 4))

        footer = tk.Frame(self.window, bg=BG)
        footer.pack(fill="x", padx=14, pady=(2, 12))
        self.engine_label = tk.Label(footer, text="", bg=BG, fg=FG_DIM,
                                     font=("Segoe UI", 8))
        self.engine_label.pack(side="left")
        tk.Button(footer, text="Refresh", command=self.reload, bg=BG_ROW, fg=FG,
                  relief="flat", font=("Segoe UI", 8), padx=10,
                  activebackground=BG_ROW_ON, activeforeground=FG,
                  borderwidth=0).pack(side="right")

    # -- content -----------------------------------------------------------

    def reload(self):
        for child in self.device_area.winfo_children():
            child.destroy()
        self.rows = []

        endpoints = self.engine.refresh()
        selected = list(self.settings.get("selected", []))
        delays = self.settings.get("delays", {})

        self._suspend_apply = True
        for endpoint in endpoints:
            row = tk.Frame(self.device_area, bg=BG_ROW)
            row.pack(fill="x", pady=1)

            top = tk.Frame(row, bg=BG_ROW)
            top.pack(fill="x")

            var = tk.BooleanVar(value=endpoint.id in selected)
            ttk.Checkbutton(
                top, text=endpoint.name, variable=var, style="Panel.TCheckbutton",
                command=lambda e=endpoint, v=var: self._toggled(e, v)
            ).pack(side="left", padx=(8, 4), pady=6)

            test_btn = tk.Button(
                top, text="Test", bg=BG, fg=FG_DIM, relief="flat", borderwidth=0,
                font=("Segoe UI", 8), padx=6, activebackground=BG_ROW_ON,
                activeforeground=FG,
                command=lambda e=endpoint: self._test_tone(e))
            test_btn.pack(side="right", padx=(0, 6))

            tag = tk.Label(top, text="", bg=BG_ROW, fg=ACCENT,
                           font=("Consolas", 8), width=4)
            tag.pack(side="right", padx=(4, 8))
            tag.bind("<Button-1>", lambda _e, e=endpoint: self._make_primary(e))

            offset = tk.Label(top, text="", bg=BG_ROW, fg=FG_DIM,
                              font=("Consolas", 8), width=7)
            offset.pack(side="right")

            delay_var = tk.IntVar(value=int(delays.get(endpoint.id, 0)))
            delay_box = ttk.Spinbox(
                top, from_=0, to=500, increment=5, width=4, textvariable=delay_var,
                style="Panel.TSpinbox", font=("Consolas", 9),
                command=lambda e=endpoint, v=delay_var: self._delay_changed(e, v))
            delay_box.bind("<FocusOut>",
                           lambda _e, ep=endpoint, v=delay_var:
                           self._delay_changed(ep, v))
            delay_box.pack(side="right")
            tk.Label(top, text="ms", bg=BG_ROW, fg=FG_DIM,
                     font=("Segoe UI", 8)).pack(side="right", padx=(10, 3))

            # A live Windows per-device volume, independent of PRIM/MIR --
            # it works even for unchecked devices, since it is not our
            # stream being scaled but the OS endpoint itself.
            vol_row = tk.Frame(row, bg=BG_ROW)
            vol_row.pack(fill="x", padx=(30, 12), pady=(0, 6))

            level = self.engine.get_volume(endpoint.id)
            pct = int(round((level or 0.0) * 100))

            muted = self.engine.get_mute(endpoint.id)
            mute_btn = tk.Button(
                vol_row, text="Vol", bg=BG_ROW, fg=FG_DIM, relief="flat",
                borderwidth=0, font=("Segoe UI", 8), padx=2, pady=0, width=6,
                activebackground=BG_ROW_ON, activeforeground=FG,
                command=lambda e=endpoint: self._mute_clicked(e))
            if muted is None:
                mute_btn.configure(state="disabled")
            mute_btn.pack(side="left", padx=(0, 6))
            self._paint_mute(mute_btn, muted)
            vol_label = tk.Label(vol_row, text=f"{pct}%" if level is not None else "--",
                                 bg=BG_ROW, fg=FG_DIM, font=("Consolas", 8),
                                 width=4, anchor="e")
            vol_label.pack(side="right")
            vol_scale = ttk.Scale(
                vol_row, from_=0, to=100, orient="horizontal",
                style="Panel.Horizontal.TScale",
                command=lambda v, e=endpoint, lbl=vol_label:
                self._volume_changed(e, lbl, v))
            vol_scale.set(pct)
            if level is None:
                vol_scale.state(["disabled"])
            vol_scale.pack(side="left", fill="x", expand=True)
            # While a slider is held, don't let the sync poll fight the drag.
            drag = {"on": False}
            vol_scale.bind("<ButtonPress-1>", lambda _e, d=drag: d.update(on=True), add="+")
            vol_scale.bind("<ButtonRelease-1>", lambda _e, d=drag: d.update(on=False), add="+")

            # Digital boost past 100% -- only meaningful on a mirror, since
            # that is the only audio that passes through code of ours able
            # to scale samples. The primary plays Windows' native path
            # untouched; see engine.set_gain.
            boost_row = tk.Frame(row, bg=BG_ROW)
            boost_row.pack(fill="x", padx=(30, 12), pady=(0, 6))

            gain = float(self.settings.get("gains", {}).get(endpoint.id, 1.0))
            boost_pct = int(round(gain * 100))
            max_boost = int(router_mod.MAX_GAIN * 100)

            tk.Label(boost_row, text="Boost", bg=BG_ROW, fg=FG_DIM,
                     font=("Segoe UI", 8), width=6).pack(side="left", padx=(0, 6))
            boost_label = tk.Label(boost_row, text=f"{boost_pct}%", bg=BG_ROW,
                                   fg=FG_DIM, font=("Consolas", 8), width=4,
                                   anchor="e")
            boost_label.pack(side="right")
            boost_scale = ttk.Scale(
                boost_row, from_=100, to=max_boost, orient="horizontal",
                style="Panel.Horizontal.TScale",
                command=lambda v, e=endpoint, lbl=boost_label:
                self._boost_changed(e, lbl, v))
            boost_scale.set(boost_pct)
            boost_scale.pack(side="left", fill="x", expand=True)
            self._bind_scale_extras(vol_scale, step=5, default=None)
            self._bind_scale_extras(boost_scale, step=10, default=100)

            # Live signal level actually delivered to this endpoint -- the
            # quickest way to tell "PC is sending audio" from "device is mute".
            meter_canvas = tk.Canvas(row, height=int(3 * self.scale), bg=BG, highlightthickness=0)
            meter_canvas.pack(fill="x", padx=(30, 12), pady=(0, 6))
            meter_bar = meter_canvas.create_rectangle(0, 0, 0, int(3 * self.scale),
                                                         width=0, fill=GOOD)

            self.rows.append({"endpoint": endpoint, "var": var, "tag": tag,
                              "mute_btn": mute_btn, "muted": muted,
                              "vol_scale": vol_scale, "vol_label": vol_label,
                              "vol_drag": drag,
                              "meter": winaudio.PeakMeter(endpoint.id),
                              "meter_canvas": meter_canvas, "meter_bar": meter_bar,
                              "offset": offset, "delay": delay_var,
                              "box": delay_box, "row": row, "top": top,
                              "vol_row": vol_row, "boost_row": boost_row,
                              "boost": boost_scale, "boost_label": boost_label})

        self._suspend_apply = False
        self._renumber()
        self._update_engine_label()
        self._resize()

    def _renumber(self):
        """Tag rows PRIM/MIR by position, and gate the delay boxes."""
        order = list(self.settings.get("selected", []))
        for entry in self.rows:
            endpoint_id = entry["endpoint"].id
            if endpoint_id not in order:
                entry["tag"].configure(text="")
                entry["offset"].configure(text="")
                entry["row"].configure(bg=BG_ROW)
                entry["top"].configure(bg=BG_ROW)
                entry["vol_row"].configure(bg=BG_ROW)
                entry["boost_row"].configure(bg=BG_ROW)
                entry["box"].state(["disabled"])
                entry["boost"].state(["disabled"])
                entry["boost_label"].configure(text="n/a")
                continue

            # In boost-all mode every checked device is a mirror of the
            # hidden anchor -- none of them get the PRIM/native treatment.
            is_primary = (not self.settings.get("boost_all")
                         and order.index(endpoint_id) == 0)
            # A MIR tag is a button: click it to make that device the primary.
            promotable = not is_primary and not self.settings.get("boost_all")
            entry["tag"].configure(text="PRIM" if is_primary else "MIR",
                                   fg=GOOD if is_primary else ACCENT,
                                   cursor="hand2" if promotable else "")
            entry["row"].configure(bg=BG_ROW_ON)
            entry["top"].configure(bg=BG_ROW_ON)
            entry["vol_row"].configure(bg=BG_ROW_ON)
            entry["boost_row"].configure(bg=BG_ROW_ON)
            # The primary is the untouched native path; there is nothing of
            # ours in it to delay or to boost.
            entry["box"].state(["disabled"] if is_primary else ["!disabled"])
            entry["boost"].state(["disabled"] if is_primary else ["!disabled"])
            if is_primary:
                entry["offset"].configure(text="0.0ms", fg=GOOD)
                entry["boost_label"].configure(text="n/a")
            else:
                # Coming back from "n/a" (was primary or unchecked) --
                # restore the real percentage rather than leave it stale.
                pct = int(round(float(entry["boost"].get())))
                entry["boost_label"].configure(text=f"{pct}%")

    def _update_engine_label(self):
        if self.engine.active:
            text = f"Native WASAPI  |  {self.engine.router.describe_format()}"
            colour = FG_DIM
        elif self.engine.status == "single":
            text = "Native path only -- no capture running"
            colour = FG_DIM
        elif self.engine.status == "error":
            text = f"Engine: {self.engine.message}"
            colour = BAD
        else:
            text = "Idle -- check a device to begin"
            colour = FG_DIM
        self.engine_label.configure(text=text, fg=colour)
        self.hint.configure(text=self.settings.get("hotkey", {}).get("label", ""))

    def _sync_volumes(self):
        """Follow volume/mute changes made elsewhere (keyboard keys, other apps)."""
        for entry in self.rows:
            endpoint_id = entry["endpoint"].id
            level = self.engine.get_volume(endpoint_id)
            if level is not None and not entry["vol_drag"]["on"]:
                pct = int(round(level * 100))
                if pct != int(round(float(entry["vol_scale"].get()))):
                    self._suspend_apply = True
                    try:
                        entry["vol_scale"].set(pct)
                        entry["vol_label"].configure(text=f"{pct}%")
                    finally:
                        self._suspend_apply = False
            muted = self.engine.get_mute(endpoint_id)
            if muted is not None and muted != entry["muted"]:
                entry["muted"] = muted
                self._paint_mute(entry["mute_btn"], muted)

    def _refresh_stats(self):
        """Live per-mirror offset, so drift correction is visible."""
        self._sync_volumes()
        stats = {s["id"]: s for s in self.engine.stats()}
        for entry in self.rows:
            stat = stats.get(entry["endpoint"].id)
            if stat is None:
                continue
            if stat["failed"]:
                entry["offset"].configure(text="lost", fg=BAD)
            elif stat["offset_ms"] is not None:
                entry["offset"].configure(text=f"{stat['offset_ms']:.1f}ms",
                                          fg=FG_DIM)
            # Amber boost percentage = peaks are currently being squashed by
            # the limiter, i.e. more boost would only add distortion.
            entry["boost_label"].configure(
                fg=WARN if stat.get("limiting") and stat["gain"] > 1.0 else FG_DIM)
        if self.visible:
            self._stats_job = self.window.after(STATS_INTERVAL_MS,
                                                self._refresh_stats)

    def _tick_meters(self):
        for entry in self.rows:
            canvas = entry["meter_canvas"]
            fraction = winaudio.peak_to_fraction(entry["meter"].peak())
            width = canvas.winfo_width()
            canvas.coords(entry["meter_bar"], 0, 0, int(width * fraction),
                          int(3 * self.scale))
            canvas.itemconfigure(
                entry["meter_bar"],
                fill=BAD if fraction > 0.97 else WARN if fraction > 0.85 else GOOD)
        if self.visible:
            self._meter_job = self.window.after(METER_INTERVAL_MS, self._tick_meters)

    def _resize(self):
        self.window.update_idletasks()
        height = self.window.winfo_reqheight()
        x, y = winui.place_popup(winui.work_area(), winui.screen_bounds(),
                                 self.width, height, gap=int(12 * self.scale))
        self.window.geometry(f"{self.width}x{height}+{x}+{y}")

    # -- interaction -------------------------------------------------------

    def _toggled(self, endpoint, var):
        if self._suspend_apply:
            return
        selected = list(self.settings.get("selected", []))
        if var.get():
            if endpoint.id not in selected:
                selected.append(endpoint.id)  # order decides primary vs mirror
        elif endpoint.id in selected:
            selected.remove(endpoint.id)
        self.settings["selected"] = selected
        self._renumber()
        self._apply()

    def _test_tone(self, endpoint):
        """Beep this device and report whether the PC delivered the signal."""
        self.show_status(f"Playing test tone on {endpoint.name}...")
        outcome = {}

        def work():
            outcome["result"] = tone.play(endpoint.id)

        thread = threading.Thread(target=work, daemon=True, name="test-tone")
        thread.start()

        def poll():
            if thread.is_alive():
                self.window.after(100, poll)
                return
            ok, message = outcome.get("result", (False, "test tone failed"))
            self.show_status(f"{endpoint.name}: {message}", ok)
            self._resize()

        self.window.after(100, poll)

    def _make_primary(self, endpoint):
        selected = list(self.settings.get("selected", []))
        if (self.settings.get("boost_all") or endpoint.id not in selected
                or selected[0] == endpoint.id):
            return
        self.settings["selected"] = self.engine.promote(selected, endpoint.id)
        self.reload()
        self._apply()

    def _boost_all_toggled(self):
        self.settings["boost_all"] = self.boost_all_var.get()
        # Which rows count as PRIM vs MIR (and which get boost-enabled)
        # changes with this flag, so rebuild the rows, then re-route.
        self.reload()
        self._apply()

    def _delay_changed(self, endpoint, var):
        if self._suspend_apply:
            return
        try:
            value = max(0, min(500, int(var.get())))
        except (tk.TclError, ValueError):
            return
        # Applied live -- no need to tear the stream down and rebuild it.
        self.engine.set_delay(endpoint.id, value)
        self.on_change()

    @staticmethod
    def _bind_scale_extras(scale, step, default):
        """Mouse wheel nudges by `step`; double-click snaps to `default`."""
        def wheel(event):
            if str(scale.cget("state")) == "disabled" or "disabled" in scale.state():
                return "break"
            lo, hi = float(scale.cget("from")), float(scale.cget("to"))
            direction = 1 if event.delta > 0 else -1
            scale.set(max(lo, min(hi, float(scale.get()) + direction * step)))
            return "break"

        scale.bind("<MouseWheel>", wheel)
        if default is not None:
            def reset(_event):
                if "disabled" not in scale.state():
                    scale.set(default)
                return "break"
            scale.bind("<Double-Button-1>", reset)

    @staticmethod
    def _paint_mute(button, muted):
        # The "Vol" caption doubles as the mute switch: click to toggle.
        button.configure(text="MUTED" if muted else "Vol",
                         fg=BAD if muted else FG_DIM)

    def _mute_clicked(self, endpoint):
        entry = next(r for r in self.rows if r["endpoint"].id == endpoint.id)
        want = not bool(entry["muted"])
        ok, note = self.engine.set_mute(endpoint.id, want)
        if not ok:
            self.show_status(f"{endpoint.name}: {note}", False)
            return
        entry["muted"] = want
        self._paint_mute(entry["mute_btn"], want)

    def _volume_changed(self, endpoint, label, value):
        if self._suspend_apply:
            return
        level = max(0, min(100, round(float(value))))
        label.configure(text=f"{level}%")
        # This is the OS endpoint volume, so Windows remembers it on its own
        # -- nothing for us to persist or restore on start.
        self.engine.set_volume(endpoint.id, level / 100.0)

    def _boost_changed(self, endpoint, label, value):
        if self._suspend_apply:
            return
        max_boost = int(router_mod.MAX_GAIN * 100)
        pct = max(100, min(max_boost, round(float(value))))
        label.configure(text=f"{pct}%")
        # Unlike volume this is our own digital gain, so it does need
        # persisting/restoring -- see engine.set_gain.
        self.engine.set_gain(endpoint.id, pct / 100.0)
        self.on_change()

    def apply_selection(self):
        """Re-route to the current selection (also used by the tray menu)."""
        self._apply()

    def show_status(self, message, ok=True):
        self.status.configure(text=message, fg=FG_DIM if ok else BAD)
        self._update_engine_label()

    def _apply(self):
        ok, message, detail = self.engine.apply(self.settings.get("selected", []))
        problems = [f"{tag} {name}: {note}"
                    for tag, name, good, note in detail if not good]
        muted = [entry["endpoint"].name for entry in self.rows
                 if entry["endpoint"].id in self.settings.get("selected", [])
                 and self.engine.get_mute(entry["endpoint"].id)]
        if muted and not problems:
            problems = [f"muted in Windows: {', '.join(muted)} (click Vol/MUTED to unmute)"]
        self.status.configure(text=" | ".join(problems) if problems else message,
                              fg=BAD if problems else (FG_DIM if ok else WARN))
        self._update_engine_label()
        self.on_change()
        self._resize()

    # -- visibility --------------------------------------------------------

    def _maybe_hide(self, _event):
        # Moving between widgets inside the panel also fires FocusOut; only a
        # focus loss with nothing of ours focused means the user clicked away.
        if self.visible and not self.window.focus_get():
            self.hide()

    def show(self):
        self.reload()
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()
        self.visible = True
        self._refresh_stats()
        self._tick_meters()

    def hide(self):
        if self._stats_job is not None:
            self.window.after_cancel(self._stats_job)
            self._stats_job = None
        if self._meter_job is not None:
            self.window.after_cancel(self._meter_job)
            self._meter_job = None
        self.window.withdraw()
        self.visible = False

    def toggle(self):
        self.hide() if self.visible else self.show()
