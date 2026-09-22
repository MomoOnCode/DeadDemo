"""Clips tab in match detail: build sequences and hand them to the recorder."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from deaddemo.core.assets.catalog import catalog
from deaddemo.core.db.repos import MatchPlayerRow, MatchRow
from deaddemo.core.video.sequences import CAMERAS, KINDS, GenerateOptions, Sequence, generate
from deaddemo.gui.context import AppContext
from deaddemo.gui.models.table_model import Column, RowTableModel
from deaddemo.gui.theme import fmt_clock

KIND_LABELS = {"kills": "Kills", "deaths": "Deaths", "multikills": "Multi-kills", "first_blood": "First blood",
               "teamfights": "Teamfights", "objectives": "Objectives destroyed", "midboss": "Mid boss"}


class SequencesModel(RowTableModel):
    def __init__(self, parent=None):
        super().__init__([
            Column("#", lambda s: s.id, align_right=True),
            Column("Label", lambda s: s.label),
            Column("Start", lambda s: s.start_s, fmt_clock, align_right=True),
            Column("End", lambda s: s.end_s, fmt_clock, align_right=True),
            Column("Length", lambda s: s.duration_s, lambda v: f"{v:.0f}s", align_right=True),
            Column("Focus", lambda s: s.focus_name or ""),
            Column("Camera", lambda s: s.camera),
            Column("Speed", lambda s: s.timescale, lambda v: f"{v:g}x", align_right=True),
        ], parent)


class GenerateDialog(QDialog):
    def __init__(self, ctx: AppContext, players: list[MatchPlayerRow], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Auto-generate clips")
        cat = catalog()
        form = QFormLayout(self)
        self.player = QComboBox()
        for p in sorted(players, key=lambda p: (p.team_num or 0, p.hero_id)):
            self.player.addItem(f"{p.player_name} — {cat.hero_name(p.hero_id)}", p.hero_id)
        me = ctx.install.account
        if me:
            for i, p in enumerate(sorted(players, key=lambda p: (p.team_num or 0, p.hero_id))):
                if p.steam_id == me.steam_id64:
                    self.player.setCurrentIndex(i)
        form.addRow("Player", self.player)
        self.kind_boxes: dict[str, QCheckBox] = {}
        for k in KINDS:
            cb = QCheckBox(KIND_LABELS[k])
            cb.setChecked(k in ("kills", "multikills", "teamfights"))
            self.kind_boxes[k] = cb
            form.addRow("", cb)
        self.lead_in = QDoubleSpinBox()
        self.lead_in.setRange(0, 60)
        self.lead_in.setValue(ctx.settings.video_lead_in_s)
        self.lead_out = QDoubleSpinBox()
        self.lead_out.setRange(0, 60)
        self.lead_out.setValue(ctx.settings.video_lead_out_s)
        self.max_n = QSpinBox()
        self.max_n.setRange(1, 200)
        self.max_n.setValue(40)
        self.camera = QComboBox()
        self.camera.addItems(list(CAMERAS))
        form.addRow("Seconds before", self.lead_in)
        form.addRow("Seconds after", self.lead_out)
        form.addRow("Max clips", self.max_n)
        form.addRow("Camera", self.camera)
        self.replace = QCheckBox("Replace existing sequences")
        self.replace.setChecked(True)
        form.addRow("", self.replace)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def options(self) -> GenerateOptions:
        return GenerateOptions(kinds=tuple(k for k, cb in self.kind_boxes.items() if cb.isChecked()),
                               lead_in_s=self.lead_in.value(), lead_out_s=self.lead_out.value(),
                               max_sequences=self.max_n.value(), camera=self.camera.currentText())


class SequenceDialog(QDialog):
    def __init__(self, seq: Sequence, players: list[MatchPlayerRow], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sequence")
        self.seq = seq
        cat = catalog()
        form = QFormLayout(self)
        self.label = QLineEdit(seq.label)
        self.start = QDoubleSpinBox()
        self.start.setRange(0, 24 * 3600)
        self.start.setValue(seq.start_s)
        self.end = QDoubleSpinBox()
        self.end.setRange(0, 24 * 3600)
        self.end.setValue(seq.end_s)
        self.focus = QComboBox()
        self.focus.addItem("(no player lock)", None)
        for p in sorted(players, key=lambda p: (p.team_num or 0, p.hero_id)):
            self.focus.addItem(f"{p.player_name} — {cat.hero_name(p.hero_id)}", p.hero_id)
            if p.hero_id == seq.focus_hero_id:
                self.focus.setCurrentIndex(self.focus.count() - 1)
        self.camera = QComboBox()
        self.camera.addItems(list(CAMERAS))
        self.camera.setCurrentText(seq.camera)
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.1, 8)
        self.speed.setSingleStep(0.25)
        self.speed.setValue(seq.timescale)
        self.hud = QCheckBox("Show HUD in this clip")
        self.hud.setChecked(seq.hud)
        form.addRow("Label", self.label)
        form.addRow("Start (s)", self.start)
        form.addRow("End (s)", self.end)
        form.addRow("Focus player", self.focus)
        form.addRow("Camera", self.camera)
        form.addRow("Speed", self.speed)
        form.addRow("", self.hud)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._players = {p.hero_id: p for p in players}

    def apply(self) -> Sequence:
        s = self.seq
        s.label = self.label.text().strip()
        s.start_s = self.start.value()
        s.end_s = max(self.end.value(), s.start_s + 1)
        hero = self.focus.currentData()
        s.focus_hero_id = hero
        p = self._players.get(hero) if hero is not None else None
        s.focus_name = p.player_name if p else None
        s.focus_account_id = (p.steam_id - 76561197960265728) if p and p.steam_id else None
        s.camera = self.camera.currentText()
        s.timescale = self.speed.value()
        s.hud = self.hud.isChecked()
        return s


class ClipsTab(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.match: MatchRow | None = None
        self.players: list[MatchPlayerRow] = []
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.btn_generate = QPushButton("Auto-generate…")
        self.btn_add = QPushButton("Add")
        self.btn_edit = QPushButton("Edit")
        self.btn_remove = QPushButton("Remove")
        self.btn_preview = QPushButton("Preview in 2D viewer")
        self.btn_record = QPushButton("Record…")
        self.btn_record.setStyleSheet("font-weight: 600")
        for b in (self.btn_generate, self.btn_add, self.btn_edit, self.btn_remove, self.btn_preview):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(self.btn_record)
        layout.addLayout(bar)
        self.model = SequencesModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.doubleClicked.connect(lambda _i: self._edit())
        layout.addWidget(self.table, 1)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.btn_generate.clicked.connect(self._generate)
        self.btn_add.clicked.connect(self._add)
        self.btn_edit.clicked.connect(self._edit)
        self.btn_remove.clicked.connect(self._remove)
        self.btn_preview.clicked.connect(self._preview)
        self.btn_record.clicked.connect(self._record)

    def load_match(self, match: MatchRow, players: list[MatchPlayerRow]) -> None:
        self.match = match
        self.players = players
        self.reload()

    def reload(self) -> None:
        if not self.match:
            return
        seqs = self.ctx.sequences.for_match(self.match.match_id)
        self.model.set_rows(seqs)
        self.table.resizeColumnsToContents()
        total = sum(s.duration_s for s in seqs)
        build = self.ctx.install.client_build
        demos = [d for d in self.ctx.demos.by_match(self.match.match_id) if d.status in ("found", "parsed")]
        warn = ""
        if demos and build and demos[0].build and demos[0].build < build:
            warn = (f" — WARNING: this demo was recorded on build {demos[0].build}, the installed client is "
                    f"{build}; Deadlock refuses to play older demos, so recording will fail.")
        self.btn_record.setEnabled(bool(seqs) and bool(demos))
        self.info.setText(f"{len(seqs)} sequence(s), {fmt_clock(total)} of footage" + warn)

    def selected(self) -> list[Sequence]:
        return [self.model.row_at(i) for i in self.table.selectionModel().selectedRows() if self.model.row_at(i)]

    def _generate(self) -> None:
        if not self.match:
            return
        dlg = GenerateDialog(self.ctx, self.players, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        seqs = generate(self.ctx.db, self.match, dlg.player.currentData(), dlg.options(), catalog().hero_name)
        if dlg.replace.isChecked():
            self.ctx.sequences.replace_all(self.match.match_id, seqs)
        else:
            for s in seqs:
                self.ctx.sequences.add(s)
        self.ctx.status(f"Generated {len(seqs)} clip(s)")
        self.reload()

    def _add(self) -> None:
        if not self.match:
            return
        seq = Sequence(self.match.match_id, 0.0, 10.0, "New clip")
        dlg = SequenceDialog(seq, self.players, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.ctx.sequences.add(dlg.apply())
            self.reload()

    def add_from_event(self, seconds: float, label: str, hero_id: int | None) -> None:
        if not self.match:
            return
        s = self.ctx.settings
        p = next((p for p in self.players if p.hero_id == hero_id), None)
        seq = Sequence(self.match.match_id, max(0.0, seconds - s.video_lead_in_s), seconds + s.video_lead_out_s, label,
                       hero_id, p.player_name if p else None,
                       (p.steam_id - 76561197960265728) if p and p.steam_id else None)
        self.ctx.sequences.add(seq)
        self.reload()
        self.ctx.status(f"Added clip: {label}")

    def _edit(self) -> None:
        rows = self.selected()
        if not rows:
            return
        dlg = SequenceDialog(rows[0], self.players, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.ctx.sequences.update(dlg.apply())
            self.reload()

    def _remove(self) -> None:
        for s in self.selected():
            if s.id is not None:
                self.ctx.sequences.delete(s.id)
        self.reload()

    def _preview(self) -> None:
        rows = self.selected()
        if rows and self.match:
            self.ctx.events.open_viewer.emit(self.match.match_id)
            self.ctx.events.viewer_seek.emit(rows[0].start_s)

    def _record(self) -> None:
        if not self.match:
            return
        from deaddemo.gui.widgets.record_dialog import RecordDialog

        seqs = self.selected() or self.model.rows
        RecordDialog(self.ctx, self.match, seqs, self).exec()
