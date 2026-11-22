from __future__ import annotations

import os
from pathlib import Path

from PyQt5 import QtCore, QtWidgets, sip

from fracrtm.messages import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    Message,
    canonical_text,
    current_locale,
    diagnostic_message,
    message,
    render_text,
    restore_text,
)
from fracrtm.messages import format_message as format_message
from fracrtm.messages import set_locale as _set_locale
from fracrtm.messages import tr as tr
from fracrtm.runtime.settings import settings_store

LANGUAGE_NAMES = {
    locale: message("language.english" if locale == "en_US" else "language.chinese")
    for locale in SUPPORTED_LOCALES
}
MESSAGE_ROLE = QtCore.Qt.UserRole + 117
VERBATIM_TEXT_ROLE = QtCore.Qt.UserRole + 118
HEADER_ROLE = QtCore.Qt.UserRole + 119
TOOLTIP_MESSAGE_ROLE = QtCore.Qt.UserRole + 120


def set_locale(locale):
    _set_locale(locale)
    QtCore.QLocale.setDefault(QtCore.QLocale(locale))
    _install_qt_translation()
    return locale


def configure_startup_locale(argv):
    clean = list(argv[:1])
    requested = None
    index = 1
    while index < len(argv):
        argument = argv[index]
        if argument.startswith("--language="):
            requested = argument.split("=", 1)[1]
        elif argument == "--language":
            if index + 1 >= len(argv):
                raise ValueError("--language requires en_US or zh_CN")
            requested = argv[index + 1]
            index += 1
        else:
            clean.append(argument)
        index += 1
    requested = requested or os.environ.get("FRACRTM_LANGUAGE")
    if requested is None:
        requested = settings_store().value("display_language", DEFAULT_LOCALE, type=str)
    set_locale(requested)
    return clean


def diagnostic_summary(value, locale=None):
    value = diagnostic_message(value)
    return value if locale is None else value.render(locale)


def mark_verbatim(widget):
    widget.setProperty("fracrtm_user_content", True)
    return widget


def _bindings(obj):
    bindings = getattr(obj, "_fracrtm_bindings", None)
    if bindings is None:
        bindings = {}
        obj._fracrtm_bindings = bindings
    return bindings


def _verbatim(obj):
    return isinstance(obj, QtCore.QObject) and bool(
        QtCore.QObject.property(obj, "fracrtm_user_content")
    )


def _native(obj, method, *args, **kwargs):
    if isinstance(obj, QtWidgets.QMessageBox) and method in {
        "setText",
        "setWindowTitle",
        "setDetailedText",
    }:
        return getattr(QtWidgets.QMessageBox, method)(obj, *args, **kwargs)
    return getattr(obj, method)(*args, **kwargs)


def _bind(obj, method, value, prefix=()):
    if (
        isinstance(obj, QtWidgets.QMessageBox)
        and method == "setText"
        and hasattr(obj, "_set_message")
    ):
        return obj._set_message(value)
    key = (method, *prefix)
    if isinstance(value, Message) and not _verbatim(obj):
        _bindings(obj)[key] = value
        return _native(obj, method, *prefix, value.render())
    _bindings(obj).pop(key, None)
    return _native(obj, method, *prefix, canonical_text(value) if _verbatim(obj) else value)


def _item_message(item, column=0):
    payload = (
        item.data(column, MESSAGE_ROLE)
        if isinstance(item, QtWidgets.QTreeWidgetItem)
        else item.data(MESSAGE_ROLE)
    )
    return restore_text(payload) if payload else None


def _set_item(item, value, column=0, *, header=False):
    payload = value.to_record() if isinstance(value, Message) else None
    text = value.render() if payload and header else canonical_text(value)
    if isinstance(item, QtWidgets.QTreeWidgetItem):
        item.setData(column, MESSAGE_ROLE, payload)
        item.setData(column, HEADER_ROLE, header)
        item.setText(column, text)
    else:
        item.setData(MESSAGE_ROLE, payload)
        item.setData(HEADER_ROLE, header)
        item.setText(text)


def ui_construct(factory, *args, **kwargs):
    values = list(args)
    source = None
    for index, value in enumerate(values):
        if isinstance(value, (str, Message)):
            source = value
            values[index] = canonical_text(value)
            break
    if factory is QtWidgets.QTreeWidgetItem:
        values = [
            [canonical_text(item) for item in value] if isinstance(value, (list, tuple)) else value
            for value in values
        ]
    widget = factory(*values, **kwargs)
    if isinstance(widget, QtWidgets.QTreeWidgetItem):
        cells = next((value for value in args if isinstance(value, (list, tuple))), [])
        for column, value in enumerate(cells):
            _set_item(widget, value, column)
    elif isinstance(widget, (QtWidgets.QTableWidgetItem, QtWidgets.QListWidgetItem)):
        if source is not None:
            _set_item(widget, source)
    elif source is not None:
        method = (
            "setTitle"
            if isinstance(widget, QtWidgets.QGroupBox)
            else "setPlainText"
            if isinstance(widget, QtWidgets.QPlainTextEdit)
            else "setText"
        )
        if hasattr(widget, method):
            _bind(widget, method, source)
    return widget


def ui_call(obj, method, *args, **kwargs):
    if method == "addButton" and args and isinstance(args[0], Message):
        button = obj.addButton(canonical_text(args[0]), *args[1:], **kwargs)
        _bind(button, "setText", args[0])
        return button
    if method == "setToolTip" and isinstance(
        obj, (QtWidgets.QTreeWidgetItem, QtWidgets.QTableWidgetItem, QtWidgets.QListWidgetItem)
    ):
        value = args[-1]
        payload = value.to_record() if isinstance(value, Message) else None
        if isinstance(obj, QtWidgets.QTreeWidgetItem):
            obj.setData(args[0], TOOLTIP_MESSAGE_ROLE, payload)
        else:
            obj.setData(TOOLTIP_MESSAGE_ROLE, payload)
        return obj.setToolTip(*args[:-1], render_text(value))
    if isinstance(obj, QtWidgets.QTreeWidgetItem) and method == "setText":
        return _set_item(obj, args[1], args[0], header=bool(obj.data(args[0], HEADER_ROLE)))
    if (
        isinstance(obj, (QtWidgets.QTableWidgetItem, QtWidgets.QListWidgetItem))
        and method == "setText"
    ):
        return _set_item(obj, args[0], header=bool(obj.data(HEADER_ROLE)))
    if (
        method
        in {
            "setText",
            "setTitle",
            "setWindowTitle",
            "setToolTip",
            "setPlaceholderText",
            "setPlainText",
            "setSpecialValueText",
        }
        and len(args) == 1
        and isinstance(obj, QtCore.QObject)
    ):
        return _bind(obj, method, args[0])
    if method in {"setTabText", "setTabToolTip"}:
        return _bind(obj, method, args[1], (args[0],))
    if method == "insertRow" and len(args) > 1 and isinstance(args[1], Message):
        return obj.insertRow(args[0], ui_construct(QtWidgets.QLabel, args[1]), *args[2:])
    if method == "addRow" and args and isinstance(args[0], Message):
        return obj.addRow(ui_construct(QtWidgets.QLabel, args[0]), *args[1:])
    if method == "addItems" and isinstance(obj, (QtWidgets.QComboBox, QtWidgets.QListWidget)):
        for value in args[0]:
            ui_call(obj, "addItem", value)
        return None
    if method in {"addItem", "insertItem"} and isinstance(obj, QtWidgets.QComboBox):
        values = list(args)
        label_index = next(
            (i for i, value in enumerate(values) if isinstance(value, (str, Message))), None
        )
        source = values[label_index] if label_index is not None else None
        if source is not None:
            values[label_index] = canonical_text(source)
        result = getattr(obj, method)(*values, **kwargs)
        row = obj.count() - 1 if method == "addItem" else args[0]
        if isinstance(source, Message):
            obj.setItemData(row, source.to_record(), MESSAGE_ROLE)
        return result
    if method in {"addItem", "insertItem"} and isinstance(obj, QtWidgets.QListWidget):
        source = args[-1]
        if isinstance(source, (str, Message)):
            item = ui_construct(QtWidgets.QListWidgetItem, source)
            return obj.addItem(item) if method == "addItem" else obj.insertItem(args[0], item)
    if method == "addTab":
        values = list(args)
        source = values[-1]
        values[-1] = render_text(source)
        index = obj.addTab(*values, **kwargs)
        if isinstance(source, Message):
            _bindings(obj)[("setTabText", index)] = source
        return index
    if method == "setHeaderLabels" and isinstance(obj, QtWidgets.QTreeWidget):
        obj.setHeaderLabels([render_text(value) for value in args[0]])
        for column, value in enumerate(args[0]):
            _set_item(obj.headerItem(), value, column, header=True)
        return None
    if method in {"setHorizontalHeaderLabels", "setVerticalHeaderLabels"} and isinstance(
        obj, QtWidgets.QTableWidget
    ):
        getattr(obj, method)([render_text(value) for value in args[0]])
        for index, value in enumerate(args[0]):
            item = (
                obj.horizontalHeaderItem(index)
                if method == "setHorizontalHeaderLabels"
                else obj.verticalHeaderItem(index)
            )
            if item is not None:
                _set_item(item, value, header=True)
        return None
    return getattr(obj, method)(*args, **kwargs)


def set_label_text(label, source):
    return ui_call(label, "setText", source)


class _MessageDelegate(QtWidgets.QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        payload = index.data(MESSAGE_ROLE)
        if payload and not index.data(VERBATIM_TEXT_ROLE):
            option.text = restore_text(payload).render()


class _StandardButtonTranslator(QtCore.QTranslator):
    def isEmpty(self):
        return False

    def translate(self, context, sourceText, disambiguation=None, n=-1):
        from fracrtm.messages import _CATALOGS

        catalog = _CATALOGS[current_locale()]
        key = "qt.context." + context + "." + sourceText
        if key in catalog:
            return catalog[key]
        if context in {"QPlatformTheme", "QDialogButtonBox", "QMessageBox"}:
            return catalog.get("qt.button." + sourceText, "")
        return ""


def _install_qt_translation():
    app = QtCore.QCoreApplication.instance()
    if app is None or getattr(app, "_fracrtm_qt_locale", None) == current_locale():
        return
    previous = getattr(app, "_fracrtm_qt_translator", None)
    if previous is not None:
        app.removeTranslator(previous)
    translator = QtCore.QTranslator(app)
    if current_locale() == "zh_CN":
        directory = QtCore.QLibraryInfo.location(QtCore.QLibraryInfo.TranslationsPath)
        if not translator.load(str(Path(directory) / "qtbase_zh_CN.qm")):
            translator = _StandardButtonTranslator(app)
        app.installTranslator(translator)
    app._fracrtm_qt_translator = translator
    app._fracrtm_qt_locale = current_locale()


class UiLanguageController(QtCore.QObject):
    def __init__(self, root, parent=None):
        super().__init__(parent or root)
        self.root = root
        self._pending = False
        self._delegate = _MessageDelegate(self)

    def install(self):
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self.retranslate()
        return self

    def set_locale(self, locale):
        set_locale(locale)
        self.retranslate()

    def eventFilter(self, watched, event):
        # Qt may deliver events while widgets are being destroyed.
        root = getattr(self, "root", None)
        if root is None or sip.isdeleted(self) or sip.isdeleted(root) or sip.isdeleted(watched):
            return False
        if isinstance(watched, QtWidgets.QWidget) and (
            watched is root or root.isAncestorOf(watched)
        ):
            if (
                event.type() == QtCore.QEvent.Paint
                and isinstance(watched, QtWidgets.QComboBox)
                and not watched.isEditable()
                and not _verbatim(watched)
            ):
                painter = QtWidgets.QStylePainter(watched)
                option = QtWidgets.QStyleOptionComboBox()
                watched.initStyleOption(option)
                payload = watched.currentData(MESSAGE_ROLE)
                if payload:
                    option.currentText = restore_text(payload).render()
                painter.drawComplexControl(QtWidgets.QStyle.CC_ComboBox, option)
                painter.drawControl(QtWidgets.QStyle.CE_ComboBoxLabel, option)
                return True
            if event.type() == QtCore.QEvent.ToolTip:
                view = watched.parentWidget()
                if isinstance(view, QtWidgets.QAbstractItemView) and watched is view.viewport():
                    payload = view.indexAt(event.pos()).data(TOOLTIP_MESSAGE_ROLE)
                    if payload:
                        QtWidgets.QToolTip.showText(
                            event.globalPos(), restore_text(payload).render(), watched
                        )
                        return True
            if (
                event.type()
                in {QtCore.QEvent.Show, QtCore.QEvent.ChildAdded, QtCore.QEvent.LayoutRequest}
                and not self._pending
            ):
                self._pending = True
                QtCore.QTimer.singleShot(0, self._deferred)
        return super().eventFilter(watched, event)

    def _deferred(self):
        self._pending = False
        if hasattr(self, "root") and not sip.isdeleted(self):
            self.retranslate()

    def retranslate(self, root=None):
        _install_qt_translation()
        target = root or self.root
        if sip.isdeleted(target):
            return
        for widget in [target, *target.findChildren(QtWidgets.QWidget)]:
            if _verbatim(widget):
                continue
            for (method, *prefix), value in list(getattr(widget, "_fracrtm_bindings", {}).items()):
                _native(widget, method, *prefix, value.render())
            if isinstance(widget, (QtWidgets.QComboBox, QtWidgets.QListWidget)):
                widget.view().setItemDelegate(self._delegate) if isinstance(
                    widget, QtWidgets.QComboBox
                ) else widget.setItemDelegate(self._delegate)
            if isinstance(widget, QtWidgets.QAbstractItemView):
                for column in widget.property("fracrtm_label_columns") or ():
                    widget.setItemDelegateForColumn(column, self._delegate)
            if isinstance(widget, QtWidgets.QTreeWidget):
                for column in range(widget.columnCount()):
                    value = _item_message(widget.headerItem(), column)
                    if value is not None:
                        widget.headerItem().setText(column, value.render())
            if isinstance(widget, QtWidgets.QTableWidget):
                for item in [
                    *(widget.horizontalHeaderItem(i) for i in range(widget.columnCount())),
                    *(widget.verticalHeaderItem(i) for i in range(widget.rowCount())),
                ]:
                    if item is not None:
                        value = _item_message(item)
                        if value is not None:
                            item.setText(value.render())
            for action in widget.actions():
                for (method, *prefix), value in list(
                    getattr(action, "_fracrtm_bindings", {}).items()
                ):
                    _native(action, method, *prefix, value.render())
            widget.update()
        target.update()


def localize_dialog(dialog):
    parent = dialog.parentWidget()
    while parent is not None:
        controller = getattr(parent, "_language_controller", None)
        if controller is not None:
            controller.retranslate(dialog)
            return controller
        parent = parent.parentWidget()
    controller = getattr(dialog, "_language_controller", None)
    if controller is None:
        controller = UiLanguageController(dialog).install()
        dialog._language_controller = controller
    controller.retranslate(dialog)
    return controller
