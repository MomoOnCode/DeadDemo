"""A small QPainter line chart: thin 2px lines, recessive grid, hover crosshair with tooltip,
direct labels at line ends (collision-avoided), one y-axis, zero baseline for signed data."""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from deaddemo.gui.theme import fmt_clock


@dataclass
class Series:
    name: str
    color: QColor
    xs: list[float]
    ys: list[float]
    width: float = 2.0
    dashed: bool = False
    visible: bool = True


@dataclass
class ChartStyle:
    margin_left: int = 60
    margin_right: int = 130
    margin_top: int = 16
    margin_bottom: int = 28
    grid: QColor = field(default_factory=lambda: QColor(255, 255, 255, 28))
    axis_text: QColor = field(default_factory=lambda: QColor("#b8b8b8"))
    surface: QColor = field(default_factory=lambda: QColor("#1e1e1e"))


class LineChart(QWidget):
    def __init__(self, parent=None, *, y_format=None, x_format=None):
        super().__init__(parent)
        self.series: list[Series] = []
        self.markers: list[tuple[float, QColor, str]] = []  # vertical markers (x, color, label)
        self.style = ChartStyle()
        self.y_format = y_format or (lambda v: f"{v / 1000:.1f}k" if abs(v) >= 1000 else f"{v:.0f}")
        self.x_format = x_format or fmt_clock
        self.setMouseTracking(True)
        self._hover_x: float | None = None
        self.setMinimumHeight(220)
        self.empty_text = "No data"

    def set_series(self, series: list[Series], markers: list[tuple[float, QColor, str]] | None = None) -> None:
        self.series = series
        self.markers = markers or []
        self.update()

    # -- geometry ----------------------------------------------------------------------
    def _bounds(self):
        xs = [x for s in self.series if s.visible for x in s.xs]
        ys = [y for s in self.series if s.visible for y in s.ys]
        if not xs or not ys:
            return 0.0, 1.0, 0.0, 1.0
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(0.0, min(ys)), max(0.0, max(ys))
        if x1 == x0:
            x1 = x0 + 1
        if y1 == y0:
            y1 = y0 + 1
        pad = (y1 - y0) * 0.05
        return x0, x1, y0 - (pad if y0 < 0 else 0), y1 + pad

    def _plot_rect(self) -> QRectF:
        st = self.style
        return QRectF(st.margin_left, st.margin_top, max(10, self.width() - st.margin_left - st.margin_right),
                      max(10, self.height() - st.margin_top - st.margin_bottom))

    def _to_px(self, x: float, y: float, b, r: QRectF) -> QPointF:
        x0, x1, y0, y1 = b
        return QPointF(r.left() + (x - x0) / (x1 - x0) * r.width(), r.bottom() - (y - y0) / (y1 - y0) * r.height())

    # -- painting ----------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), self.style.surface)
        r = self._plot_rect()
        b = self._bounds()
        x0, x1, y0, y1 = b
        font = QFont(self.font())
        font.setPointSize(8)
        p.setFont(font)
        fm = QFontMetrics(font)
        visible = [s for s in self.series if s.visible and len(s.xs) >= 2]
        if not visible:
            p.setPen(self.style.axis_text)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.empty_text)
            p.end()
            return

        # grid + y labels
        n_y = 5
        for i in range(n_y + 1):
            yv = y0 + (y1 - y0) * i / n_y
            py = self._to_px(x0, yv, b, r).y()
            p.setPen(QPen(self.style.grid, 1))
            p.drawLine(QPointF(r.left(), py), QPointF(r.right(), py))
            p.setPen(self.style.axis_text)
            p.drawText(QRectF(0, py - 8, r.left() - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       self.y_format(yv))
        if y0 < 0 < y1:  # zero baseline for signed data
            zy = self._to_px(x0, 0.0, b, r).y()
            p.setPen(QPen(QColor(255, 255, 255, 110), 1))
            p.drawLine(QPointF(r.left(), zy), QPointF(r.right(), zy))
        # x labels
        n_x = max(2, int(r.width() // 90))
        for i in range(n_x + 1):
            xv = x0 + (x1 - x0) * i / n_x
            px = self._to_px(xv, y0, b, r).x()
            p.setPen(self.style.axis_text)
            p.drawText(QRectF(px - 40, r.bottom() + 4, 80, 16), Qt.AlignmentFlag.AlignHCenter, self.x_format(xv))

        # markers
        for mx, color, label in self.markers:
            if x0 <= mx <= x1:
                px = self._to_px(mx, y0, b, r).x()
                p.setPen(QPen(QColor(color), 1, Qt.PenStyle.DashLine))
                p.drawLine(QPointF(px, r.top()), QPointF(px, r.bottom()))
                if label:
                    p.drawText(QPointF(px + 3, r.top() + 10), label)

        # series
        ends: list[tuple[float, Series, QPointF]] = []
        p.setClipRect(r.adjusted(-2, -2, 2, 2))
        for s in visible:
            path = QPainterPath()
            pts = [self._to_px(x, y, b, r) for x, y in zip(s.xs, s.ys, strict=False)]
            path.moveTo(pts[0])
            for pt in pts[1:]:
                path.lineTo(pt)
            pen = QPen(s.color, s.width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            if s.dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
            ends.append((pts[-1].y(), s, pts[-1]))
        p.setClipping(False)

        # direct labels at line ends, pushed apart so they never overlap
        ends.sort(key=lambda e: e[0])
        line_h = fm.height()
        placed: list[float] = []
        for y, _s, _end in ends:
            ly = y
            if placed and ly < placed[-1] + line_h:
                ly = placed[-1] + line_h
            placed.append(ly)
        # if the stack overflows the bottom, shift everything up
        overflow = placed[-1] - r.bottom() if placed else 0
        if overflow > 0:
            placed = [v - overflow for v in placed]
        for (y, s, end), ly in zip(ends, placed, strict=False):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(s.color)
            p.drawEllipse(end, 3, 3)
            if abs(ly - y) > 2:
                p.setPen(QPen(QColor(s.color), 1))
                p.drawLine(end, QPointF(r.right() + 4, ly))
            p.setPen(QColor("#e6e6e6"))
            p.drawText(QPointF(r.right() + 6, ly + 4), fm.elidedText(s.name, Qt.TextElideMode.ElideRight,
                                                                    self.style.margin_right - 10))

        # hover crosshair
        if self._hover_x is not None and r.left() <= self._hover_x <= r.right():
            xv = x0 + (self._hover_x - r.left()) / r.width() * (x1 - x0)
            p.setPen(QPen(QColor(255, 255, 255, 90), 1))
            p.drawLine(QPointF(self._hover_x, r.top()), QPointF(self._hover_x, r.bottom()))
            rows: list[tuple[str, QColor]] = [(self.x_format(xv), QColor("#f0f0f0"))]
            for s in visible:
                idx = min(range(len(s.xs)), key=lambda i: abs(s.xs[i] - xv))
                pt = self._to_px(s.xs[idx], s.ys[idx], b, r)
                p.setBrush(s.color)
                p.setPen(QPen(self.style.surface, 2))
                p.drawEllipse(pt, 4, 4)
                rows.append((f"{s.name}: {self.y_format(s.ys[idx])}", s.color))
            w = max(fm.horizontalAdvance(t) for t, _ in rows) + 24
            h = fm.height() * len(rows) + 8
            bx = self._hover_x + 12 if self._hover_x + 12 + w < r.right() else self._hover_x - 12 - w
            box = QRectF(bx, r.top() + 4, w, h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(20, 20, 20, 235))
            p.drawRoundedRect(box, 4, 4)
            for i, (t, c) in enumerate(rows):
                ty = box.top() + 4 + fm.ascent() + i * fm.height()
                if i > 0:
                    p.setBrush(c)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.drawEllipse(QPointF(box.left() + 9, ty - fm.ascent() / 2 + 1), 3.5, 3.5)
                p.setPen(QColor("#f0f0f0"))
                p.drawText(QPointF(box.left() + 18, ty), t)
        p.end()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self._hover_x = event.position().x()
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover_x = None
        self.update()
