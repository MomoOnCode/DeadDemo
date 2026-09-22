"""2D replay viewer: minimap playback with scrub, speed, hero filters and calibration."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from deaddemo.core.assets.catalog import catalog
from deaddemo.core.stats.match_stats import scan
from deaddemo.core.viewer.frames import FrameSet, attach_events, load_frames
from deaddemo.gui.context import AppContext
from deaddemo.gui.map_assets import load_map_assets
from deaddemo.gui.theme import fmt_clock, fmt_souls, team_color
from deaddemo.gui.widgets.minimap_view import MinimapView

SPEEDS = [0.5, 1, 2, 4, 8, 16]
FRAME_MS = 33


class ViewerPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.match = None
        self.frames: FrameSet | None = None
        self.index = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(FRAME_MS)
        self.timer.timeout.connect(self._tick)

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.match_combo = QComboBox()
        self.match_combo.setMinimumWidth(320)
        self.btn_load = QPushButton("Load")
        self.btn_calibrate = QPushButton("Calibrate map…")
        self.btn_fit = QPushButton("Fit")
        self.header = QLabel("")
        top.addWidget(QLabel("Match"))
        top.addWidget(self.match_combo)
        top.addWidget(self.btn_load)
        top.addWidget(self.btn_calibrate)
        top.addWidget(self.btn_fit)
        top.addWidget(self.header, 1)
        layout.addLayout(top)

        body = QHBoxLayout()
        layout.addLayout(body, 1)
        self.view = MinimapView()
        body.addWidget(self.view, 1)
        side = QVBoxLayout()
        body.addLayout(side)
        side.addWidget(QLabel("Heroes"))
        self.hero_list = QListWidget()
        self.hero_list.setMaximumWidth(260)
        side.addWidget(self.hero_list, 1)
        self.trails = QCheckBox("Movement trails")
        self.trails.setChecked(True)
        side.addWidget(self.trails)
        self.stats_label = QLabel("")
        self.stats_label.setMaximumWidth(260)
        self.stats_label.setWordWrap(True)
        side.addWidget(self.stats_label)

        controls = QHBoxLayout()
        self.btn_play = QPushButton("▶")
        self.btn_play.setFixedWidth(36)
        self.btn_back = QPushButton("−10s")
        self.btn_fwd = QPushButton("+10s")
        self.speed = QComboBox()
        self.speed.addItems([f"{s}x" for s in SPEEDS])
        self.speed.setCurrentIndex(1)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.clock = QLabel("0:00")
        self.clock.setMinimumWidth(60)
        self.event_combo = QComboBox()
        self.event_combo.setMinimumWidth(260)
        for w in (self.btn_play, self.btn_back, self.btn_fwd, self.speed):
            controls.addWidget(w)
        controls.addWidget(self.slider, 1)
        controls.addWidget(self.clock)
        controls.addWidget(QLabel("Jump to"))
        controls.addWidget(self.event_combo)
        layout.addLayout(controls)

        self.btn_load.clicked.connect(lambda: self.load_match(self.match_combo.currentData()))
        self.btn_calibrate.clicked.connect(self._calibrate)
        self.btn_fit.clicked.connect(self.view.fit)
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_back.clicked.connect(lambda: self.seek_seconds(-10))
        self.btn_fwd.clicked.connect(lambda: self.seek_seconds(10))
        self.slider.sliderMoved.connect(self._slider_moved)
        self.slider.sliderPressed.connect(self.timer.stop)
        self.event_combo.activated.connect(self._jump_event)
        self.hero_list.itemChanged.connect(self._heroes_changed)
        self.trails.toggled.connect(lambda on: setattr(self.view, "show_trails", on) or self._render())
        ctx.events.matches_changed.connect(self._fill_matches)
        ctx.events.viewer_seek.connect(self.seek_to_seconds)
        self._pending_seek: float | None = None
        self._fill_matches()

    def seek_to_seconds(self, seconds: float) -> None:
        if self.frames is None:
            self._pending_seek = seconds
            return
        self.index = float(self.frames.index_for_seconds(seconds))
        self._render()

    # -- data ------------------------------------------------------------------------
    def _fill_matches(self) -> None:
        current = self.match_combo.currentData()
        self.match_combo.blockSignals(True)
        self.match_combo.clear()
        for m in self.ctx.matches.all():
            if m.has_ticks:
                self.match_combo.addItem(f"{m.match_id} — {m.map_name} — {fmt_clock(m.regulation_seconds)}", m.match_id)
        if current is not None:
            i = self.match_combo.findData(current)
            if i >= 0:
                self.match_combo.setCurrentIndex(i)
        self.match_combo.blockSignals(False)

    def load_match(self, match_id: int | None) -> None:
        if match_id is None:
            return
        self.timer.stop()
        match = self.ctx.matches.get(match_id)
        if match is None or not match.has_ticks:
            self.ctx.status("This match has no position data (analyze it, or re-analyze after 'Delete positions')")
            return
        self.match = match
        players = self.ctx.matches.players(match_id)
        team_of = {p.hero_id: p.team_num or 0 for p in players}
        i = self.match_combo.findData(match_id)
        if i >= 0:
            self.match_combo.setCurrentIndex(i)
        self.header.setText(f"Loading match {match_id}…")
        step = max(1, self.ctx.settings.viewer_tick_step)

        def work(progress, cancel):
            fs = load_frames(match, team_of, step)
            if fs is None:
                raise RuntimeError("player_ticks parquet missing")
            attach_events(fs, [dict(r) for r in self.ctx.matches.kills(match_id)],
                          [dict(r) for r in self.ctx.matches.objective_events(match_id)], scan(match, "objectives"))
            return fs

        def done(fs: FrameSet) -> None:
            self.frames = fs
            assets = load_map_assets(self.ctx, match.map_name or "unknown")
            self.view.set_assets(assets)
            cat = catalog()
            names = {p.hero_id: p.player_name or "" for p in players}
            labels = {h: cat.hero_name(h) for h in fs.hero_ids}
            self.view.set_frames(fs, labels)
            self.hero_list.blockSignals(True)
            self.hero_list.clear()
            for h in sorted(fs.hero_ids, key=lambda h: (fs.team_of.get(h, 0), h)):
                item = QListWidgetItem(f"{labels[h]} — {names.get(h, '')}")
                item.setData(Qt.ItemDataRole.UserRole, h)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)
                item.setForeground(team_color(fs.team_of.get(h)))
                self.hero_list.addItem(item)
            self.hero_list.blockSignals(False)
            self.view.hidden_heroes = set()
            self.slider.setRange(0, fs.n_frames - 1)
            self.event_combo.clear()
            self.event_combo.addItem("— events —", None)
            for k in fs.kills:
                self.event_combo.addItem(f"{fmt_clock(k.match_seconds)} kill: {labels.get(k.attacker, '?')} → "
                                         f"{labels.get(k.victim, '?')}", k.tick)
            for o in fs.objectives:
                self.event_combo.addItem(f"{fmt_clock(o.match_seconds)} {o.objective_type} destroyed", o.tick)
            self.index = float(fs.index_for_seconds(self._pending_seek if self._pending_seek is not None else 0.0))
            self._pending_seek = None
            self.header.setText(f"Match {match_id} — {match.map_name} — {fs.n_frames} frames "
                                f"(every {step} ticks) — calibration: {assets.calibration.source}")
            self._render()
            self.view.fit()

        self.ctx.jobs.submit(f"frames:{match_id}", work, on_finished=done,
                             on_failed=lambda e: self.ctx.status(f"Viewer load failed: {e.splitlines()[0]}", 10000))

    # -- playback ----------------------------------------------------------------------
    def toggle_play(self) -> None:
        if not self.frames:
            return
        if self.timer.isActive():
            self.timer.stop()
            self.btn_play.setText("▶")
        else:
            self.timer.start()
            self.btn_play.setText("❚❚")

    def _tick(self) -> None:
        if not self.frames or not self.match:
            return
        speed = SPEEDS[self.speed.currentIndex()]
        step = max(1, self.ctx.settings.viewer_tick_step)
        frames_per_ms = (self.match.tick_rate or 64) / step / 1000.0
        self.index += speed * FRAME_MS * frames_per_ms
        if self.index >= self.frames.n_frames - 1:
            self.index = float(self.frames.n_frames - 1)
            self.toggle_play()
        self._render()

    def seek_seconds(self, delta: float) -> None:
        if not self.frames:
            return
        i = int(self.index)
        target = float(self.frames.seconds[i]) + delta
        self.index = float(self.frames.index_for_seconds(target))
        self._render()

    def _slider_moved(self, value: int) -> None:
        self.index = float(value)
        self._render()

    def _jump_event(self, combo_index: int) -> None:
        tick = self.event_combo.itemData(combo_index)
        if tick is not None and self.frames:
            self.index = float(max(0, self.frames.index_for_tick(int(tick)) - 60))
            self._render()

    def _heroes_changed(self, _item) -> None:
        hidden = set()
        for i in range(self.hero_list.count()):
            it = self.hero_list.item(i)
            if it.checkState() != Qt.CheckState.Checked:
                hidden.add(it.data(Qt.ItemDataRole.UserRole))
        self.view.hidden_heroes = hidden
        self._render()

    def _render(self) -> None:
        if not self.frames:
            return
        i = int(self.index)
        trail = int(6 * (self.match.tick_rate or 64) / max(1, self.ctx.settings.viewer_tick_step))
        self.view.show_frame(i, trail_frames=trail if self.trails.isChecked() else 0)
        self.slider.blockSignals(True)
        self.slider.setValue(i)
        self.slider.blockSignals(False)
        self.clock.setText(fmt_clock(float(self.frames.seconds[i])))
        fs = self.frames
        cat = catalog()
        lines = []
        for team in (2, 3):
            idx = [k for k, h in enumerate(fs.hero_ids) if fs.team_of.get(h) == team]
            total = int(fs.souls[i, idx].sum()) if idx else 0
            lines.append(f"<b style='color:{team_color(team).name()}'>{'Amber' if team == 2 else 'Sapphire'}</b> "
                         f"{fmt_souls(total)} souls")
            for k in idx:
                h = fs.hero_ids[k]
                st = "" if fs.alive[i, k] else " (dead)"
                lines.append(
                    f"&nbsp;&nbsp;{cat.hero_name(h)} L{int(fs.level[i, k])} {fmt_souls(int(fs.souls[i, k]))}{st}"
                )
        self.stats_label.setText("<br>".join(lines))

    def _calibrate(self) -> None:
        if not self.match:
            self.ctx.status("Load a match first")
            return
        from deaddemo.gui.widgets.calibration_dialog import CalibrationDialog

        dlg = CalibrationDialog(self.ctx, self.match, self)
        if dlg.exec():
            self.load_match(self.match.match_id)
