# Chibi-Tomo (Qt / PySide6) — Self-Contained, Organized
# Minimal desktop Pomodoro widget:
#   • Time (numbers only) on top
#   • Circular “chibi” avatar with progress ring + bobbing
#   • Right-click anywhere for menu (start/pause/reset, modes, image, settings)
#   • Tray menu for Always-on-top & Click-through
#   • Simple persistence via QSettings
from __future__ import annotations

import base64
import math
import sys
from dataclasses import dataclass
import os
from typing import Optional
import random

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtGui import QAction, QActionGroup, QFont

# =============================================================================
# Styles (inline; previously styles.qss)
# =============================================================================

APP_STYLES = """
QWidget {
  color: #e8e9ef;
  font: 14px 'Segoe UI', 'Inter', system-ui;
}
#TimeLabel {
  color: #ffffff;
  background: transparent;
  padding: 0;
  border: none;
  font-size: 56px;
  font-weight: 600;
}
#SessionLabel {
  color: rgba(255, 255, 255, 0.6);
  font-weight: 500;
  padding: 2px 8px;
  border-radius: 8px;
  background: rgba(0,0,0,0.25);
}
QMenu {
  background-color: rgb(35, 35, 40);
  border: 1px solid rgba(255,255,255,0.15);
  border-radius: 8px;
  padding: 4px;
}
QMenu::item {
  padding: 6px 24px;
  border-radius: 5px;
}
QMenu::item:selected {
  background-color: rgba(255, 255, 255, 0.1);
}
QMenu::separator {
  height: 1px;
  background: rgba(255,255,255,0.1);
  margin: 4px 8px;
}
"""

# Base dimensions for scaling
BASE_WIDTH = 260
BASE_HEIGHT = 380
BASE_TIME_FONT = 56
BASE_DROP = 160

# =============================================================================
# Tiny embedded resources (1x1 transparent PNGs keep deps minimal)
# =============================================================================

_ICON_PNG = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
)
_DEFAULT_AVATAR_PNG = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
)

# On-disk default avatar path (use this unless QSettings points elsewhere)
# On-disk default avatar path (look next to the script so packaged copies don't point to a single user's home)
_ON_DISK_DEFAULT_AVATAR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Adobe Express - file.png")

# =============================================================================
# Data models
# =============================================================================

@dataclass
class Durations:
    focus: int = 25
    brk: int = 5
    long_brk: int = 15
    sessions: int = 4

# =============================================================================
# Helper / UI components
# =============================================================================

class GlassPanel(QtWidgets.QWidget):
    """Transparent overlay to keep a clean, glass-like window without extra paint."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, _: QtGui.QPaintEvent) -> None:  # intentionally no paint
        return


class DropCircle(QtWidgets.QLabel):
    """Circular avatar with progress ring + subtle bobbing animation."""

    def __init__(self) -> None:
        super().__init__(alignment=QtCore.Qt.AlignmentFlag.AlignCenter)

        # Basic look/feel
        self.setAcceptDrops(True)
        self.setFixedSize(160, 160)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("QLabel { border: none; }")
        self.setText("Drop a photo\n(or right-click)")

        # Progress ring state
        self._ring_width = 6
        self._progress: float = 1.0
        self._progress_color = QtGui.QColor("#f28fad")

        # Soft shadow “bobbing”
        self._bob = QtWidgets.QGraphicsDropShadowEffect(
            blurRadius=20, xOffset=0, yOffset=8, color=QtGui.QColor(0, 0, 0, 90)
        )
        self.setGraphicsEffect(self._bob)

        self._bob_anim = QtCore.QPropertyAnimation(self._bob, b"yOffset", self)
        self._bob_anim.setDuration(2500)
        self._bob_anim.setStartValue(8)
        self._bob_anim.setEndValue(12)
        self._bob_anim.setEasingCurve(QtCore.QEasingCurve.Type.InOutSine)
        self._bob_anim.setLoopCount(-1)
        self._bob_anim.start()

        # Progress animation
        self._progress_anim = QtCore.QPropertyAnimation(self, b"progress", self)
        self._progress_anim.setDuration(950)
        self._progress_anim.setEasingCurve(QtCore.QEasingCurve.Type.OutQuad)

    # ----- progress property (for animation) -----
    def _get_progress_prop(self) -> float:
        return self._progress

    def _set_progress_prop(self, value: float) -> None:
        self._progress = float(max(0.0, min(1.0, value)))
        self.update()

    progress = QtCore.Property(float, _get_progress_prop, _set_progress_prop)

    def set_progress(self, value: float, animated: bool = False) -> None:
        target = float(max(0.0, min(1.0, value)))
        if animated:
            if self._progress_anim.state() == QtCore.QPropertyAnimation.State.Running:
                self._progress_anim.stop()
            self._progress_anim.setStartValue(self.progress)
            self._progress_anim.setEndValue(target)
            self._progress_anim.start()
        else:
            self.progress = target

    def set_progress_color(self, color: QtGui.QColor) -> None:
        self._progress_color = color
        self.update()

    # ----- painting -----
    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(
            self._ring_width // 2, self._ring_width // 2,
            -self._ring_width // 2, -self._ring_width // 2
        )

        # background ring
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 20), self._ring_width))
        p.drawArc(rect, 0, 360 * 16)

        # progress ring
        pen_fg = QtGui.QPen(
            self._progress_color, self._ring_width,
            QtCore.Qt.PenStyle.SolidLine, QtCore.Qt.PenCapStyle.RoundCap
        )
        pen_fg.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        p.setPen(pen_fg)
        p.drawArc(rect, 90 * 16, -int(self.progress * 360 * 16))

        # interior hint when empty
        if not self.pixmap():
            p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 45), 2, QtCore.Qt.PenStyle.DashLine))
            p.drawEllipse(self.rect().adjusted(15, 15, -15, -15))

    # ----- drag/drop -----
    def dragEnterEvent(self, e: QtGui.QDragEnterEvent) -> None:
        if e.mimeData().hasUrls() and any(u.isLocalFile() for u in e.mimeData().urls()):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e: QtGui.QDropEvent) -> None:
        for u in e.mimeData().urls():
            if u.isLocalFile():
                self.load_image(u.toLocalFile())
                break

    # ----- image loading -----
    def load_image(self, image_source: str | bytes) -> None:
        img = QtGui.QImage()
        ok = img.load(image_source) if isinstance(image_source, str) else img.loadFromData(image_source)
        if not ok:
            return

        # simple “posterize” pass
        chibi = self._posterize(img)
        size = min(chibi.width(), chibi.height())
        chibi = chibi.copy((chibi.width() - size) // 2, (chibi.height() - size) // 2, size, size)
        chibi = chibi.scaled(
            self.width() - self._ring_width * 2,
            self.height() - self._ring_width * 2,
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )

        # circular mask
        mask = QtGui.QPixmap(chibi.size())
        mask.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(mask)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setBrush(QtCore.Qt.GlobalColor.black)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.drawEllipse(0, 0, mask.width(), mask.height())
        painter.end()

        pm = QtGui.QPixmap.fromImage(chibi)
        pm.setMask(mask.mask())
        self.setPixmap(pm)
        self.setText("")

    def load_default_avatar(self) -> None:
        """Draw a simple circular placeholder avatar programmatically."""
        size = max(8, min(self.width(), self.height()) - self._ring_width * 2)
        pm = QtGui.QPixmap(size, size)
        pm.fill(QtCore.Qt.GlobalColor.transparent)

        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        # simple fill and subtle inner circle
        p.setBrush(QtGui.QBrush(QtGui.QColor(200, 200, 200)))
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.drawEllipse(0, 0, size, size)
        p.setBrush(QtGui.QBrush(QtGui.QColor(240, 240, 240)))
        inset = int(size * 0.18)
        p.drawEllipse(inset, inset, size - inset * 2, size - inset * 2)
        p.end()

        # mask to circle and set
        mask = QtGui.QPixmap(pm.size())
        mask.fill(QtCore.Qt.GlobalColor.transparent)
        mp = QtGui.QPainter(mask)
        mp.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        mp.setBrush(QtGui.Qt.GlobalColor.black)
        mp.setPen(QtCore.Qt.PenStyle.NoPen)
        mp.drawEllipse(0, 0, mask.width(), mask.height())
        mp.end()
        pm.setMask(mask.mask())
        self.setPixmap(pm)
        self.setText("")

    @staticmethod
    def _posterize(img: QtGui.QImage, levels: int = 6) -> QtGui.QImage:
        fmt = QtGui.QImage.Format.Format_ARGB32
        if img.format() != fmt:
            img = img.convertToFormat(fmt)
        out = img.copy()
        step = 255 // max(1, levels - 1)
        for y in range(out.height()):
            for x in range(out.width()):
                c = QtGui.QColor(out.pixel(x, y))
                r = (c.red() // step) * step
                g = (c.green() // step) * step
                b = (c.blue() // step) * step
                out.setPixelColor(x, y, QtGui.QColor(r, g, b, c.alpha()))
        return out

# =============================================================================
# Settings dialog (optional custom durations)
# =============================================================================

class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, current: Durations, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Custom Durations")

        layout = QtWidgets.QFormLayout(self)
        self.spin_focus = QtWidgets.QSpinBox(minimum=1, maximum=120, value=current.focus)
        self.spin_break = QtWidgets.QSpinBox(minimum=1, maximum=60, value=current.brk)
        self.spin_long = QtWidgets.QSpinBox(minimum=1, maximum=60, value=current.long_brk)
        self.spin_sessions = QtWidgets.QSpinBox(minimum=1, maximum=12, value=current.sessions)

        layout.addRow("Focus (minutes):", self.spin_focus)
        layout.addRow("Break (minutes):", self.spin_break)
        layout.addRow("Long Break (minutes):", self.spin_long)
        layout.addRow("Sessions for Long Break:", self.spin_sessions)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_durations(self) -> Durations:
        return Durations(
            self.spin_focus.value(),
            self.spin_break.value(),
            self.spin_long.value(),
            self.spin_sessions.value(),
        )

# =============================================================================
# Main Window
# =============================================================================

class ChibiTomo(QtWidgets.QWidget):
    PHASE_COLORS = {"focus": "#f28fad", "break": "#6de0f2", "long_break": "#88f2b6"}

    def __init__(self) -> None:
        super().__init__()

        # window
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(260, 380)

        self.bg = GlassPanel(self)
        self._drag_pos: Optional[QtCore.QPoint] = None
        # When True the window is locked in place and cannot be moved by dragging
        self._pos_locked: bool = False

        # state
        self.durations = Durations()
        # mid-session notification flags (50% and 25%) to avoid duplicates
        self._notified_50 = False
        self._notified_25 = False
        self.phase = "focus"
        self.session_count = 0
        self.secs = self.durations.focus * 60
        self._timer = QtCore.QTimer(self, interval=1000, timeout=self._tick)

        # UI: time + sessions
        self.lbl_time = QtWidgets.QLabel("25:00", objectName="TimeLabel", alignment=QtCore.Qt.AlignmentFlag.AlignCenter)
        self.lbl_sessions = QtWidgets.QLabel(" ", objectName="SessionLabel", alignment=QtCore.Qt.AlignmentFlag.AlignCenter)
        self.lbl_time.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.lbl_sessions.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        # UI: avatar/progress
        self.drop = DropCircle()
        # transient bubble for in-app popup notifications (appears near avatar)
        self._bubble = QtWidgets.QLabel("", self, alignment=QtCore.Qt.AlignmentFlag.AlignCenter)
        self._bubble.setVisible(False)
        self._bubble.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._bubble.setWordWrap(True)
        # make the bubble a bit larger and place it to the side when shown
        self._bubble.setMaximumWidth(360)
        # speech-bubble-like style: dark rounded box with a small pseudo-tail via border-radius and padding
        self._bubble.setStyleSheet(
            "QLabel { color: #ffffff; background: rgba(0,0,0,0.9); padding: 12px 16px; border-radius: 14px; font-weight: 700; font-size: 14px; }"
        )
        bubble_effect = QtWidgets.QGraphicsOpacityEffect(self._bubble)
        bubble_effect.setOpacity(0.0)
        self._bubble.setGraphicsEffect(bubble_effect)
        self._bubble_anim = QtCore.QPropertyAnimation(bubble_effect, b"opacity", self)
        self._bubble_anim.setDuration(5000)
        self._bubble_anim.setStartValue(1.0)
        self._bubble_anim.setEndValue(0.0)
        self._bubble_anim.finished.connect(lambda: self._bubble.setVisible(False))
        # currently loaded image path (if any) — used to reload when scaling
        self._image_path = None

        # Predefined motivational messages for different milestones
        self._msgs_50 = [
            "Halfway there! Keep the momentum going — you’ve got this!",
            "50% done! Every step counts — finish strong.",
            "You’re crushing it! Stay focused, the finish line is in sight.",
            "Halfway is the new beginning — push forward with purpose.",
            "You’ve already proven you can start — now show you can finish.",
            "Half the time’s gone, but the best effort comes now!",
            "Momentum is your friend — don’t stop now!",
            "Midway check: your future self is proud of you already.",
            "Stay locked in. The goal is closer than it seems.",
            "You’re shining already — keep that energy alive!",
        ]

        self._msgs_25 = [
            "Only 25% left — now’s the time to give your best!",
            "You’ve come so far — don’t let up now!",
            "This is where champions are made — finish with power!",
            "One last push! You’re almost there!",
            "You started strong, now finish stronger.",
            "Final stretch — make it count!",
            "The top is near — keep climbing!",
            "Your focus now is your greatest strength.",
            "You’re 75% done — don’t stop until it’s 100%.",
            "Greatness lives in the final effort. Give it your all!",
        ]

        self._msgs_done = [
            "You made it! Every second counts — and you just proved it!",
            "Victory! Timer complete and you nailed it!",
            "Another win in the books — your consistency is gold!",
            "Boom! That’s how champions finish!",
            "Done and dusted — success unlocked!",
            "Timer’s done — and you shined all the way through!",
            "Bullseye! You stayed the course and hit your mark!",
            "Session complete — your focus paid off big time!",
            "Mission accomplished — onward to greatness!",
            "You gave your best — and it shows. Well done!",
        ]

        # build menus/tray/layout and init
        self._build_menu()
        self._make_tray()
        self._create_layout()
        self._load_settings()
        self.reset()

    # ----- layout -----
    def _create_layout(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 12)
        layout.setSpacing(0)
        layout.addWidget(self.lbl_time, 0, QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.drop, 0, QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_sessions, 0, QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()

    # ----- menus -----
    def _build_menu(self) -> None:
        self.menu = QtWidgets.QMenu(self)
        self.menu.addAction("Start", self.start)
        self.menu.addAction("Pause", self.pause)
        self.menu.addAction("Reset", self.reset)
        self.menu.addSeparator()

        group = QActionGroup(self)
        group.setExclusive(True)
        modes = [("Pomodoro (25/5)", (25, 5, 15, 4)), ("50/10", (50, 10, 20, 4))]
        for i, (text, params) in enumerate(modes):
            action = QAction(text, self, checkable=True, checked=(i == 0))
            # QAction.triggered(bool) sends the checked state as the first arg – capture it safely
            action.triggered.connect(lambda checked, p=params: self._apply_mode(*p))
            self.menu.addAction(action)
            group.addAction(action)

        custom_action = QAction("Custom...", self, checkable=True)
        custom_action.triggered.connect(self._show_custom_settings)
        self.menu.addAction(custom_action)
        group.addAction(custom_action)
        # visual break between custom and appearance presets
        self.menu.addSeparator()

        # Opacity submenu (presets)
        opacity_menu = QtWidgets.QMenu("Opacity", self.menu)
        opacity_group = QActionGroup(self)
        opacity_group.setExclusive(True)
        # store actions so we can mark the saved preset as checked on load
        self._opacity_actions = {}
        for val, label in ((0.05, "5%"), (0.25, "25%"), (0.5, "50%"), (0.75, "75%"), (1.0, "100%")):
            a = QAction(label, self, checkable=True)
            a.triggered.connect(lambda checked, v=val: self._apply_opacity(v))
            opacity_menu.addAction(a); opacity_group.addAction(a)
            self._opacity_actions[float(val)] = a
        self.menu.addMenu(opacity_menu)

        # Scale submenu (presets)
        scale_menu = QtWidgets.QMenu("Scale", self.menu)
        scale_group = QActionGroup(self)
        scale_group.setExclusive(True)
        self._scale_actions = {}
        for val, label in ((0.05, "5%"), (0.25, "25%"), (0.5, "50%"), (0.75, "75%"), (1.0, "100%"), (1.25, "125%"), (1.5, "150%"), (1.75, "175%"), (2.0, "200%")):
            a = QAction(label, self, checkable=True)
            a.triggered.connect(lambda checked, s=val: self._apply_scale(s))
            scale_menu.addAction(a); scale_group.addAction(a)
            self._scale_actions[float(val)] = a
        self.menu.addMenu(scale_menu)

        self.menu.addSeparator()
        self.menu.addAction("Select picture…", self._select_picture)

        # Tray-related actions (use same menu for tray context)
        self.menu.addSeparator()
        # Notifications submenu (user-requested name)
        notify_menu = QtWidgets.QMenu("Notification", self.menu)
        self.act_notify_popup = QAction("Popup", self, checkable=True, checked=True)
        self.act_notify_sound = QAction("Sound", self, checkable=True, checked=True)
        # persist choices when toggled
        self.act_notify_popup.toggled.connect(lambda v: QtCore.QSettings("ChibiTomo", "App").setValue("notify_popup", bool(v)))
        self.act_notify_sound.toggled.connect(lambda v: QtCore.QSettings("ChibiTomo", "App").setValue("notify_sound", bool(v)))
        notify_menu.addAction(self.act_notify_popup)
        notify_menu.addAction(self.act_notify_sound)
        self.menu.addMenu(notify_menu)
        # separate notifications from lock/quit actions
        self.menu.addSeparator()
        # Single "Lock position" action: when checked the window cannot be moved
        self.act_lock = QAction("Lock position (prevent moving)", self)
        self.act_lock.setCheckable(True)
        self.act_lock.toggled.connect(self._apply_lock)
        self.menu.addAction(self.act_lock)

        self.menu.addSeparator()
        self.menu.addAction("Quit", self.close)

    # ----- tray -----
    def _make_tray(self) -> None:
        self.tray = QtWidgets.QSystemTrayIcon(self)
        # Create a small programmatic icon for the tray to avoid loading embedded PNG bytes
        pm = QtGui.QPixmap(16, 16)
        pm.fill(QtCore.Qt.GlobalColor.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setBrush(QtGui.QBrush(QtGui.QColor(242, 143, 173)))
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.drawEllipse(0, 0, 16, 16)
        p.end()
        self.tray.setIcon(QtGui.QIcon(pm))

    # Attach the already-built menu to the tray so both use same actions
        self.tray.setContextMenu(self.menu)
        self.tray.setToolTip("Chibi-Tomo")
        self.tray.show()

    # ----- appearance helpers -----
    def _apply_opacity(self, opacity: float) -> None:
        # clamp
        opacity = max(0.2, min(1.0, float(opacity)))
        self.setWindowOpacity(opacity)
        # persist
        QtCore.QSettings("ChibiTomo", "App").setValue("opacity", float(opacity))

    def _apply_scale(self, scale: float) -> None:
        # Apply scale to window size, timer font, and drop size
        scale = float(scale)
        w = int(BASE_WIDTH * scale)
        h = int(BASE_HEIGHT * scale)
        self.resize(w, h)
        # adjust timer font by overriding the label style
        fs = int(BASE_TIME_FONT * scale)
        self.lbl_time.setStyleSheet(f"#TimeLabel {{ font-size: {fs}px; font-weight: 600; color: #ffffff; background: transparent; border: none; }}")
        # adjust drop size
        drop_size = int(BASE_DROP * scale)
        self.drop.setFixedSize(drop_size, drop_size)
        # if we have a path to the image, reload to scale properly; otherwise rescale current pixmap
        if getattr(self, "_image_path", None):
            try:
                self.drop.load_image(self._image_path)
            except Exception:
                pass
        else:
            pm = self.drop.pixmap()
            if pm:
                try:
                    target = max(8, drop_size - self.drop._ring_width * 2)
                    scaled = pm.scaled(target, target, QtCore.Qt.AspectRatioMode.KeepAspectRatio, QtCore.Qt.TransformationMode.SmoothTransformation)
                    self.drop.setPixmap(scaled)
                except Exception:
                    pass
        QtCore.QSettings("ChibiTomo", "App").setValue("ui_scale", float(scale))

    # Provide a simple default avatar if no on-disk image is available
    def _load_default_avatar(self) -> None:
        try:
            self.drop.load_default_avatar()
        except Exception:
            # fallback: keep whatever is currently shown
            pass

    # =============================================================================
    # Timer & display
    # =============================================================================

    def _tick(self) -> None:
        if self.secs <= 0:
            self._timer.stop()
            self.tray.showMessage(
                f"{self.phase.replace('_', ' ').title()} Over!",
                "Time to switch tasks.",
                self.tray.icon(),
            )
            QtWidgets.QApplication.beep()

            next_phase = "focus"
            if self.phase == "focus":
                self.session_count += 1
                next_phase = "long_break" if self.session_count % self.durations.sessions == 0 else "break"

            self.set_phase(next_phase, animate_progress=True)
            self.start()
        else:
            self.secs -= 1
            self._update_display()

    def _update_display(self) -> None:
        m, s = divmod(max(0, self.secs), 60)
        self.lbl_time.setText(f"{m:02d}:{s:02d}")

        total = (
            self.durations.focus if self.phase == "focus"
            else self.durations.long_brk if self.phase == "long_break"
            else self.durations.brk
        ) * 60
        progress = self.secs / total if total > 0 else 0.0
        self.drop.set_progress(progress, animated=self._timer.isActive())

        # Mid-session notifications at 50% and 25% remaining
        # Only notify while the timer is active to avoid spurious popups
        if total > 0 and self._timer.isActive():
            try:
                # progress is fraction remaining (0.0..1.0)
                if progress <= 0.5 and not self._notified_50:
                    self._notify("50% remaining", f"{m}m {s}s left")
                    self._notified_50 = True
                if progress <= 0.25 and not self._notified_25:
                    self._notify("25% remaining", f"{m}m {s}s left")
                    self._notified_25 = True
            except Exception:
                # keep the timer robust even if notifications fail
                pass

        if self.phase == "focus":
            self.lbl_sessions.setText(f"Session {self.session_count % self.durations.sessions + 1}/{self.durations.sessions}")
            self.lbl_sessions.show()
        else:
            self.lbl_sessions.hide()

    def start(self) -> None:
        self._timer.start()

    def pause(self) -> None:
        self._timer.stop()

    def reset(self) -> None:
        self.pause()
        self.session_count = 0
        # reset notification flags and go to focus
        self._notified_50 = False
        self._notified_25 = False
        self.set_phase("focus", animate_progress=True)

    def set_phase(self, p: str, animate_progress: bool = False) -> None:
        self.phase = p
        # reset mid-session notification flags at the start of each phase
        self._notified_50 = False
        self._notified_25 = False
        if p == "focus":
            self.secs = self.durations.focus * 60
        elif p == "break":
            self.secs = self.durations.brk * 60
        else:
            self.secs = self.durations.long_brk * 60

        self.drop.set_progress_color(QtGui.QColor(self.PHASE_COLORS.get(p, "#ffffff")))

        total = (
            self.durations.focus if self.phase == "focus"
            else self.durations.long_brk if self.phase == "long_break"
            else self.durations.brk
        ) * 60
        progress = self.secs / total if total > 0 else 0.0
        self.drop.set_progress(progress, animated=animate_progress)
        self._update_display()

    def _apply_mode(self, focus: int, brk: int, long_brk: int, sessions: int) -> None:
        self.durations = Durations(focus, brk, long_brk, sessions)
        self.reset()

    def _show_custom_settings(self) -> None:
        dialog = SettingsDialog(self.durations, self)
        if dialog.exec():
            self.durations = dialog.get_durations()
            self.reset()
    # =============================================================================
    # Persistence & window behavior
    # =============================================================================

    def _load_settings(self) -> None:
        s = QtCore.QSettings("ChibiTomo", "App")
        geom = s.value("geometry")
        if geom:
            self.restoreGeometry(geom)

        img_path = s.value("imagePath")
        if img_path:
            self.drop.load_image(img_path)
        elif os.path.isfile(_ON_DISK_DEFAULT_AVATAR):
            # Use the on-disk default provided by the user if available
            self.drop.load_image(_ON_DISK_DEFAULT_AVATAR)
        else:
            # Use a programmatically generated default avatar to avoid loading embedded PNG data
            self.drop.load_default_avatar()

        # Restore position-locked state if present
        pos_locked = s.value("pos_locked", False, type=bool)
        self.act_lock.setChecked(pos_locked)
        self._pos_locked = bool(pos_locked)
        # Restore notification preferences
        notify_popup = s.value("notify_popup", True, type=bool)
        notify_sound = s.value("notify_sound", True, type=bool)
        try:
            if hasattr(self, "act_notify_popup"):
                self.act_notify_popup.setChecked(bool(notify_popup))
            if hasattr(self, "act_notify_sound"):
                self.act_notify_sound.setChecked(bool(notify_sound))
        except Exception:
            pass
        # restore appearance settings
        opacity = s.value("opacity", None, type=float)
        if opacity is None:
            opacity = 1.0
        try:
            # if we have an action for this preset, check it so menu reflects the state
            a = self._opacity_actions.get(float(opacity)) if hasattr(self, "_opacity_actions") else None
            if a:
                a.setChecked(True)
            self._apply_opacity(float(opacity))
        except Exception:
            pass
        ui_scale = s.value("ui_scale", None, type=float)
        if ui_scale is None:
            ui_scale = 1.0
        try:
            a = self._scale_actions.get(float(ui_scale)) if hasattr(self, "_scale_actions") else None
            if a:
                a.setChecked(True)
            self._apply_scale(float(ui_scale))
        except Exception:
            pass

        self.durations = Durations(
            s.value("d_focus", 25, type=int),
            s.value("d_break", 5, type=int),
            s.value("d_long_break", 15, type=int),
            s.value("d_sessions", 4, type=int),
        )

    def _save_settings(self) -> None:
        s = QtCore.QSettings("ChibiTomo", "App")
        s.setValue("geometry", self.saveGeometry())
        s.setValue("pos_locked", self.act_lock.isChecked())
        s.setValue("d_focus", self.durations.focus)
        s.setValue("d_break", self.durations.brk)
        s.setValue("d_long_break", self.durations.long_brk)
        s.setValue("d_sessions", self.durations.sessions)
        s.setValue("opacity", float(self.windowOpacity()))
        # ui_scale already persisted by _apply_scale; ensure presence
        if not s.contains("ui_scale"):
            s.setValue("ui_scale", 1.0)

    # window drag (disabled when position-locked)
    def mousePressEvent(self, e: QtGui.QMouseEvent) -> None:
        if e.button() == QtCore.Qt.MouseButton.LeftButton and not self._pos_locked:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e: QtGui.QMouseEvent) -> None:
        if self._pos_locked:
            return
        if e.buttons() == QtCore.Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, e: QtGui.QMouseEvent) -> None:
        self._drag_pos = None
        e.accept()

    # context menu anywhere
    def contextMenuEvent(self, e: QtGui.QContextMenuEvent) -> None:
        self.menu.exec(e.globalPos())

    def resizeEvent(self, e: QtGui.QResizeEvent) -> None:
        self.bg.resize(self.size())
        super().resizeEvent(e)

    def closeEvent(self, e: QtGui.QCloseEvent) -> None:
        self._save_settings()
        self.tray.hide()
        QtWidgets.QApplication.quit()
        e.accept()

    # actions
    def _select_picture(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Choose image", "", "Images (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if path:
            self.drop.load_image(path)
            self._image_path = path
            QtCore.QSettings("ChibiTomo", "App").setValue("imagePath", path)

    def _apply_lock(self, checked: bool) -> None:
        # When checked, prevent the window from being moved by drag
        self._pos_locked = bool(checked)

    def _notify(self, title: str, message: str) -> None:
        """Show a tray popup and play a short beep to alert the user."""
        try:
            settings = QtCore.QSettings("ChibiTomo", "App")
            popup = settings.value("notify_popup", True, type=bool)
            sound = settings.value("notify_sound", True, type=bool)
            # pick a message variant for 50%/25%/done events
            chosen_text = f"{title}: {message}"
            try:
                low = title.lower()
                if "50%" in title or "50%" in low or "halfway" in low:
                    chosen_text = random.choice(self._msgs_50)
                elif "25%" in title or "25%" in low or "final" in low or "final stretch" in low:
                    chosen_text = random.choice(self._msgs_25)
                elif "over" in low or "done" in low or "complete" in low or "time" in low:
                    chosen_text = random.choice(self._msgs_done)
            except Exception:
                # fallback to the original title/message
                chosen_text = f"{title}: {message}"

            # show in-app bubble popup if enabled
            if popup:
                self._show_bubble(chosen_text, duration_ms=5000)
            else:
                # fallback to system tray message only if popup disabled but tray exists
                if hasattr(self, 'tray') and self.tray is not None:
                    self.tray.showMessage(title, message, self.tray.icon())
            if sound and popup:
                # short beep along with the popup
                QtWidgets.QApplication.beep()
            elif sound and not popup:
                # still play sound if popup disabled but sound enabled
                QtWidgets.QApplication.beep()
        except Exception:
            # best-effort only
            pass

    def _show_bubble(self, text: str, duration_ms: int = 8000) -> None:
        """Show a transient bubble near the avatar with fade-out animation.
        The bubble prefers to appear to the right of the avatar and is larger than before.
        """
        try:
            self._bubble.setText(text)
            self._bubble.adjustSize()
            hint_w = self._bubble.sizeHint().width()
            hint_h = self._bubble.sizeHint().height()

            # prefer right side of the avatar and move slightly further away
            drop_geo = self.drop.geometry()
            # push further out from the avatar for clearer separation
            right_x = drop_geo.right() + 500
            left_x = drop_geo.left() - hint_w - 0
            # center vertically on avatar
            y = drop_geo.center().y() - hint_h // 2

            # Center bubble horizontally and anchor it to the bottom of the window
            bottom_margin = 16
            bx = max(6, (self.width() - hint_w) // 2)
            by = max(6, self.height() - hint_h - bottom_margin)

            self._bubble.move(int(bx), int(by))
            self._bubble.setVisible(True)
            # reset and play animation
            self._bubble.graphicsEffect().setOpacity(1.0)
            self._bubble_anim.stop()
            self._bubble_anim.setDuration(duration_ms)
            self._bubble_anim.setStartValue(1.0)
            self._bubble_anim.setEndValue(0.0)
            self._bubble_anim.start()
        except Exception:
            pass

# =============================================================================
# App entry
# =============================================================================

def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    w = ChibiTomo()
    w.setStyleSheet(APP_STYLES)
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
