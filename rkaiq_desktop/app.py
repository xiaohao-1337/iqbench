"""Qt application and one-in-flight tuning workflow."""

from pathlib import Path
import sys

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QMainWindow, QMessageBox, QScrollArea, QStackedWidget,
    QVBoxLayout, QWidget,
)

from .connection import ConnectionPage, STEPS
from .session import SshSession, validate_login
from .theme import STYLE
from .widgets import box, label
from .workbench import Workbench


class SessionEvents(QObject):
    received = Signal(str, object)
    finished = Signal(str, bool)


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RKAIQ 在线调参")
        self.resize(1030, 725)
        self.setMinimumSize(760, 490)
        self.events = SessionEvents(self)
        self.events.received.connect(self.receive)
        self.events.finished.connect(self.session_finished)
        self.session = None
        self.closing = False
        self.exit_requested = False
        self.operation = None
        self.state = {}
        self.initial = {}
        self.pending = {}
        self.sent = {}
        self.refresh_queued = False
        self.scan_queued = False
        self.export_target = None
        self.selection = None
        self.flush = QTimer(self)
        self.flush.setSingleShot(True)
        self.flush.setInterval(85)
        self.flush.timeout.connect(self.dispatch)

        surface = QWidget()
        surface.setObjectName("surface")
        shell = QVBoxLayout(surface)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        top, header = box(surface, "topbar", (18, 8, 18, 8))
        title = QHBoxLayout()
        title.addWidget(label("RKAIQ", "badge"))
        title.addWidget(label("在线调参工作台", "section"))
        title.addStretch()
        title.addWidget(label("ISP30  /  LIVE TUNING", "muted"))
        header.addLayout(title)
        shell.addWidget(top)
        self.stack = QStackedWidget()
        self.connection = ConnectionPage()
        self.login_scroll = QScrollArea()
        self.login_scroll.setWidgetResizable(True)
        self.login_scroll.setWidget(self.connection)
        self.workbench = Workbench()
        self.stack.addWidget(self.login_scroll)
        self.stack.addWidget(self.workbench)
        shell.addWidget(self.stack, 1)
        self.setCentralWidget(surface)

        self.connection.login_requested.connect(self.login)
        self.connection.selection_requested.connect(self.select)
        self.connection.scan_requested.connect(self.queue_scan)
        self.connection.disconnect_requested.connect(self.disconnect)
        self.workbench.changed.connect(self.change)
        self.workbench.save_requested.connect(self.save)
        self.workbench.refresh_requested.connect(self.refresh)
        self.workbench.restore_requested.connect(self.restore)
        self.workbench.gamma_requested.connect(self.restore_gamma)
        self.workbench.scan_requested.connect(self.queue_scan)
        self.workbench.disconnect_requested.connect(self.disconnect)
        self.workbench.quit_requested.connect(self.close)

    def login(self, host, user, password):
        if self.session:
            return
        try:
            validate_login(host, user, password)
        except ValueError as error:
            self.connection.reset(str(error), error=True)
            return
        self.host = host
        self.connection.connecting()
        self.session = SshSession(self.events.received.emit, self.events.finished.emit)
        self.session.start(host, user, password)

    def submit(self, operation, message, target=None):
        self.operation = operation
        self.session.submit(operation, message, target)

    def queue_scan(self):
        self.scan_queued = True
        self.kick()

    def scan(self):
        self.reset_tuning()
        self.stack.setCurrentWidget(self.login_scroll)
        self.connection.scanning()
        self.submit("scan", {"list": True})

    def select(self, message):
        if self.session and not self.closing and self.operation is None:
            self.connection.selecting(lookup=message["iq_file"] is None)
            self.submit("select", message)

    def reset_tuning(self):
        self.flush.stop()
        self.state.clear()
        self.initial.clear()
        self.sent.clear()
        self.pending.clear()
        self.refresh_queued = False
        self.scan_queued = False
        self.export_target = None
        self.selection = None

    def receive(self, operation, reply):
        if self.closing:
            return
        if operation == "phase":
            if reply == "upload":
                self.connection.set_stage(1, 1, "SSH 已连接，正在上传临时脚本")
            elif reply == "start":
                self.scan()
            return
        if operation != self.operation:
            return
        self.operation = None
        if reply.get("reselect"):
            self.reset_tuning()
            self.stack.setCurrentWidget(self.login_scroll)
            self.connection.show_nodes([], reply["error"])
            return
        if operation == "scan":
            self.connection.show_nodes(reply.get("nodes", []), reply.get("error", ""))
        elif operation == "select":
            self.open_selection(reply)
        elif "error" in reply:
            self.sent.clear()
            self.export_target = None
            # A rejected write can have changed the device before its readback failed.
            if operation == "set":
                self.submit("resync", {"set": {}})
            self.workbench.set_status(reply["error"], "error")
            self.render()
        elif operation == "export":
            self.export_target = None
            self.render()
            self.workbench.set_status(f'已保存到本机 · {reply["saved"]}', "ok")
        else:
            self.state.update(reply["state"])
            self.sent.clear()
            self.render()
            if operation != "resync" and self.export_target is None:
                if self.pending:
                    self.workbench.set_status("正在应用调参改动…", "busy")
                else:
                    self.workbench.set_status("设备已回读 · 参数已同步", "ok")
        self.kick()

    def open_selection(self, reply):
        if "error" in reply:
            self.connection.finish_selection(reply["error"])
            return
        if "iq_choices" in reply:
            self.connection.choose_iq(reply["iq_choices"], reply["preferred_iq"], reply["loaded_iq"])
            return
        self.selection = reply["iq_file"]
        self.state = reply["state"].copy()
        self.initial = self.state.copy()
        self.workbench.open(self.host, reply)
        self.connection.set_stage(4, None, "设备与 IQ 已加载", "ok")
        self.workbench.set_status(
            reply.get("offline", "设备已连接 · 参数已回读"), "error" if "offline" in reply else "ok",
        )
        self.render()
        self.stack.setCurrentWidget(self.workbench)
        self.workbench.navigation.button(self.workbench.pages.currentIndex()).setFocus()

    def change(self, key, value):
        if self.session and not self.closing and self.export_target is None and key in self.state:
            if not self.pending:
                self.workbench.set_status("正在应用调参改动…", "busy")
            self.pending[key] = value
            self.kick()

    def render(self):
        self.workbench.render(self.state, {**self.sent, **self.pending},
                              bool(self.state) and not self.closing and self.export_target is None)

    def refresh(self):
        self.refresh_queued = True
        self.kick()

    def restore(self):
        self.pending = {key: value for key, value in self.initial.items()
                        if value != self.sent.get(key, self.state.get(key))}
        self.render()
        self.refresh()

    def restore_gamma(self):
        self.pending.update({key: value for key, value in self.workbench.defaults.items()
                             if key.startswith("gamma_") and key in self.state})
        self.render()
        self.kick()

    def save(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存完整 IQ 到本机", self.selection or "", "RKAIQ IQ JSON (*.json)",
        )
        if path and self.state and self.session and not self.closing:
            target = Path(path)
            self.export_target = target if target.suffix else target.with_suffix(".json")
            self.workbench.set_status("正在确认改动并生成本机 IQ 文件…", "busy")
            self.render()
            self.kick()

    def kick(self):
        if (self.session and self.operation is None and not self.closing and not self.flush.isActive()
                and (self.pending or self.refresh_queued or self.export_target or self.scan_queued)):
            self.flush.start()

    def dispatch(self):
        # Coalesce rapid edits by key. Keep one request in flight so replies stay ordered.
        if self.closing or self.session is None or self.operation is not None:
            return
        if self.pending:
            self.sent, self.pending = self.pending, {}
            self.submit("set", {"set": self.sent})
        elif self.refresh_queued:
            self.refresh_queued = False
            self.submit("refresh", {"set": {}})
        elif self.export_target:
            self.submit("export", {"export": True}, self.export_target)
        elif self.scan_queued:
            self.scan()

    def disconnect(self):
        if self.session and not self.closing:
            self.closing = True
            self.flush.stop()
            self.render()
            self.workbench.set_status("正在关闭 SSH 并等待板端清理…", "busy")
            self.connection.closing()
            self.session.close()

    def session_finished(self, error, cleaned):
        stage = self.connection.active
        if error and stage is not None:
            error = f"{STEPS[stage]}失败 · {error}"
        if not cleaned:
            error = f"{error}\n未收到板端临时文件清理确认".strip()
        self.session = None
        self.operation = None
        self.closing = False
        self.reset_tuning()
        if self.exit_requested:
            if not cleaned:
                QMessageBox.warning(self, "板端清理", error)
            self.close()
        else:
            self.connection.reset(error or "会话已断开", bool(error))
            self.stack.setCurrentWidget(self.login_scroll)

    def closeEvent(self, event):
        if self.session is None:
            event.accept()
        else:
            event.ignore()
            self.exit_requested = True
            self.disconnect()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("RKAIQ Workbench")
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(STYLE)
    icon = Path(__file__).resolve().parent / "assets" / "rkaiq.ico"
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))
    window = Window()
    window.show()
    return app.exec()
