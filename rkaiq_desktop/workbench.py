"""Read-only device context and editable live ISP controls."""

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QGridLayout, QHBoxLayout, QHeaderView, QPushButton,
    QScrollArea, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from board.rkaiq_controls import (
    AE_CONTROLS, Control, DETAIL_CONTROLS, EXPOSURE_IQ_CONTROLS, TONE_CONTROLS,
    WB_CCT, WB_CONTROLS, WB_GAIN, WB_MANUAL, WB_SCENE,
)

from .theme import update_style
from .widgets import Combo, GammaPlot, Slider, box, button, label


COLORS = {key: Control(name, "", 0, 255, 1) for key, name in (
    ("brightness", "亮度"), ("contrast", "对比度"), ("saturation", "饱和度"), ("hue", "色相"),
)}
MODES = {
    "gray": Control("黑白模式", "", choices=((1, "关闭"), (2, "开启"))),
    "enable": Control("色彩处理（CPROC）", ""),
}
GAMMA = {
    "gamma_enable": Control("启用 Gamma", ""),
    "gamma_offset": Control("输出偏移", "", 0, 4095, 1),
}
PARAMETERS = {**COLORS, **MODES, **GAMMA, **AE_CONTROLS, **EXPOSURE_IQ_CONTROLS,
              **WB_CONTROLS, **DETAIL_CONTROLS, **TONE_CONTROLS}
PAGES = ("基本参数", "Gamma 曲线", "曝光", "白平衡", "ISP 模块", "IQ 默认值")
METADATA = {"host": "设备地址", "video": "ISP 主链路", "media": "Media 节点",
            "sensor": "Sensor", "iq_file": "IQ 基线文件", "loaded_iq": "3A 启动加载"}


def display_value(key, value):
    if key == "gamma_curve":
        return f"{len(value)} 个采样点"
    if key == "gray" and value == 0:
        return "跟随补光"
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "开启" if value else "关闭"
    if spec := PARAMETERS.get(key):
        if spec.choices:
            return dict(spec.choices).get(value, str(value))
    return f"{value:.6g}" if isinstance(value, (int, float)) else str(value)


class ParameterRow(QWidget):
    changed = Signal(str, object)

    def __init__(self, key):
        super().__init__()
        self.key = key
        self.spec = spec = PARAMETERS[key]
        self.rendered = None
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(10)
        caption = label(spec.label, wrap=True)
        caption.setMinimumWidth(133)
        row.addWidget(caption)
        if spec.choices:
            control = Combo()
            for value, text in spec.choices:
                control.addItem(text, value)
            control.activated.connect(lambda index: self.edit(control.itemData(index)))
        elif spec.step is not None:
            control = Slider(Qt.Orientation.Horizontal)
            control.setMinimumHeight(23)
            control.setRange(0, round((spec.maximum - spec.minimum) / spec.step))
            control.setPageStep(max(1, control.maximum() // 16))
            control.valueChanged.connect(self.slide)
        else:
            control = QPushButton()
            control.setObjectName("toggle")
            control.setCheckable(True)
            control.setMinimumWidth(64)
            control.toggled.connect(self.toggle)
        self.control = control
        control.setAccessibleName(spec.label)
        caption.setBuddy(control)
        if spec.choices or spec.step is None:
            row.addStretch()
        row.addWidget(control, 1 if spec.step is not None else 0)
        self.value = label("—", "value")
        self.value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.value.setMinimumWidth(61)
        self.value.setVisible(spec.step is not None)
        row.addWidget(self.value)
        self.iq = label("—", "muted")
        self.iq.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.iq.setMinimumWidth(69)
        row.addWidget(self.iq)

    def slide(self, index):
        spec = self.spec
        raw = spec.minimum + index * spec.step
        value = int(raw) if isinstance(spec.step, int) else round(raw, 6)
        self.value.setText(display_value(self.key, value))
        self.edit(value)

    def toggle(self, checked):
        self.control.setText("开启" if checked else "关闭")
        self.edit(checked)

    def edit(self, value):
        self.rendered = None
        self.changed.emit(self.key, value)

    def show_default(self, value):
        self.iq.setText(display_value(self.key, value))
        self.iq.setToolTip("IQ 文件基线")

    def render(self, value, enabled, pending):
        if self.rendered == (value, enabled, pending):
            return
        self.rendered = value, enabled, pending
        control, spec = self.control, self.spec
        control.setEnabled(enabled)
        if spec.choices:
            with QSignalBlocker(control):
                index = control.findData(value) if value is not None else -1
                if index < 0:
                    control.setPlaceholderText(display_value(self.key, value))
                control.setCurrentIndex(index)
        elif spec.step is not None:
            self.value.setText(display_value(self.key, value))
            if value is not None:
                with QSignalBlocker(control):
                    control.setValue(round((value - spec.minimum) / spec.step))
        else:
            with QSignalBlocker(control):
                control.setChecked(bool(value))
            control.setText(display_value(self.key, value))
        control.setToolTip("正在等待设备确认" if pending else "")
        self.value.setToolTip(control.toolTip())


class Workbench(QWidget):
    changed = Signal(str, object)
    save_requested = Signal()
    refresh_requested = Signal()
    restore_requested = Signal()
    gamma_requested = Signal()
    scan_requested = Signal()
    disconnect_requested = Signal()
    quit_requested = Signal()

    def __init__(self):
        super().__init__()
        self.rows = {}
        self.defaults = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(10)
        body = QHBoxLayout()
        body.setSpacing(12)
        outer.addLayout(body, 1)

        rail, nav = box(self, "rail", (11, 14, 11, 14), 6)
        rail.setFixedWidth(152)
        nav.addWidget(label("调参工作台", "inverse"))
        nav.addWidget(label("RKISP / ISP30", "inverseNote"))
        nav.addSpacing(8)
        self.pages = QStackedWidget()
        self.navigation = QButtonGroup(self)
        for index, name in enumerate(PAGES):
            navigation = button(name, lambda index=index: self.pages.setCurrentIndex(index), "nav")
            navigation.setCheckable(True)
            self.navigation.addButton(navigation, index)
            nav.addWidget(navigation)
        nav.addStretch()
        self.node_button = button("重选节点 / IQ", self.scan_requested.emit, "nav")
        nav.addWidget(self.node_button)
        nav.addWidget(button("切换设备", self.disconnect_requested.emit, "nav"))
        body.addWidget(rail)

        content = QVBoxLayout()
        content.setSpacing(9)
        body.addLayout(content, 1)
        context, context_body = box(self, "readonly", (13, 8, 13, 8), 5)
        heading = QHBoxLayout()
        heading.addWidget(label("设备与校准信息", "eyebrow"))
        heading.addWidget(label("只读", "badge"))
        heading.addStretch()
        context_body.addLayout(heading)
        fields = QGridLayout()
        fields.setHorizontalSpacing(12)
        fields.setVerticalSpacing(3)
        self.metadata = {}
        for index, (key, title) in enumerate(METADATA.items()):
            column = index % 3
            line = index // 3
            field = label("—", wrap=True)
            field.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            field.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            fields.addWidget(label(title, "muted"), line * 2, column)
            fields.addWidget(field, line * 2 + 1, column)
            fields.setColumnStretch(column, 1)
            self.metadata[key] = field
        context_body.addLayout(fields)
        content.addWidget(context)
        content.addWidget(self.pages, 1)
        self.build_pages()

        footer, footer_body = box(self, "footer", (12, 8, 12, 8), 6)
        self.status = label("", "status", wrap=True)
        self.status.setAccessibleName("调参状态")
        footer_body.addWidget(self.status)
        actions = QHBoxLayout()
        self.refresh = button("刷新读数", self.refresh_requested.emit)
        self.restore = button("恢复连接时的值", self.restore_requested.emit)
        self.save = button("全局保存 JSON", self.save_requested.emit, "primary")
        actions.addWidget(self.refresh)
        actions.addWidget(self.restore)
        actions.addStretch()
        actions.addWidget(button("退出", self.quit_requested.emit))
        actions.addWidget(self.save)
        footer_body.addLayout(actions)
        outer.addWidget(footer)

    def build_pages(self):
        layouts = []
        for name in PAGES:
            page = QWidget()
            page.setObjectName("surface")
            layout = QVBoxLayout(page)
            layout.setContentsMargins(1, 0, 6, 6)
            layout.setSpacing(8)
            layout.addWidget(label(name, "section"))
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(page)
            self.pages.addWidget(scroll)
            layouts.append(layout)
        basic, gamma, exposure, balance, modules, defaults = layouts
        self.group(basic, "图像模式", MODES)
        self.group(basic, "色彩参数", COLORS, "左侧为可调值，最右列为 IQ 文件基线。")

        gamma_card, gamma_body = box(self)
        gamma_tools = QHBoxLayout()
        gamma_tools.addWidget(label("整体 Gamma · 49 点", "eyebrow"))
        gamma_tools.addStretch()
        self.gamma_reset = button("恢复 IQ 曲线", self.gamma_requested.emit)
        gamma_tools.addWidget(self.gamma_reset)
        gamma_body.addLayout(gamma_tools)
        gamma_body.addWidget(label("拖动蓝色控制点调整输出。蓝色实线为当前曲线，灰色虚线为 IQ 参考。", "muted", wrap=True))
        self.gamma_plot = GammaPlot()
        self.gamma_plot.changed.connect(lambda curve: self.changed.emit("gamma_curve", curve))
        gamma_body.addWidget(self.gamma_plot)
        gamma.addWidget(gamma_card)
        self.group(gamma, "曲线设置", GAMMA)
        self.group(exposure, "工作模式", ("ae_mode",))
        self.group(exposure, "手动曝光", ("ae_time", "ae_gain"), "切换到手动曝光后调整。")
        self.group(exposure, "自动曝光", EXPOSURE_IQ_CONTROLS)
        self.group(balance, "工作模式", ("wb_mode", "wb_manual"))
        self.group(balance, "色温与光源", ("wb_temperature", "wb_scene"))
        self.group(balance, "通道增益", ("wb_r", "wb_gr", "wb_gb", "wb_b"))
        self.group(modules, "降噪与细节", DETAIL_CONTROLS)
        self.group(modules, "动态范围与校正", TONE_CONTROLS)
        defaults.addWidget(label("所选 JSON 的 normal/day ISP30 场景。", "muted", wrap=True))
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(("参数", "IQ 文件默认值"))
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(235)
        defaults.addWidget(self.table, 1)
        for page in layouts[:-1]:
            page.addStretch()

    def group(self, parent, title, keys, note=""):
        card, layout = box(self, margins=(12, 10, 12, 10), spacing=4)
        heading = QHBoxLayout()
        heading.addWidget(label(title, "eyebrow"))
        heading.addStretch()
        iq = label("IQ 默认", "muted")
        iq.setMinimumWidth(69)
        iq.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading.addWidget(iq)
        layout.addLayout(heading)
        if note:
            layout.addWidget(label(note, "muted", wrap=True))
        for key in keys:
            row = self.rows[key] = ParameterRow(key)
            row.changed.connect(self.changed)
            layout.addWidget(row)
        parent.addWidget(card)

    def open(self, host, reply):
        self.defaults = reply["defaults"]
        for key, widget in self.metadata.items():
            widget.setText(host if key == "host" else reply.get(key) or "未确认")
            widget.setToolTip(widget.text())
        for key, row in self.rows.items():
            row.show_default(self.defaults.get(key))
        entries = [(key, value) for key, value in self.defaults.items() if key in PARAMETERS or key == "gamma_curve"]
        self.table.setRowCount(len(entries))
        for index, (key, value) in enumerate(entries):
            name = PARAMETERS[key].label if key in PARAMETERS else "Gamma 曲线"
            self.table.setItem(index, 0, QTableWidgetItem(name))
            self.table.setItem(index, 1, QTableWidgetItem(display_value(key, value)))
        self.pages.setCurrentIndex(5 if "offline" in reply else 0)
        self.navigation.button(self.pages.currentIndex()).setChecked(True)

    def set_status(self, text, tone=""):
        self.status.setText(text)
        update_style(self.status, "tone", tone)

    def render(self, state, targets, editable):
        manual_ae = state.get("ae_mode") == "RK_AIQ_OP_MODE_MANUAL"
        wb_mode = state.get("wb_manual") if state.get("wb_mode") == WB_MANUAL else None
        enabled = {
            "ae_time": manual_ae, "ae_gain": manual_ae, "ae_ev": not manual_ae,
            "ae_flicker": not manual_ae, "ae_flicker_hz": not manual_ae and state.get("ae_flicker", False),
            "wb_manual": wb_mode is not None, "wb_temperature": wb_mode == WB_CCT, "wb_scene": wb_mode == WB_SCENE,
            **dict.fromkeys(("wb_r", "wb_gr", "wb_gb", "wb_b"), wb_mode == WB_GAIN),
        }
        for key, row in self.rows.items():
            row.render(targets.get(key, state.get(key)), editable and key in state and enabled.get(key, True),
                       key in targets)
        self.gamma_plot.render_curves(targets.get("gamma_curve", state.get("gamma_curve")),
                                      self.defaults.get("gamma_curve"), editable and "gamma_curve" in state)
        self.gamma_reset.setEnabled(editable and any(key in self.defaults and key in state for key in (*GAMMA, "gamma_curve")))
        for widget in (self.refresh, self.restore, self.save):
            widget.setEnabled(editable)
