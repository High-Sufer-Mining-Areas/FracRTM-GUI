from __future__ import annotations

from collections.abc import Callable

from PyQt5 import QtCore, QtGui, QtWidgets

from fracrtm.messages import message as msg
from fracrtm.ui.localization import mark_verbatim, ui_call, ui_construct
from fracrtm.ui.qt_helpers import LocalizedDialog
from fracrtm.ui.styles import DIALOG_STYLE


class ReferenceDelegate(QtWidgets.QStyledItemDelegate):
    def __init__(self, choices: Callable[[], list[str]], allow_blank: bool = True, parent=None):
        super().__init__(parent)
        self._choices = choices
        self._allow_blank = allow_blank

    def createEditor(self, parent, option, index):
        editor = mark_verbatim(QtWidgets.QComboBox(parent))
        editor.setEditable(False)
        if self._allow_blank:
            ui_call(editor, "addItem", "")
        ui_call(editor, "addItems", self._choices())
        return editor

    def setEditorData(self, editor, index):
        text = index.data(QtCore.Qt.EditRole) or ""
        position = editor.findText(text)
        if position < 0 and text:
            ui_call(editor, "addItem", text)
            position = editor.count() - 1
        editor.setCurrentIndex(max(0, position))

    def setModelData(self, editor, item_model, index):
        item_model.setData(index, editor.currentText(), QtCore.Qt.EditRole)


class MultiReferenceDialog(LocalizedDialog):
    def __init__(self, title: str, options: list[str], selected: list[str], parent=None):
        super().__init__(parent)
        ui_call(self, "setWindowTitle", title)
        self.resize(430, 500)
        self.setStyleSheet(DIALOG_STYLE)
        layout = QtWidgets.QVBoxLayout(self)
        note = ui_construct(
            QtWidgets.QLabel,
            msg("text.select.existing.objects.undefined.objects.are.excluded.from.this.list"),
        )
        note.setObjectName("PageDescription")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.search = ui_construct(QtWidgets.QLineEdit)
        ui_call(self.search, "setPlaceholderText", msg("text.search"))
        layout.addWidget(self.search)
        self.list = QtWidgets.QListWidget()
        layout.addWidget(self.list, 1)
        selected_set = set(selected)
        for value in options:
            item = ui_construct(QtWidgets.QListWidgetItem, value)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked if value in selected_set else QtCore.Qt.Unchecked)
            ui_call(self.list, "addItem", item)
        clear = ui_construct(QtWidgets.QPushButton, msg("text.clear.selection"))
        layout.addWidget(clear)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.search.textChanged.connect(self._filter)
        clear.clicked.connect(
            lambda: [
                self.list.item(i).setCheckState(QtCore.Qt.Unchecked)
                for i in range(self.list.count())
            ]
        )

    def _filter(self, text: str) -> None:
        query = text.strip().casefold()
        for index in range(self.list.count()):
            item = self.list.item(index)
            item.setHidden(bool(query and query not in item.text().casefold()))

    def selected_values(self) -> list[str]:
        return [
            self.list.item(i).text()
            for i in range(self.list.count())
            if self.list.item(i).checkState() == QtCore.Qt.Checked
        ]


class ScientificDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDecimals(323)
        self.setRange(1e-30, 1e30)
        self.setSingleStep(1e-12)
        self.setKeyboardTracking(False)

    def textFromValue(self, value: float) -> str:
        return repr(float(value))

    def valueFromText(self, text: str) -> float:
        try:
            return float(text.strip())
        except ValueError:
            return self.value()

    def validate(self, text: str, position: int):
        try:
            value = float(text.strip())
        except ValueError:
            if text.strip().lower() in {"", "+", "-", ".", "e", "+e", "-e"}:
                return (QtGui.QValidator.Intermediate, text, position)
            return (QtGui.QValidator.Invalid, text, position)
        state = (
            QtGui.QValidator.Acceptable
            if self.minimum() <= value <= self.maximum()
            else QtGui.QValidator.Intermediate
        )
        return (state, text, position)


class CompactDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDecimals(323)
        self.setKeyboardTracking(False)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    def textFromValue(self, value: float) -> str:
        return repr(float(value))

    def valueFromText(self, text: str) -> float:
        try:
            return float(text.strip())
        except ValueError:
            return self.value()


class ScientificValueEdit(QtWidgets.QLineEdit):
    def __init__(self, value: float | None, parent=None, *, optional: bool = False):
        super().__init__(parent)
        self.optional = optional
        validator = QtGui.QDoubleValidator(-1e308, 1e308, 300, self)
        validator.setNotation(QtGui.QDoubleValidator.ScientificNotation)
        self.setValidator(validator)
        self.setMinimumWidth(150)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setValue(value)
        self.editingFinished.connect(self._refresh_error_state)

    def setValue(self, value: float | None) -> None:
        ui_call(self, "setText", "" if value is None else repr(float(value)))
        self._refresh_error_state()

    def value(self) -> float | None:
        if self.optional and (not self.text().strip()):
            return None
        try:
            value = float(self.text().strip())
        except ValueError as exc:
            raise ValueError(
                msg("validation.tolerance.must.be.a.valid.number.in.scientific.notation")
            ) from exc
        if not value > 0:
            raise ValueError(msg("validation.tolerance.must.be.positive"))
        return value

    def _refresh_error_state(self) -> None:
        try:
            self.value()
        except ValueError:
            self.setStyleSheet("border:1px solid #b42318;")
            ui_call(
                self,
                "setToolTip",
                msg("text.optional.when.specified.enter.a.positive.value.for.example.1e.8"),
            )
        else:
            self.setStyleSheet("")
            ui_call(self, "setToolTip", "")


class SolverInputError(ValueError):
    def __init__(self, message, process):
        super().__init__(message)
        self.process = process
