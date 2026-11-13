from __future__ import annotations

from copy import deepcopy

from PyQt5 import QtCore, QtGui, QtWidgets

from fracrtm.config import pflotran as model
from fracrtm.messages import join_text, render_text
from fracrtm.messages import message as msg
from fracrtm.ui.localization import (
    format_message,
    mark_verbatim,
    set_label_text,
    tr,
    ui_call,
    ui_construct,
)

USAGES = (
    (msg("text.initial.condition"), "initial"),
    (msg("text.boundary.condition"), "boundary"),
    (msg("text.source.sink"), "source_sink"),
)


class _ReferenceDelegate(QtWidgets.QStyledItemDelegate):
    def __init__(self, provider, allow_empty=False, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.allow_empty = allow_empty

    def createEditor(self, parent, option, index):
        editor = QtWidgets.QComboBox(parent)
        if type(self) is _ReferenceDelegate:
            mark_verbatim(editor)
        if self.allow_empty:
            ui_call(editor, "addItem", "", "")
        for label, value in self.provider():
            ui_call(editor, "addItem", label, value)
        current = index.data() or ""
        if editor.findData(current) < 0 and current:
            ui_call(editor, "addItem", format_message("reference.expired", name=current), current)
        return editor

    def setEditorData(self, editor, index):
        value = index.data() or ""
        found = editor.findData(value)
        editor.setCurrentIndex(max(0, found))

    def setModelData(self, editor, data_model, index):
        data_model.setData(index, editor.currentData())


class _UsageDelegate(_ReferenceDelegate):
    def displayText(self, value, locale):
        for label, key in USAGES:
            if value == key:
                return render_text(label)
        return super().displayText(value, locale)


class SpatialAssignmentWidget(QtWidgets.QWidget):
    changed = QtCore.pyqtSignal()
    assignmentSelected = QtCore.pyqtSignal(object)
    showMaterialsRequested = QtCore.pyqtSignal()
    locateRequested = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._region_provider = lambda: []
        self._flow_provider = lambda: []
        self._transport_provider = lambda: []
        self._mapping = model.MaterialMappingSummary()
        self._build_ui()

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        host = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(host)
        assignments = ui_construct(QtWidgets.QGroupBox, msg("text.spatial.condition.assignments"))
        assignment_layout = QtWidgets.QVBoxLayout(assignments)
        self.assignment_table = QtWidgets.QTableWidget(0, 5)
        ui_call(
            self.assignment_table,
            "setHorizontalHeaderLabels",
            [
                msg("text.name"),
                msg("text.purpose"),
                msg("text.spatial.region"),
                msg("text.flow.condition"),
                msg("text.transport.condition"),
            ],
        )
        self.assignment_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.assignment_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.assignment_table.setMinimumHeight(220)
        assignment_layout.addWidget(self.assignment_table)
        actions = QtWidgets.QHBoxLayout()
        self.add_button = ui_construct(QtWidgets.QPushButton, msg("text.add"))
        self.delete_button = ui_construct(QtWidgets.QPushButton, msg("text.delete.selected"))
        actions.addWidget(self.add_button)
        actions.addWidget(self.delete_button)
        self.locate_assignment_button = ui_construct(QtWidgets.QPushButton, tr("viewer.locate"))
        self.locate_assignment_button.setObjectName("locateAssignmentButton")
        self.locate_assignment_button.clicked.connect(self.locateRequested)
        actions.addWidget(self.locate_assignment_button)
        actions.addStretch()
        assignment_layout.addLayout(actions)
        layout.addWidget(assignments)
        mapping = ui_construct(QtWidgets.QGroupBox, msg("text.spatial.material.mapping"))
        mapping_layout = QtWidgets.QVBoxLayout(mapping)
        form = QtWidgets.QFormLayout()
        self.mode_label = ui_construct(QtWidgets.QLabel, msg("text.udfm.material.id.file"))
        self.file_label = ui_construct(QtWidgets.QLabel, "—")
        self.file_label.setProperty("fracrtm_user_content", True)
        self.status_label = ui_construct(QtWidgets.QLabel, msg("text.not.read"))
        self.counts_label = ui_construct(
            QtWidgets.QLabel, msg("text.active.cells.0.mapped.cells.0.unmapped.cells.0")
        )
        ui_call(form, "addRow", msg("text.current.mode"), self.mode_label)
        ui_call(form, "addRow", msg("text.file"), self.file_label)
        ui_call(form, "addRow", msg("text.read.status"), self.status_label)
        ui_call(form, "addRow", self.counts_label)
        mapping_layout.addLayout(form)
        self.material_table = QtWidgets.QTableWidget(0, 4)
        self.material_table.setProperty("fracrtm_label_columns", [3])
        ui_call(
            self.material_table,
            "setHorizontalHeaderLabels",
            [
                msg("text.material.id"),
                msg("text.material.name"),
                msg("text.number.of.cells"),
                msg("text.property.status"),
            ],
        )
        self.material_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.material_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.material_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.material_table.setMinimumHeight(150)
        mapping_layout.addWidget(self.material_table)
        material_actions = QtWidgets.QHBoxLayout()
        self.view_material_button = ui_construct(
            QtWidgets.QPushButton, msg("text.view.material.properties")
        )
        material_actions.addWidget(self.view_material_button)
        material_actions.addStretch()
        mapping_layout.addLayout(material_actions)
        layout.addWidget(mapping)
        layout.addStretch()
        scroll.setWidget(host)
        root.addWidget(scroll)
        self.assignment_table.setItemDelegateForColumn(
            1, _UsageDelegate(lambda: list(USAGES), False, self.assignment_table)
        )
        self.assignment_table.setItemDelegateForColumn(
            2, _ReferenceDelegate(lambda: self._region_provider(), False, self.assignment_table)
        )
        self.assignment_table.setItemDelegateForColumn(
            3, _ReferenceDelegate(lambda: self._flow_provider(), True, self.assignment_table)
        )
        self.assignment_table.setItemDelegateForColumn(
            4, _ReferenceDelegate(lambda: self._transport_provider(), True, self.assignment_table)
        )
        self.add_button.clicked.connect(self._add)
        self.delete_button.clicked.connect(self._delete)
        self.assignment_table.itemChanged.connect(lambda *_: self.changed.emit())
        self.assignment_table.itemSelectionChanged.connect(self._emit_assignment)
        self.view_material_button.clicked.connect(self.showMaterialsRequested)

    def set_reference_providers(self, regions, flows, transports):
        self._region_provider = regions
        self._flow_provider = flows
        self._transport_provider = transports

    def load(self, config: model.SpatialAssignmentConfig):
        self.assignment_table.blockSignals(True)
        self.assignment_table.setRowCount(0)
        for value in config.condition_assignments:
            self._append(value)
        self.assignment_table.blockSignals(False)
        self._mapping = deepcopy(config.material_mapping or model.MaterialMappingSummary())

    def assignments(self):
        result = []
        for row in range(self.assignment_table.rowCount()):
            values = [
                self.assignment_table.item(row, column).text()
                if self.assignment_table.item(row, column)
                else ""
                for column in range(5)
            ]
            result.append(
                model.SpatialConditionAssignment(
                    values[0].strip(), values[1], values[2], values[3] or None, values[4] or None
                )
            )
        return result

    def selected_assignment(self):
        row = self.assignment_table.currentRow()
        values = self.assignments()
        return values[row] if 0 <= row < len(values) else None

    def config(self):
        return model.SpatialAssignmentConfig(self.assignments(), deepcopy(self._mapping))

    def selected_material_id(self):
        row = self.material_table.currentRow()
        return self._mapping.items[row].material_id if 0 <= row < len(self._mapping.items) else None

    def _append(self, value):
        row = self.assignment_table.rowCount()
        self.assignment_table.insertRow(row)
        for column, text in enumerate(
            (
                value.name,
                value.usage,
                value.region_name,
                value.flow_condition_name or "",
                value.transport_condition_name or "",
            )
        ):
            self.assignment_table.setItem(
                row, column, ui_construct(QtWidgets.QTableWidgetItem, str(text))
            )

    def _add(self):
        existing = {item.name for item in self.assignments()}
        index = 1
        while join_text("", ["assignment_", index]) in existing:
            index += 1
        self._append(
            model.SpatialConditionAssignment(join_text("", ["assignment_", index]), "initial")
        )
        self.assignment_table.selectRow(self.assignment_table.rowCount() - 1)
        self.changed.emit()

    def _delete(self):
        rows = sorted({item.row() for item in self.assignment_table.selectedItems()}, reverse=True)
        for row in rows:
            self.assignment_table.removeRow(row)
        if rows:
            self.changed.emit()

    def rename_reference(self, kind, old, new):
        column = {"region": 2, "FLOW": 3, "TRANSPORT": 4}.get(kind)
        if column is None:
            return
        self.assignment_table.blockSignals(True)
        for row in range(self.assignment_table.rowCount()):
            item = self.assignment_table.item(row, column)
            if item and item.text() == old:
                ui_call(item, "setText", new)
        self.assignment_table.blockSignals(False)
        self.changed.emit()

    def update_material_mapping(self, summary, material_records, material_issue_provider):
        materials = summary.materials
        definitions = {int(item["material_id"]): item for item in material_records}
        items = []
        for material_id, count in materials.material_cell_counts:
            definition = definitions.get(material_id)
            if definition is None:
                items.append(
                    model.MaterialMappingItem(
                        material_id,
                        msg("text.no.materials.defined.2"),
                        count,
                        "missing",
                        msg("text.material.properties.are.missing.on.page.4"),
                    )
                )
            else:
                issues = material_issue_provider(definition)
                items.append(
                    model.MaterialMappingItem(
                        material_id,
                        definition["name"],
                        count,
                        "incomplete" if issues else "defined",
                        join_text("\n", issues),
                    )
                )
        self._mapping = model.MaterialMappingSummary(
            "material_file",
            materials.file_path,
            materials.active_cell_count,
            materials.mapped_cell_count,
            materials.unmapped_cell_count,
            items,
            materials.status,
            materials.message,
        )
        self._refresh_mapping()

    def _refresh_mapping(self):
        m = self._mapping
        ui_call(self.file_label, "setText", m.file_path or "—")
        if m.status == "available" and (not m.unmapped_cell_count):
            text, color = (msg("text.all.active.cells.mapped"), "#247a5a")
        elif m.status == "warning":
            text, color = (msg("text.loaded.with.warnings.2"), "#b54708")
        else:
            text, color = (join_text("", ["× ", m.message or msg("text.not.loaded")]), "#b42318")
        set_label_text(self.status_label, text)
        self.status_label.setStyleSheet(join_text("", ["color:", color, ";"]))
        ui_call(self.status_label, "setToolTip", m.message)
        set_label_text(
            self.counts_label,
            format_message(
                "mapping.counts",
                active=m.active_cell_count,
                mapped=m.mapped_cell_count,
                unmapped=m.unmapped_cell_count,
            ),
        )
        self.material_table.setRowCount(0)
        for value in m.items:
            row = self.material_table.rowCount()
            self.material_table.insertRow(row)
            status = {
                "defined": msg("text.defined"),
                "incomplete": msg("text.incomplete.parameters"),
                "missing": msg("text.material.properties.missing"),
            }[value.property_status]
            for column, text in enumerate(
                (
                    value.material_id,
                    "—" if value.property_status == "missing" else value.material_name,
                    value.cell_count,
                    status,
                )
            ):
                item = ui_construct(QtWidgets.QTableWidgetItem, str(text))
                ui_call(item, "setToolTip", value.message)
                item.setForeground(
                    QtGui.QColor(
                        "#b42318"
                        if value.property_status == "missing"
                        else "#b54708"
                        if value.property_status == "incomplete"
                        else "#247a5a"
                    )
                    if column == 3
                    else QtGui.QColor()
                )
                self.material_table.setItem(row, column, item)

    def apply_validation(self, report):
        messages = {}
        for issue in report.errors:
            match = QtCore.QRegularExpression(
                "spatial_assignment\\.condition_assignments\\[(\\d+)\\]"
            ).match(issue.path)
            if match.hasMatch():
                messages.setdefault(int(match.captured(1)), []).append(issue.message)
        for row in range(self.assignment_table.rowCount()):
            problems = messages.get(row, [])
            for column in range(5):
                item = self.assignment_table.item(row, column)
                if item:
                    item.setBackground(
                        QtGui.QColor("#fdecea") if problems else QtGui.QColor("transparent")
                    )
                    ui_call(item, "setToolTip", join_text("\n", problems))

    def _emit_assignment(self):
        row = self.assignment_table.currentRow()
        values = self.assignments()
        self.assignmentSelected.emit(values[row] if 0 <= row < len(values) else None)
