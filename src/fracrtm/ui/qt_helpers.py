from PyQt5 import QtCore, QtWidgets

from fracrtm.messages import (
    Message,
    diagnostic_message,
    exception_text,
    raw_diagnostics,
)
from fracrtm.messages import message as msg

from .localization import localize_dialog, ui_call, ui_construct


class LocalizedDialog(QtWidgets.QDialog):
    def exec_(self):
        localize_dialog(self)
        return super().exec_()

    def showEvent(self, event):
        localize_dialog(self)
        super().showEvent(event)


class MessageBox(QtWidgets.QMessageBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._raw_details = ""

    def _set_message(self, value):
        source = value if isinstance(value, Message) else diagnostic_message(value)
        self._fracrtm_bindings = getattr(self, "_fracrtm_bindings", {})
        self._fracrtm_bindings[("setText",)] = source
        diagnostics = raw_diagnostics(source)
        self._raw_details = "\n\n".join(dict.fromkeys(diagnostics))
        QtWidgets.QMessageBox.setText(self, source.render())
        if self._raw_details:
            QtWidgets.QMessageBox.setDetailedText(self, self._raw_details)

    def setText(self, value):
        self._set_message(value)

    def setDetailedText(self, text):
        value = str(text)
        if self._raw_details and value != self._raw_details:
            value = self._raw_details + "\n\n" + value
        super().setDetailedText(value)

    def exec_(self):
        localize_dialog(self)
        return super().exec_()

    def showEvent(self, event):
        localize_dialog(self)
        super().showEvent(event)

    @classmethod
    def _show(cls, icon, parent, title, text, buttons, default):
        box = cls(parent)
        box.setIcon(icon)
        ui_call(box, "setWindowTitle", title)
        box.setText(text)
        box.setStandardButtons(buttons)
        if default != cls.NoButton:
            box.setDefaultButton(default)
        box.exec_()
        return box.standardButton(box.clickedButton())

    @classmethod
    def warning(
        cls,
        parent,
        title,
        text,
        buttons=QtWidgets.QMessageBox.Ok,
        default=QtWidgets.QMessageBox.NoButton,
    ):
        return cls._show(cls.Warning, parent, title, text, buttons, default)

    @classmethod
    def critical(
        cls,
        parent,
        title,
        text,
        buttons=QtWidgets.QMessageBox.Ok,
        default=QtWidgets.QMessageBox.NoButton,
    ):
        return cls._show(cls.Critical, parent, title, text, buttons, default)

    @classmethod
    def information(
        cls,
        parent,
        title,
        text,
        buttons=QtWidgets.QMessageBox.Ok,
        default=QtWidgets.QMessageBox.NoButton,
    ):
        return cls._show(cls.Information, parent, title, text, buttons, default)

    @classmethod
    def question(
        cls,
        parent,
        title,
        text,
        buttons=QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        default=QtWidgets.QMessageBox.NoButton,
    ):
        return cls._show(cls.Question, parent, title, text, buttons, default)


class RunLogDialog(LocalizedDialog):
    def __init__(self, text, parent=None, *, translate_content=False):
        super().__init__(parent)
        ui_call(self, "setWindowTitle", msg("text.run.log"))
        self.resize(850, 520)
        layout = QtWidgets.QVBoxLayout(self)
        output = ui_construct(QtWidgets.QPlainTextEdit, text)
        output.setReadOnly(True)
        layout.addWidget(output)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class AdaptiveDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    def textFromValue(self, value):
        return f"{value:g}"


class WorkerSignals(QtCore.QObject):
    succeeded = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(object)
    diagnostic = QtCore.pyqtSignal(object)


class BackgroundCall(QtCore.QRunnable):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self.signals = WorkerSignals()
        self.last_failure = None
        self.diagnostic_path = None
        self.diagnostic_storage_error = None

    @QtCore.pyqtSlot()
    def run(self):
        try:
            self.signals.succeeded.emit(self.callback())
        except Exception as exc:
            from fracrtm.runtime.diagnostics import FailureDiagnostic

            self.last_failure = FailureDiagnostic.capture(exc)
            from pathlib import Path

            from fracrtm.runtime.diagnostics import persist_diagnostic

            location = QtCore.QStandardPaths.writableLocation(
                QtCore.QStandardPaths.GenericDataLocation
            )
            try:
                self.diagnostic_path = persist_diagnostic(
                    self.last_failure, Path(location) / "FracRTM-GUI/diagnostics"
                )
            except OSError as storage_error:
                self.diagnostic_storage_error = str(storage_error)
            self.signals.diagnostic.emit(self.last_failure)
            self.signals.failed.emit(exception_text(exc))
