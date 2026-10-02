"""The popup panel -- a replacement for the Windows sound flyout that allows
more than one output to be checked at once.

Deliberately shaped like the flyout it stands in for: frameless, dark, anchored
above the tray, dismissed with Escape or by clicking away.

The first checked device is the primary and carries a PRIM tag; the rest are
mirrors. Only mirrors get a delay box and a boost slider, because the primary
plays through the untouched native path and there is nothing in it for us to
delay or scale.
"""

import tkinter as tk
from tkinter import ttk

import router as router_mod

BG = "#202020"
BG_ROW = "#2b2b2b"
BG_ROW_ON = "#323a45"
FG = "#f0f0f0"
FG_DIM = "#9a9a9a"
ACCENT = "#4cc2ff"
GOOD = "#6fcf7f"
WARN = "#ffb454"
BAD = "#ff6b6b"

WIDTH = 560
TASKBAR_MARGIN = 56
STATS_INTERVAL_MS = 700


class Panel:
    def __init__(self, root, engine, settings, on_change):
        self.root = root
        self.engine = engine
        self.settings = settings
        self.on_change = on_change
        self.visible = False
        self.rows = []
        self._suspend_apply = False
        self._stats_job = None

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
                 text="OUTPUT DEVICES  --  first checked plays natively, rest mirror it",
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
                               font=("Segoe UI", 8), wraplength=WIDTH - 28,
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

            tag = tk.Label(top, text="", bg=BG_ROW, fg=ACCENT,
                           font=("Consolas", 8), width=4)
            tag.pack(side="right", padx=(4, 8))

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

            tk.Label(vol_row, text="Vol", bg=BG_ROW, fg=FG_DIM,
                     font=("Segoe UI", 8)).pack(side="left", padx=(0, 6))
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
                     font=("Segoe UI", 8)).pack(side="left", padx=(0, 6))
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

            self.rows.append({"endpoint": endpoint, "var": var, "tag": tag,
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
            entry["tag"].configure(text="PRIM" if is_primary else "MIR",
                                   fg=GOOD if is_primary else ACCENT)
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

    def _refresh_stats(self):
        """Live per-mirror offset, so drift correction is visible."""
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
        if self.visible:
            self._stats_job = self.window.after(STATS_INTERVAL_MS,
                                                self._refresh_stats)

    def _resize(self):
        self.window.update_idletasks()
        height = self.window.winfo_reqheight()
        screen_w = self.window.winfo_screenwidth()
        screen_h = self.window.winfo_screenheight()
        x = screen_w - WIDTH - 12
        y = screen_h - TASKBAR_MARGIN - height - 8
        self.window.geometry(f"{WIDTH}x{height}+{max(x, 0)}+{max(y, 0)}")

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

    def _apply(self):
        ok, message, detail = self.engine.apply(self.settings.get("selected", []))
        problems = [f"{tag} {name}: {note}"
                    for tag, name, good, note in detail if not good]
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

    def hide(self):
        if self._stats_job is not None:
            self.window.after_cancel(self._stats_job)
            self._stats_job = None
        self.window.withdraw()
        self.visible = False

    def toggle(self):
        self.hide() if self.visible else self.show()
