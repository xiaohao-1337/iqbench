"""Small reusable widgets for the connection and tuning pages."""

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import (
    QComboBox, QFrame, QLabel, QPushButton, QSlider, QStyle, QStyledItemDelegate,
    QStyleOptionComboBox, QStyleOptionViewItem, QStylePainter, QVBoxLayout, QWidget,
)

from .theme import ACCENT, BORDER, MUTED


def label(text="", name="", *, wrap=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def button(text, callback, name=""):
    widget = QPushButton(text)
    widget.setObjectName(name)
    widget.clicked.connect(lambda: callback())
    return widget


def box(parent, name="card", margins=(16, 14, 16, 14), spacing=8):
    widget = QFrame(parent)
    widget.setObjectName(name)
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return widget, layout


class Combo(QComboBox):
    def text_width(self, option):
        edit = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxEditField, self,
        )
        return max(0, edit.width() - 4)

    def paintEvent(self, _event):
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        option.currentText = option.fontMetrics.elidedText(
            option.currentText, Qt.TextElideMode.ElideRight, self.text_width(option),
        )
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(MUTED if self.isEnabled() else "#9aaabd"), 1.5))
        x, y = self.width() - 13, self.height() / 2
        path = QPainterPath(QPointF(x - 3, y - 1))
        path.lineTo(x, y + 2)
        path.lineTo(x + 3, y - 1)
        painter.drawPath(path)

    def wheelEvent(self, event):
        event.ignore()


class PickerItem(QStyledItemDelegate):
    horizontal_padding = 8
    vertical_padding = 5
    text_flags = Qt.TextFlag.TextWrapAnywhere.value | Qt.AlignmentFlag.AlignVCenter.value

    def __init__(self, parent):
        super().__init__(parent)
        self.text_width = 1

    def sizeHint(self, option, index):
        item = QStyleOptionViewItem(option)
        self.initStyleOption(item, index)
        metrics = QFontMetrics(item.font)
        height = metrics.boundingRect(QRect(0, 0, self.text_width, 0), self.text_flags, item.text).height()
        return QSize(self.text_width + 2 * self.horizontal_padding,
                     max(height, metrics.height()) + 2 * self.vertical_padding)

    def paint(self, painter, option, index):
        item = QStyleOptionViewItem(option)
        self.initStyleOption(item, index)
        text, item.text = item.text, ""
        item.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, item, painter, item.widget)
        group = (QPalette.ColorGroup.Normal if item.state & QStyle.StateFlag.State_Enabled
                 else QPalette.ColorGroup.Disabled)
        role = (QPalette.ColorRole.HighlightedText if item.state & QStyle.StateFlag.State_Selected
                else QPalette.ColorRole.Text)
        rect = item.rect.adjusted(self.horizontal_padding, self.vertical_padding,
                                  -self.horizontal_padding, -self.vertical_padding)
        rect.setWidth(min(rect.width(), self.text_width))
        painter.save()
        painter.setFont(item.font)
        painter.setPen(item.palette.color(group, role))
        painter.drawText(rect, self.text_flags, text)
        painter.restore()


class Picker(Combo):
    clipped_text_changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("picker")
        self.setSizeAdjustPolicy(self.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.items = PickerItem(self)
        self.setItemDelegate(self.items)
        self.currentIndexChanged.connect(self.update_caption)

    def update_caption(self):
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        text = self.currentText() if self.currentData() is not None else ""
        self.setToolTip(text)
        clipped = option.fontMetrics.horizontalAdvance(text) > self.text_width(option)
        self.clipped_text_changed.emit(text if clipped else "")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_caption()

    def showPopup(self):
        if not self.count():
            return
        screen = self.screen().availableGeometry()
        view = self.view()
        scrollbar = view.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent, widget=view)
        gutter = 2 * PickerItem.horizontal_padding + scrollbar + 8
        longest = max(view.fontMetrics().horizontalAdvance(self.itemText(row)) for row in range(self.count()))
        width = min(max(1, screen.width() - 8), max(self.width(), longest + gutter))
        # Reserve scrollbar space so wrapped labels keep the same height when scrolling.
        self.items.text_width = max(1, width - gutter)
        popup = view.window()
        popup.setFixedWidth(width)
        view.doItemsLayout()
        super().showPopup()

        chrome = popup.height() - view.viewport().height()
        visible_rows = min(self.count(), self.maxVisibleItems())
        row_height = sum(view.sizeHintForRow(row) for row in range(visible_rows))
        height_limit = max(1, screen.height() - 8)
        popup.resize(width, min(height_limit, row_height + chrome))
        popup.layout().activate()
        missing = row_height - view.viewport().height()
        if missing > 0:
            popup.resize(width, min(height_limit, popup.height() + missing))
        frame = popup.frameGeometry()
        below = self.mapToGlobal(QPoint(0, self.height())).y()
        above = self.mapToGlobal(QPoint()).y() - frame.height()
        y = below if below + frame.height() <= screen.bottom() + 1 else above
        popup.move(
            max(screen.left(), min(frame.left(), screen.right() - frame.width() + 1)),
            max(screen.top(), min(y, screen.bottom() - frame.height() + 1)),
        )


class Slider(QSlider):
    def wheelEvent(self, event):
        event.ignore()


GAMMA_X = (
    0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 14, 16, 20, 24, 28, 32, 40, 48, 56, 64,
    80, 96, 112, 128, 160, 192, 224, 256, 320, 384, 448, 512, 640, 768, 896, 1024,
    1280, 1536, 1792, 2048, 2304, 2560, 2816, 3072, 3328, 3584, 3840, 4095,
)
GAMMA_HANDLES = tuple(GAMMA_X.index(x) for x in (0, 128, 320, 640, 1024, 1536, 2304, 3072, 3584, 4095))


def drag_gamma(curve, handle, value):
    left, center, right = GAMMA_HANDLES[handle - 1:handle + 2]
    value = min(curve[right], max(curve[left], value))
    adjusted = list(curve)
    for start, end, low, high in ((left, center, curve[left], value), (center, right, value, curve[right])):
        span = curve[end] - curve[start]
        for index in range(start, end + 1):
            ratio = ((curve[index] - curve[start]) / span if span else
                     (GAMMA_X[index] - GAMMA_X[start]) / (GAMMA_X[end] - GAMMA_X[start]))
            adjusted[index] = round(low + (high - low) * ratio)
    return adjusted


class GammaPlot(QWidget):
    changed = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(242)
        self.setAccessibleName("Gamma 整体曲线")
        self.curve = self.reference = self.drag = None
        self.editable = False

    def plot_rect(self):
        return QRectF(self.rect()).adjusted(45, 24, -22, -31)

    def point(self, x, y):
        rect = self.plot_rect()
        return QPointF(rect.left() + rect.width() * x / 4095, rect.bottom() - rect.height() * y / 4095)

    def render_curves(self, curve, reference, editable):
        if not editable:
            self.drag = None
            self.unsetCursor()
        curve = self.curve if self.drag is not None else curve
        if (curve, reference, editable) != (self.curve, self.reference, self.editable):
            self.curve, self.reference, self.editable = curve, reference, editable
            self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.plot_rect()
        for value in (0, 1024, 2048, 3072, 4095):
            point = self.point(value, value)
            painter.setPen(QColor(BORDER))
            painter.drawLine(QPointF(point.x(), rect.top()), QPointF(point.x(), rect.bottom()))
            painter.drawLine(QPointF(rect.left(), point.y()), QPointF(rect.right(), point.y()))
            painter.setPen(QColor(MUTED))
            painter.drawText(QRectF(point.x() - 20, rect.bottom() + 4, 40, 17), Qt.AlignmentFlag.AlignCenter, str(value))
            painter.drawText(QRectF(0, point.y() - 8, 38, 16), Qt.AlignmentFlag.AlignRight, str(value))
        for curve, color, width, style in (
            (self.reference, "#7289a6", 2, Qt.PenStyle.DashLine),
            (self.curve, ACCENT, 2.6, Qt.PenStyle.SolidLine),
        ):
            if not curve:
                continue
            path = QPainterPath(self.point(0, curve[0]))
            for x, y in zip(GAMMA_X[1:], curve[1:]):
                path.lineTo(self.point(x, y))
            painter.setPen(QPen(QColor(color), width, style))
            painter.drawPath(path)
        if self.editable and self.curve:
            painter.setBrush(QColor("white"))
            painter.setPen(QPen(QColor(ACCENT), 2))
            for index in GAMMA_HANDLES[1:-1]:
                painter.drawEllipse(self.point(GAMMA_X[index], self.curve[index]), 4.7, 4.7)

    def mousePressEvent(self, event):
        if not (self.editable and self.curve and event.button() == Qt.MouseButton.LeftButton):
            return
        distances = [(self.point(GAMMA_X[index], self.curve[index]) - event.position()).manhattanLength()
                     for index in GAMMA_HANDLES[1:-1]]
        if min(distances) <= 19:
            self.drag = (distances.index(min(distances)) + 1, list(self.curve))
            self.setCursor(Qt.CursorShape.SizeVerCursor)

    def mouseMoveEvent(self, event):
        if self.drag is None:
            return
        handle, initial = self.drag
        rect = self.plot_rect()
        value = round((rect.bottom() - event.position().y()) * 4095 / rect.height())
        adjusted = drag_gamma(initial, handle, value)
        if adjusted != self.curve:
            self.curve = adjusted
            self.update()
            self.changed.emit(adjusted)

    def mouseReleaseEvent(self, _event):
        self.drag = None
        self.unsetCursor()
