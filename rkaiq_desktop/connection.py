"""Connection form, live milestones and the mainpath / IQ selectors."""

from time import monotonic

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLineEdit, QProgressBar, QSizePolicy, QVBoxLayout, QWidget,
)

from .session import DEVICE_DEFAULTS
from .theme import update_style
from .widgets import Picker, box, button, label


STEPS = ("SSH 登录", "部署调参脚本", "扫描 ISP 主链路", "加载 IQ 并回读")


class ConnectionPage(QWidget):
    login_requested = Signal(str, str, str)
    selection_requested = Signal(dict)
    scan_requested = Signal()
    disconnect_requested = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("surface")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 24)
        layout.setSpacing(16)
        layout.addStretch(1)
        hero, hero_body = box(self, "hero", (20, 15, 20, 15), 4)
        hero_body.addWidget(label("连接设备", "inverse"))
        hero_body.addWidget(label("连接板卡后，选择摄像头主链路进入在线调参。", "inverseNote", wrap=True))
        layout.addWidget(hero)

        columns = QHBoxLayout()
        columns.setSpacing(16)
        layout.addLayout(columns)
        layout.addStretch(2)

        form, form_body = box(self, margins=(18, 16, 18, 16), spacing=10)
        form_body.addWidget(label("设备登录", "section"))
        form_body.addWidget(label("填写板卡的 SSH 地址与账号。", "muted"))
        self.host = QLineEdit(DEVICE_DEFAULTS.get("host", ""))
        self.user = QLineEdit(DEVICE_DEFAULTS.get("user", "root"))
        self.password = QLineEdit(DEVICE_DEFAULTS.get("password", ""))
        self.password.setObjectName("password")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("请输入设备密码")
        for caption, field in (("设备 IP 或主机名", self.host), ("用户名", self.user), ("密码", self.password)):
            caption_widget = label(caption)
            caption_widget.setBuddy(field)
            field.setAccessibleName(caption)
            form_body.addWidget(caption_widget)
            form_body.addWidget(field)
        self.login_button = button("连接设备", self.login, "primary")
        self.password.returnPressed.connect(self.login)
        self.disconnect_button = button("取消并断开", self.disconnect_requested.emit)
        actions = QHBoxLayout()
        actions.addWidget(self.login_button)
        actions.addWidget(self.disconnect_button)
        form_body.addLayout(actions)
        form_body.addStretch()
        columns.addWidget(form, 5)

        progress, progress_body = box(self, margins=(18, 16, 18, 16), spacing=8)
        progress_body.addWidget(label("连接进度", "section"))
        self.rows = []
        for number, title in enumerate(STEPS, 1):
            row, row_body = box(progress, "step", (8, 4, 8, 4))
            row_layout = QHBoxLayout()
            row_body.addLayout(row_layout)
            badge = label(f"{number:02d}", "badge")
            row_layout.addWidget(badge)
            row_layout.addWidget(label(title), 1)
            status = label("等待", "muted")
            row_layout.addWidget(status)
            progress_body.addWidget(row)
            self.rows.append((row, badge, status))
        self.activity = QProgressBar()
        self.activity.setRange(0, 0)
        self.activity.setTextVisible(False)
        self.activity.setFixedHeight(5)
        self.activity.setStyleSheet("QProgressBar { border: none; background: #e4edfc; border-radius: 2px; }"
                                    "QProgressBar::chunk { background: #3476db; }")
        progress_body.addWidget(self.activity)
        self.message = label("", "status", wrap=True)
        self.message.setAccessibleName("连接状态")
        progress_body.addWidget(self.message)

        self.selectors = QWidget()
        selector_body = QVBoxLayout(self.selectors)
        selector_body.setContentsMargins(0, 0, 0, 0)
        self.node, node_field = self.picker_field("ISP 主链路")
        self.iq, self.iq_field = self.picker_field("IQ 基线文件")
        self.iq_field.hide()
        selector_body.addWidget(node_field)
        selector_body.addWidget(self.iq_field)
        self.node.currentIndexChanged.connect(self.node_changed)
        self.iq.currentIndexChanged.connect(self.update_selection)
        self.scan_button = button("重新扫描", self.scan_requested.emit)
        self.open_button = button("打开调参", self.select, "primary")
        select_actions = QHBoxLayout()
        select_actions.addWidget(self.scan_button)
        select_actions.addWidget(self.open_button)
        selector_body.addLayout(select_actions)
        progress_body.addWidget(self.selectors)
        progress_body.addStretch()
        columns.addWidget(progress, 6)
        self.elapsed = QTimer(self)
        self.elapsed.setInterval(250)
        self.elapsed.timeout.connect(self.update_elapsed)
        self.active = None
        self.reset()

    @staticmethod
    def picker_field(title):
        field = QWidget()
        layout = QVBoxLayout(field)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        picker = Picker()
        picker.setAccessibleName(title)
        caption = label(title)
        caption.setBuddy(picker)
        detail = label("", "muted", wrap=True)
        detail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        detail.hide()

        def show_detail(text):
            detail.setText(text)
            detail.setVisible(bool(text))

        picker.clipped_text_changed.connect(show_detail)
        for widget in (caption, picker, detail):
            layout.addWidget(widget)
        return picker, field

    def login(self):
        if self.login_button.isEnabled():
            self.login_requested.emit(self.host.text().strip(), self.user.text().strip(), self.password.text())

    def reset(self, message="", error=False):
        self.disconnect_button.hide()
        self.selectors.hide()
        self.set_form_enabled(True)
        self.set_stage(0, None, message or "等待连接", "error" if error else "")

    def set_form_enabled(self, enabled):
        for field in (self.host, self.user, self.password, self.login_button):
            field.setEnabled(enabled)

    def set_stage(self, completed, active, message, tone=""):
        self.elapsed.stop()
        self.active = active
        self.started = monotonic()
        for index, (row, badge, status) in enumerate(self.rows):
            update_style(row, "state", "active" if index == active else "")
            update_style(badge, "state", "active" if index == active else "done" if index < completed else "")
            status.setText("已完成" if index < completed else "等待")
        self.activity.setVisible(active is not None)
        if active is not None:
            self.update_elapsed()
            self.elapsed.start()
        self.message.setText(message)
        update_style(self.message, "tone", tone or ("busy" if active is not None else ""))

    def update_elapsed(self):
        if self.active is not None:
            self.rows[self.active][2].setText(f"进行中 · {int(monotonic() - self.started)} s")

    def connecting(self):
        self.set_form_enabled(False)
        self.selectors.hide()
        self.disconnect_button.setEnabled(True)
        self.disconnect_button.show()
        self.set_stage(0, 0, "正在建立 SSH 会话")

    def scanning(self):
        self.selectors.hide()
        self.set_stage(2, 2, "正在检测板卡上的 RKISP mainpath")

    def show_nodes(self, nodes, error=""):
        self.node.clear()
        self.node.addItem("请选择 ISP 主链路", None)
        for node in nodes:
            sensor = node["sensor"] or "无唯一 Sensor"
            self.node.addItem(f'{node["video"]}  ·  {sensor}  ·  {node["media"]}', node)
            if not node["sensor"]:
                self.node.model().item(self.node.count() - 1).setEnabled(False)
        self.node.setEnabled(bool(nodes))
        self.scan_button.setEnabled(True)
        self.selectors.show()
        self.update_selection()
        message = (error or f"发现 {len(nodes)} 条主链路，请选择后继续") if nodes else (
            error or "未发现 RKISP 主链路，可检查板卡后重新扫描")
        self.set_stage(3 if not error else 2, None, message, "error" if error or not nodes else "ok")
        if nodes:
            self.node.setFocus()

    def node_changed(self):
        self.iq.clear()
        self.iq_field.hide()
        self.update_selection()
        self.select_node(None)

    def update_selection(self):
        self.open_button.setEnabled(self.node.isEnabled() and self.iq.isEnabled()
                                    and bool(self.node.currentData()) and bool(self.iq.currentData()))

    def choose_iq(self, names, preferred, loaded):
        self.iq.clear()
        self.iq.addItem("请选择 IQ 基线文件", None)
        for name in names:
            suffix = " · 3A 启动加载" if name == loaded else " · 推荐" if name == preferred else ""
            self.iq.addItem(name + suffix, name)
        if preferred:
            self.iq.setCurrentIndex(self.iq.findData(preferred))
        self.iq_field.show()
        self.finish_selection(f"找到 {len(names)} 个匹配的 IQ 文件。请选择基线，再打开调参。", "ok")
        self.iq.setFocus()

    def finish_selection(self, message, tone="error"):
        self.node.setEnabled(True)
        self.iq.setEnabled(True)
        self.scan_button.setEnabled(True)
        self.update_selection()
        self.set_stage(3, None, message, tone)

    def select(self):
        if filename := self.iq.currentData():
            self.select_node(filename)

    def select_node(self, filename):
        node = self.node.currentData()
        if node and node["sensor"]:
            self.selection_requested.emit({"select": node["video"], "media": node["media"], "sensor": node["sensor"],
                                           "iq_file": filename})

    def selecting(self, lookup):
        for widget in (self.node, self.iq, self.scan_button, self.open_button):
            widget.setEnabled(False)
        message = "正在查找该 Sensor 的 IQ 文件" if lookup else "正在读取所选 IQ 基线并回读设备"
        self.set_stage(3, 3, message)

    def closing(self):
        self.selectors.hide()
        self.disconnect_button.setEnabled(False)
        self.set_stage(0, None, "正在关闭 SSH 并等待板端清理", "busy")
