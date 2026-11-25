from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets

from fracrtm.config import pflotran as model
from fracrtm.config.contracts import (
    CONSTRAINT_TYPES,
    FLOW_UNITS,
    LENGTH_TO_METRES,
    MINERAL_SURFACE_UNITS,
    RATE_DISTRIBUTIONS,
    canonical_constraint_type,
    canonical_flow_unit,
    finite_real,
)
from fracrtm.messages import exception_text, join_text
from fracrtm.messages import message as msg
from fracrtm.ui.localization import (
    VERBATIM_TEXT_ROLE,
    format_message,
    render_text,
    tr,
    ui_call,
    ui_construct,
)
from fracrtm.ui.qt_helpers import MessageBox

SYSTEM_REGIONS = ("all", "top", "bottom", "west", "east", "north", "south")
REGION_TYPES = (
    (msg("text.point.coordinate"), "COORDINATE"),
    (msg("text.coordinate.bounds.coordinates"), "COORDINATES"),
    (msg("text.cell.id.list.list"), "LIST"),
    (msg("text.external.region.file.file"), "FILE"),
)
USAGES = (
    (msg("text.initial.condition"), "initial"),
    (msg("text.boundary.condition"), "boundary"),
    (msg("text.source.sink"), "source_sink"),
)
FLOW_FORMS = {
    "initial": (
        (msg("text.fixed.liquid.pressure"), "fixed_liquid_pressure"),
        (msg("text.hydraulic.head.or.hydrostatic.pressure"), "hydrostatic_head"),
    ),
    "boundary": (
        (msg("text.fixed.liquid.pressure"), "fixed_liquid_pressure"),
        (msg("text.hydraulic.head.or.hydrostatic.pressure"), "hydrostatic_head"),
        (msg("text.boundary.darcy.flux"), "darcy_flux"),
        (msg("text.seepage.face"), "seepage"),
    ),
    "source_sink": (
        (msg("text.total.volumetric.flow.rate"), "scaled_volumetric_rate"),
        (msg("text.total.mass.flow.rate"), "scaled_mass_rate"),
    ),
}
TRANSPORT_FORMS = {
    "initial": ((msg("text.initial.solution.composition"), "initial_solution"),),
    "boundary": (
        (msg("text.inflow.solution.composition"), "inflow_solution"),
        (msg("text.fixed.solution.composition"), "fixed_solution"),
        (msg("text.free.outflow.zero.gradient"), "zero_gradient_outflow"),
    ),
    "source_sink": ((msg("text.injected.solution.composition"), "injection_solution"),),
}


class RegionConditionsWidget(QtWidgets.QWidget):
    changed = QtCore.pyqtSignal()
    referenceRenamed = QtCore.pyqtSignal(str, str, str)
    locateRequested = QtCore.pyqtSignal()

    def __init__(self, project_directory: Path | None = None, parent=None):
        super().__init__(parent)
        self.project_directory = Path(project_directory) if project_directory else None
        from fracrtm.project.storage import ResourceImports

        self.imports = getattr(parent, "_resource_imports", None) or ResourceImports(
            self.project_directory
        )
        self._regions: list[model.RegionConfig] = []
        self._flow: list[model.FlowConditionConfig] = []
        self._transport: list[model.TransportConditionConfig] = []
        self._constraints: list[model.ChemicalConstraintConfig] = []
        self._primary_species: list[str] = []
        self._passive_gases: list[str] = []
        self._minerals: list[str] = []
        self.confirm_region_delete = None
        self.confirm_condition_delete = None
        self._loading = False
        self._build_ui()

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.section_stack = QtWidgets.QStackedWidget()
        self.region_group = self._build_regions()
        self.condition_group = self._build_conditions()
        self.section_stack.addWidget(self.region_group)
        self.section_stack.addWidget(self.condition_group)
        root.addWidget(self.section_stack)

    def set_section(self, section: str) -> None:
        self.section_stack.setCurrentIndex(0 if section == "region" else 1)

    def _build_regions(self):
        group = ui_construct(QtWidgets.QGroupBox, msg("text.regions"))
        layout = QtWidgets.QVBoxLayout(group)
        self.region_table = QtWidgets.QTableWidget(0, 5)
        self.region_table.setProperty("fracrtm_label_columns", [2, 3])
        ui_call(
            self.region_table,
            "setHorizontalHeaderLabels",
            [
                msg("text.name"),
                msg("text.pflotran.type"),
                msg("text.definition.summary"),
                msg("text.source"),
                msg("text.status"),
            ],
        )
        self.region_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.region_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.region_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.region_table.setMinimumHeight(135)
        layout.addWidget(self.region_table)
        editor = ui_construct(QtWidgets.QGroupBox, msg("text.region.editor"))
        form = QtWidgets.QFormLayout(editor)
        self.region_name = ui_construct(QtWidgets.QLineEdit)
        self.region_type = QtWidgets.QComboBox()
        for label, value in REGION_TYPES:
            ui_call(self.region_type, "addItem", label, value)
        ui_call(form, "addRow", msg("text.name"), self.region_name)
        ui_call(form, "addRow", msg("text.pflotran.type"), self.region_type)
        self.region_stack = QtWidgets.QStackedWidget()
        ui_call(form, "addRow", self.region_stack)
        point = QtWidgets.QWidget()
        pf = QtWidgets.QFormLayout(point)
        point_row, self.point_values = self._coordinate_row(3)
        ui_call(pf, "addRow", "X / Y / Z", point_row)
        self.point_unit = self._unit_combo(("m", "cm", "km"))
        ui_call(pf, "addRow", msg("text.coordinate.unit"), self.point_unit)
        self.region_stack.addWidget(point)
        bounds = QtWidgets.QWidget()
        bf = QtWidgets.QFormLayout(bounds)
        bounds_row, self.bounds_values = self._coordinate_row(6)
        ui_call(bf, "addRow", "Xmin / Ymin / Zmin / Xmax / Ymax / Zmax", bounds_row)
        self.bounds_unit = self._unit_combo(("m", "cm", "km"))
        ui_call(bf, "addRow", msg("text.coordinate.unit"), self.bounds_unit)
        self.region_stack.addWidget(bounds)
        cells = QtWidgets.QWidget()
        cf = QtWidgets.QVBoxLayout(cells)
        self.cell_summary = ui_construct(QtWidgets.QLabel, msg("text.no.cell.ids.defined"))
        cf.addWidget(self.cell_summary)
        cell_actions = QtWidgets.QHBoxLayout()
        self.edit_cells = ui_construct(QtWidgets.QPushButton, msg("text.edit.cell.ids"))
        self.import_cells = ui_construct(QtWidgets.QPushButton, msg("text.import.file"))
        cell_actions.addWidget(self.edit_cells)
        cell_actions.addWidget(self.import_cells)
        cell_actions.addStretch()
        cf.addLayout(cell_actions)
        self.region_stack.addWidget(cells)
        file_page = QtWidgets.QWidget()
        ff = QtWidgets.QFormLayout(file_page)
        self.file_kind = QtWidgets.QComboBox()
        ui_call(self.file_kind, "addItem", msg("text.cell.region"), "cell_region")
        ui_call(self.file_kind, "addItem", msg("text.boundary.face"), "boundary_surface")
        self.file_path = ui_construct(QtWidgets.QLineEdit)
        self.file_path.setProperty("fracrtm_user_content", True)
        self.file_path.setReadOnly(True)
        file_row = QtWidgets.QWidget()
        file_box = QtWidgets.QHBoxLayout(file_row)
        file_box.setContentsMargins(0, 0, 0, 0)
        file_box.addWidget(self.file_path)
        self.choose_file = ui_construct(QtWidgets.QPushButton, msg("text.select.file"))
        file_box.addWidget(self.choose_file)
        ui_call(ff, "addRow", msg("text.region.definition"), self.file_kind)
        ui_call(ff, "addRow", msg("text.file.path"), file_row)
        self.region_stack.addWidget(file_page)
        layout.addWidget(editor)
        actions = QtWidgets.QHBoxLayout()
        self.add_region_button = ui_construct(QtWidgets.QPushButton, msg("text.add.region"))
        self.delete_region_button = ui_construct(QtWidgets.QPushButton, msg("text.delete.selected"))
        actions.addWidget(self.add_region_button)
        actions.addWidget(self.delete_region_button)
        self.locate_region_button = ui_construct(QtWidgets.QPushButton, tr("viewer.locate"))
        self.locate_region_button.setObjectName("locateRegionButton")
        self.locate_region_button.clicked.connect(self.locateRequested)
        actions.addWidget(self.locate_region_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.region_table.currentCellChanged.connect(self._select_region)
        self.region_type.currentIndexChanged.connect(self._region_type_changed)
        self.region_name.editingFinished.connect(self._save_region_editor)
        for widget in (*self.point_values, *self.bounds_values):
            widget.editingFinished.connect(self._save_region_editor)
        self.point_unit.currentIndexChanged.connect(self._save_region_editor)
        self.bounds_unit.currentIndexChanged.connect(self._save_region_editor)
        self.file_kind.currentIndexChanged.connect(self._save_region_editor)
        self.add_region_button.clicked.connect(self._add_region)
        self.delete_region_button.clicked.connect(self._delete_region)
        self.edit_cells.clicked.connect(self._edit_cell_ids)
        self.import_cells.clicked.connect(self._import_cell_ids)
        self.choose_file.clicked.connect(self._choose_region_file)
        return group

    def _build_conditions(self):
        group = ui_construct(QtWidgets.QGroupBox, msg("text.conditions"))
        root = QtWidgets.QVBoxLayout(group)
        note = ui_construct(
            QtWidgets.QLabel,
            msg(
                "text.a.condition.defines.only.a.physical.state.assign.its.region.on.the.spatial.assignment"
            ),
        )
        note.setObjectName("PageDescription")
        root.addWidget(note)
        filters = QtWidgets.QHBoxLayout()
        self.process_filter = QtWidgets.QComboBox()
        ui_call(
            self.process_filter,
            "addItems",
            [msg("text.all.processes"), msg("section.flow_model"), msg("section.transport_model")],
        )
        self.usage_filter = QtWidgets.QComboBox()
        ui_call(self.usage_filter, "addItem", msg("text.all.usages"), "")
        for label, value in USAGES:
            ui_call(self.usage_filter, "addItem", label, value)
        filters.addWidget(ui_construct(QtWidgets.QLabel, msg("text.process.filter")))
        filters.addWidget(self.process_filter)
        filters.addWidget(ui_construct(QtWidgets.QLabel, msg("text.purpose.filter")))
        filters.addWidget(self.usage_filter)
        filters.addStretch()
        root.addLayout(filters)
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        left = QtWidgets.QWidget()
        ll = QtWidgets.QVBoxLayout(left)
        self.condition_table = QtWidgets.QTableWidget(0, 5)
        self.condition_table.setProperty("fracrtm_label_columns", [2, 3])
        ui_call(
            self.condition_table,
            "setHorizontalHeaderLabels",
            [
                msg("text.name"),
                msg("text.process"),
                msg("text.purpose"),
                msg("text.condition.form"),
                msg("text.status"),
            ],
        )
        self.condition_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.condition_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.condition_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.condition_table.setMinimumHeight(180)
        ll.addWidget(self.condition_table)
        ca = QtWidgets.QHBoxLayout()
        self.add_condition_button = ui_construct(QtWidgets.QPushButton, msg("text.add.condition"))
        self.copy_condition_button = ui_construct(QtWidgets.QPushButton, msg("text.duplicate"))
        self.delete_condition_button = ui_construct(QtWidgets.QPushButton, msg("text.delete"))
        ca.addWidget(self.add_condition_button)
        ca.addWidget(self.copy_condition_button)
        ca.addWidget(self.delete_condition_button)
        ca.addStretch()
        ll.addLayout(ca)
        split.addWidget(left)
        detail_scroll = QtWidgets.QScrollArea()
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        detail_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        detail_scroll.setMinimumWidth(430)
        detail = ui_construct(QtWidgets.QGroupBox, msg("text.condition.details"))
        df = QtWidgets.QFormLayout(detail)
        df.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        df.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        self.condition_name = ui_construct(QtWidgets.QLineEdit)
        self.condition_process = QtWidgets.QComboBox()
        ui_call(self.condition_process, "addItem", msg("section.flow_model"), "FLOW")
        ui_call(self.condition_process, "addItem", msg("section.transport_model"), "TRANSPORT")
        self.condition_usage = QtWidgets.QComboBox()
        for label, value in USAGES:
            ui_call(self.condition_usage, "addItem", label, value)
        self.condition_form = QtWidgets.QComboBox()
        ui_call(df, "addRow", msg("text.name"), self.condition_name)
        ui_call(df, "addRow", msg("text.process"), self.condition_process)
        ui_call(df, "addRow", msg("text.purpose"), self.condition_usage)
        ui_call(df, "addRow", msg("text.condition.form"), self.condition_form)
        self.condition_stack = QtWidgets.QStackedWidget()
        ui_call(df, "addRow", self.condition_stack)
        self.flow_parameters = QtWidgets.QWidget()
        fp = QtWidgets.QFormLayout(self.flow_parameters)
        fp.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        fp.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        self.parameter_value = ui_construct(QtWidgets.QLineEdit)
        self.parameter_value.setValidator(QtGui.QDoubleValidator())
        self.parameter_unit = QtWidgets.QComboBox()
        ui_call(
            self.parameter_unit, "addItems", ["Pa", "m", "m/s", "m/day", "m³/s", "m³/day", "kg/s"]
        )
        self.head_elevation = ui_construct(QtWidgets.QLineEdit, "0")
        self.head_elevation.setValidator(QtGui.QDoubleValidator())
        self.length_unit = self._unit_combo(tuple(LENGTH_TO_METRES))
        self.reference_pressure = ui_construct(QtWidgets.QLineEdit, "101325")
        self.reference_pressure.setValidator(QtGui.QDoubleValidator())
        self.distribution = QtWidgets.QComboBox()
        ui_call(self.distribution, "addItems", sorted(RATE_DISTRIBUTIONS))
        self.flow_note = ui_construct(QtWidgets.QLabel)
        self.flow_note.setWordWrap(True)
        ui_call(fp, "addRow", msg("input.numeric_value"), self.parameter_value)
        ui_call(fp, "addRow", msg("text.unit"), self.parameter_unit)
        ui_call(fp, "addRow", msg("repair.datum_elevation"), self.head_elevation)
        ui_call(fp, "addRow", msg("text.length.unit"), self.length_unit)
        ui_call(fp, "addRow", msg("repair.datum_pressure"), self.reference_pressure)
        ui_call(fp, "addRow", msg("region.assignment_method"), self.distribution)
        ui_call(fp, "addRow", self.flow_note)
        self.condition_stack.addWidget(self.flow_parameters)
        self.solution_page = QtWidgets.QWidget()
        sp = QtWidgets.QVBoxLayout(self.solution_page)
        constraint_row = QtWidgets.QHBoxLayout()
        constraint_row.addWidget(
            ui_construct(QtWidgets.QLabel, msg("text.referenced.geochemical.constraint"))
        )
        self.constraint_selector = QtWidgets.QComboBox()
        constraint_row.addWidget(self.constraint_selector, 1)
        self.add_constraint_button = ui_construct(QtWidgets.QPushButton, msg("text.new.constraint"))
        constraint_row.addWidget(self.add_constraint_button)
        sp.addLayout(constraint_row)
        self.solution_table = QtWidgets.QTableWidget(0, 5)
        ui_call(
            self.solution_table,
            "setHorizontalHeaderLabels",
            [
                msg("text.species"),
                msg("input.numeric_value"),
                msg("text.constraint.type"),
                msg("text.associated.object"),
                msg("text.unit"),
            ],
        )
        self.solution_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        sp.addWidget(self.solution_table)
        sp.addWidget(
            ui_construct(
                QtWidgets.QLabel,
                msg("text.initial.minerals.volume.fraction.and.initial.reactive.surface.area"),
            )
        )
        self.initial_minerals_table = QtWidgets.QTableWidget(0, 5)
        ui_call(
            self.initial_minerals_table,
            "setHorizontalHeaderLabels",
            [
                msg("text.use"),
                msg("text.mineral"),
                msg("text.volume.fraction"),
                msg("text.reactive.surface.area"),
                msg("text.unit"),
            ],
        )
        self.initial_minerals_table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.Stretch
        )
        sp.addWidget(self.initial_minerals_table)
        self.reference_solution = QtWidgets.QComboBox()
        sp.addWidget(
            ui_construct(
                QtWidgets.QLabel,
                msg("text.reference.condition.for.zero.gradient.outflow.legacy.compatible"),
            )
        )
        sp.addWidget(self.reference_solution)
        self.condition_stack.addWidget(self.solution_page)
        self._mapping_label = ui_construct(QtWidgets.QLabel)
        ui_call(df, "addRow", msg("text.pflotran.mapping.summary"), self._mapping_label)
        detail_scroll.setWidget(detail)
        split.addWidget(detail_scroll)
        split.setChildrenCollapsible(False)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        split.setSizes([650, 550])
        root.addWidget(split)
        self.condition_table.currentCellChanged.connect(self._select_condition)
        self.condition_name.editingFinished.connect(self._save_condition_editor)
        self.condition_process.currentIndexChanged.connect(self._condition_scope_changed)
        self.condition_usage.currentIndexChanged.connect(self._condition_scope_changed)
        self.condition_form.currentIndexChanged.connect(self._condition_form_changed)
        self.parameter_value.editingFinished.connect(self._save_condition_editor)
        self.parameter_unit.currentIndexChanged.connect(self._save_condition_editor)
        self.head_elevation.editingFinished.connect(self._save_condition_editor)
        self.length_unit.currentIndexChanged.connect(self._save_condition_editor)
        self.reference_pressure.editingFinished.connect(self._save_condition_editor)
        self.distribution.currentIndexChanged.connect(self._save_condition_editor)
        self.reference_solution.currentIndexChanged.connect(self._save_condition_editor)
        self.solution_table.itemChanged.connect(self._save_solution_table)
        self.initial_minerals_table.itemChanged.connect(self._save_solution_table)
        self.constraint_selector.currentIndexChanged.connect(self._constraint_selection_changed)
        self.add_constraint_button.clicked.connect(self._add_constraint)
        self.add_condition_button.clicked.connect(self._add_condition)
        self.copy_condition_button.clicked.connect(self._copy_condition)
        self.delete_condition_button.clicked.connect(self._delete_condition)
        self.process_filter.currentIndexChanged.connect(self._refresh_conditions)
        self.usage_filter.currentIndexChanged.connect(self._refresh_conditions)
        return group

    @staticmethod
    def _coordinate_row(count):
        widgets = []
        host = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        for _ in range(count):
            edit = ui_construct(QtWidgets.QLineEdit, "0")
            edit.setValidator(QtGui.QDoubleValidator())
            edit.setMaximumWidth(82)
            widgets.append(edit)
            layout.addWidget(edit)
        return (host, widgets)

    @staticmethod
    def _unit_combo(values):
        combo = QtWidgets.QComboBox()
        ui_call(combo, "addItems", values)
        return combo

    def load(
        self,
        regions,
        flow,
        transport,
        primary_species,
        constraints=(),
        passive_gases=(),
        minerals=(),
    ):
        self._supplied_region_names = {item.name for item in regions}
        self._regions = deepcopy(regions)
        self._flow = deepcopy(flow)
        self._transport = deepcopy(transport)
        self._primary_species = list(primary_species)
        self._constraints = deepcopy(list(constraints))
        self._passive_gases = list(passive_gases)
        self._minerals = list(minerals)
        self._migrate_embedded_constraints()
        self._ensure_system_regions()
        self._refresh_regions()
        self._refresh_conditions()

    def regions(self):
        return deepcopy(self._regions)

    def selected_region(self):
        row = self.region_table.currentRow()
        return deepcopy(self._regions[row]) if 0 <= row < len(self._regions) else None

    def flow_conditions(self):
        return deepcopy(self._flow)

    def transport_conditions(self):
        return deepcopy(self._transport)

    def chemical_constraints(self):
        return deepcopy(self._constraints)

    def region_names(self):
        return [x.name for x in self._regions]

    def flow_names(self):
        return [x.name for x in self._flow]

    def transport_names(self):
        return [x.name for x in self._transport]

    def update_chemistry_catalogs(self, primary_species, passive_gases, minerals):
        self._primary_species = list(primary_species)
        self._passive_gases = list(passive_gases)
        self._minerals = list(minerals)
        selected = self._condition_at_row()
        if selected and selected[0] == "TRANSPORT":
            self._load_solution_table(selected[1])

    def apply_validation(self, report):
        region_messages = {}
        condition_messages = {}
        for issue in report.errors:
            match = re.match("regions\\[(\\d+)\\]", issue.path)
            if match:
                region_messages.setdefault(int(match.group(1)), []).append(issue.message)
            match = re.match("(?:flow_conditions|transport_conditions)\\[(\\d+)\\]", issue.path)
            if match:
                collection = "FLOW" if issue.path.startswith("flow") else "TRANSPORT"
                condition_messages.setdefault((collection, int(match.group(1))), []).append(
                    issue.message
                )
        for row, region in enumerate(self._regions):
            messages = region_messages.get(row, [])
            status = self.region_table.item(row, 4)
            if status:
                ui_call(status, "setText", msg("status.error_badge") if messages else "✓")
                ui_call(status, "setToolTip", join_text("\n", messages))
                status.setForeground(QtGui.QColor("#b42318" if messages else "#247a5a"))
        for row in range(self.condition_table.rowCount()):
            selected = self._condition_at_visible_row(row)
            if not selected:
                continue
            process, item = selected
            source = self._flow if process == "FLOW" else self._transport
            messages = condition_messages.get((process, source.index(item)), [])
            status = self.condition_table.item(row, 4)
            ui_call(status, "setText", msg("status.error_badge") if messages else "✓")
            ui_call(status, "setToolTip", join_text("\n", messages))
            status.setForeground(QtGui.QColor("#b42318" if messages else "#247a5a"))

    def _condition_at_visible_row(self, row):
        header = self.condition_table.verticalHeaderItem(row)
        if not header:
            return None
        process, name = header.text().split(":", 1)
        values = self._flow if process == "FLOW" else self._transport
        item = next((x for x in values if x.name == name), None)
        return (process, item) if item else None

    def sync_spatial(self, summary):
        bounds = getattr(summary.mesh, "coordinate_bounds", ())
        all_region = next((x for x in self._regions if x.name == "all"))
        if len(bounds) == 3 and "all" not in self._supplied_region_names:
            all_region.bounds = tuple((pair[0] for pair in bounds)) + tuple(
                (pair[1] for pair in bounds)
            )
        by_name = {item.internal_name: item for item in summary.boundaries}
        for name in SYSTEM_REGIONS[1:]:
            region = next((x for x in self._regions if x.name == name))
            found = by_name.get(name)
            if name in self._supplied_region_names:
                continue
            region.file_path = found.file_path if found else ""
            region.status = (
                "valid" if found and found.status in {"available", "warning"} else "invalid"
            )
            region.message = "" if region.status == "valid" else msg("text.boundary.file.not.found")
        self._refresh_regions()

    def _ensure_system_regions(self):
        existing = {x.name for x in self._regions}
        if "all" not in existing:
            self._regions.insert(
                0, model.RegionConfig("all", "COORDINATES", "system", bounds=(0, 0, 0, 0, 0, 0))
            )
        for name in SYSTEM_REGIONS[1:]:
            if name not in existing:
                self._regions.append(
                    model.RegionConfig(
                        name,
                        "FILE",
                        "auto",
                        status="invalid",
                        file_region_kind="boundary_surface",
                        message=msg("text.boundary.file.not.found"),
                    )
                )

    def _refresh_regions(self):
        current = self.region_table.currentRow()
        self.region_table.setRowCount(0)
        labels = {
            "system": msg("text.system"),
            "auto": msg("text.detected.automatically"),
            "user": msg("text.user.defined"),
        }
        for region in self._regions:
            row = self.region_table.rowCount()
            self.region_table.insertRow(row)
            summary = self._region_summary(region)
            values = [
                region.name,
                region.region_type,
                summary,
                labels.get(region.source, region.source),
                "✓" if region.status == "valid" else "×",
            ]
            for column, value in enumerate(values):
                item = ui_construct(QtWidgets.QTableWidgetItem, str(value))
                if column == 2 and region.region_type == "FILE" and region.file_path:
                    item.setData(VERBATIM_TEXT_ROLE, True)
                ui_call(item, "setToolTip", region.message)
                self.region_table.setItem(row, column, item)
        if self._regions:
            self.region_table.selectRow(max(0, min(current, len(self._regions) - 1)))

    @staticmethod
    def _region_summary(region):
        if region.region_type == "COORDINATE":
            return join_text(" ", map(str, region.coordinate or ()))
        if region.region_type == "COORDINATES":
            return (
                msg("text.model.domain.bounds")
                if region.name == "all"
                else join_text(" ", map(str, region.bounds or ()))
            )
        if region.region_type == "LIST":
            return format_message("mesh.cells", count=len(region.cell_ids))
        return region.file_path or msg("text.boundary.file.not.found")

    def _select_region(self, row, *_):
        if row < 0 or row >= len(self._regions):
            return
        self._loading = True
        r = self._regions[row]
        ui_call(self.region_name, "setText", r.name)
        self._set_combo(self.region_type, r.region_type)
        self.region_stack.setCurrentIndex(self.region_type.currentIndex())
        values = r.coordinate or (0, 0, 0)
        for w, v in zip(self.point_values, values):
            ui_call(w, "setText", str(v))
        values = r.bounds or (0, 0, 0, 0, 0, 0)
        for w, v in zip(self.bounds_values, values):
            ui_call(w, "setText", str(v))
        self.point_unit.setCurrentText(r.coordinate_unit)
        self.bounds_unit.setCurrentText(r.coordinate_unit)
        ui_call(
            self.cell_summary,
            "setText",
            format_message("mesh.cells", count=len(r.cell_ids))
            if r.cell_ids
            else msg("text.no.cell.ids.defined"),
        )
        ui_call(self.file_path, "setText", r.file_path)
        self._set_combo(self.file_kind, r.file_region_kind or "cell_region")
        locked = r.name in SYSTEM_REGIONS
        self.region_name.setReadOnly(locked)
        self.region_type.setEnabled(not locked)
        self.delete_region_button.setEnabled(not locked)
        self.choose_file.setEnabled(not locked)
        self._loading = False

    def _region_type_changed(self):
        self.region_stack.setCurrentIndex(self.region_type.currentIndex())
        self._save_region_editor()

    def _save_region_editor(self):
        if self._loading:
            return
        row = self.region_table.currentRow()
        if row < 0 or row >= len(self._regions):
            return
        r = self._regions[row]
        old_name = r.name
        if r.name not in SYSTEM_REGIONS:
            r.name = self.region_name.text().strip()
            r.region_type = self.region_type.currentData()
        try:
            if r.region_type == "COORDINATE":
                r.coordinate = tuple((float(w.text()) for w in self.point_values))
                r.coordinate_unit = self.point_unit.currentText()
            elif r.region_type == "COORDINATES":
                r.bounds = tuple((float(w.text()) for w in self.bounds_values))
                r.coordinate_unit = self.bounds_unit.currentText()
            if r.region_type in {"COORDINATE", "COORDINATES"}:
                r.status = "valid"
                r.message = ""
        except ValueError:
            if r.region_type == "COORDINATE":
                r.coordinate = None
            elif r.region_type == "COORDINATES":
                r.bounds = None
            r.status = "invalid"
            r.message = msg("validation.coordinates.must.be.valid.numbers")
        r.file_region_kind = self.file_kind.currentData()
        self._refresh_regions()
        self.changed.emit()
        if old_name != r.name:
            self.referenceRenamed.emit("region", old_name, r.name)

    def _add_region(self):
        name = self._unique_name("region", set(self.region_names()))
        self._regions.append(
            model.RegionConfig(name, "COORDINATES", "user", bounds=(0, 0, 0, 0, 0, 0))
        )
        self._refresh_regions()
        self.region_table.selectRow(len(self._regions) - 1)
        self.changed.emit()

    def _delete_region(self):
        row = self.region_table.currentRow()
        if row < 0 or self._regions[row].name in SYSTEM_REGIONS:
            return
        if self.confirm_region_delete and (not self.confirm_region_delete(self._regions[row].name)):
            return
        del self._regions[row]
        self._refresh_regions()
        self.changed.emit()

    def _edit_cell_ids(self):
        row = self.region_table.currentRow()
        if row < 0:
            return
        text, ok = QtWidgets.QInputDialog.getMultiLineText(
            self,
            render_text(msg("text.edit.cell.ids.2")),
            render_text(msg("text.separate.ids.with.spaces.commas.or.new.lines")),
            join_text("\n", map(str, self._regions[row].cell_ids)),
        )
        if ok:
            try:
                values = [int(x) for x in re.split("[\\s,;]+", text.strip()) if x]
                self._regions[row].cell_ids = sorted(set(values))
                self._refresh_regions()
                self.changed.emit()
            except ValueError:
                MessageBox.warning(
                    self,
                    msg("validation.invalid.id"),
                    msg("validation.cell.ids.must.be.positive.integers"),
                )

    def _import_cell_ids(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            render_text(msg("text.import.cell.ids")),
            "",
            render_text(msg("text.text.files.txt.dat.all.files")),
            options=QtWidgets.QFileDialog.DontUseNativeDialog,
        )
        if not path:
            return
        try:
            values = [
                int(x)
                for x in re.split("[\\s,;]+", Path(path).read_text(encoding="utf-8").strip())
                if x
            ]
        except (OSError, ValueError) as exc:
            MessageBox.warning(self, msg("text.import.failed"), exception_text(exc))
            return
        row = self.region_table.currentRow()
        self._regions[row].cell_ids = sorted(set(values))
        self._refresh_regions()
        self.changed.emit()

    def _choose_region_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            render_text(msg("text.choose.region.file")),
            "",
            render_text(msg("text.region.files.ex.zone.txt.dat.all.files")),
            options=QtWidgets.QFileDialog.DontUseNativeDialog,
        )
        if not path:
            return
        source = Path(path)
        relative = source.as_posix()
        if self.project_directory:
            relative = self.imports.stage(source, "regions")
        row = self.region_table.currentRow()
        self._regions[row].file_path = relative
        self._regions[row].status = "valid"
        ui_call(self.file_path, "setText", relative)
        self._refresh_regions()
        self.changed.emit()

    def _refresh_conditions(self, *, selected=None):
        selected = selected or self._condition_at_row()
        blocker = QtCore.QSignalBlocker(self.condition_table)
        self.condition_table.setRowCount(0)
        process_filter = self.process_filter.currentIndex()
        usage_filter = self.usage_filter.currentData()
        for process, item in [("FLOW", x) for x in self._flow] + [
            ("TRANSPORT", x) for x in self._transport
        ]:
            if (
                process_filter == 1
                and process != "FLOW"
                or (process_filter == 2 and process != "TRANSPORT")
                or (usage_filter and item.usage != usage_filter)
            ):
                continue
            row = self.condition_table.rowCount()
            self.condition_table.insertRow(row)
            self.condition_table.setVerticalHeaderItem(
                row,
                ui_construct(QtWidgets.QTableWidgetItem, join_text("", [process, ":", item.name])),
            )
            values = [
                item.name,
                process,
                next((label for label, key in USAGES if key == item.usage), item.usage),
                self._form_label(process, item.form),
                "✓",
            ]
            for column, value in enumerate(values):
                self.condition_table.setItem(
                    row, column, ui_construct(QtWidgets.QTableWidgetItem, str(value))
                )
            if selected and selected[0] == process and (selected[1].name == item.name):
                self.condition_table.selectRow(row)
        del blocker
        self._refresh_reference_solutions()
        self._select_condition()

    def _condition_at_row(self):
        row = self.condition_table.currentRow()
        if row < 0:
            return None
        header = self.condition_table.verticalHeaderItem(row)
        if not header:
            return None
        process, name = header.text().split(":", 1)
        values = self._flow if process == "FLOW" else self._transport
        item = next((x for x in values if x.name == name), None)
        return (process, item) if item is not None else None

    def _select_condition(self, *_):
        selected = self._condition_at_row()
        if not selected or selected[1] is None:
            return
        process, item = selected
        self._loading = True
        ui_call(self.condition_name, "setText", item.name)
        self._set_combo(self.condition_process, process)
        self._set_combo(self.condition_usage, item.usage)
        self._populate_forms(process, item.usage, item.form)
        self.condition_stack.setCurrentIndex(0 if process == "FLOW" else 1)
        if process == "FLOW":
            p = item.parameters
            ui_call(
                self.parameter_value, "setText", "" if p.get("value") is None else str(p["value"])
            )
            self.parameter_unit.blockSignals(True)
            self.parameter_unit.clear()
            self.parameter_unit.addItems(FLOW_UNITS.get(item.form, ()))
            loaded_unit = canonical_flow_unit(p.get("unit", "Pa"))
            if self.parameter_unit.findText(loaded_unit) < 0:
                self.parameter_unit.addItem(str(loaded_unit))
            self.parameter_unit.setCurrentText(str(loaded_unit))
            self.parameter_unit.blockSignals(False)
            ui_call(
                self.head_elevation,
                "setText",
                str(
                    p["datum"][2]
                    if isinstance(p.get("datum"), (list, tuple)) and len(p["datum"]) == 3
                    else p.get("reference_z", p.get("head_elevation", 0))
                ),
            )
            self.length_unit.setCurrentText(str(p.get("length_unit", "m")))
            self._loaded_length_unit = p.get("length_unit", "m")
            ui_call(
                self.reference_pressure,
                "setText",
                str(p.get("reference_pressure", p.get("value", 101325))),
            )
            self.distribution.setCurrentText(str(p.get("distribution", "VOLUME")))
            self._update_flow_visibility(item.form)
        else:
            self._refresh_constraint_selector(item.constraint_id)
            self._load_solution_table(item)
            self.reference_solution.setCurrentText(item.reference_solution)
        ui_call(self._mapping_label, "setText", self._mapping_summary(process, item.form))
        self._loading = False

    def _condition_scope_changed(self):
        if self._loading:
            return
        selected = self._condition_at_row()
        if not selected:
            return
        process = self.condition_process.currentData()
        usage = self.condition_usage.currentData()
        current = selected[1].form
        self._populate_forms(process, usage, current)
        options = FLOW_FORMS.get(usage, ()) if process == "FLOW" else TRANSPORT_FORMS.get(usage, ())
        if current not in {code for _, code in options}:
            blocker = QtCore.QSignalBlocker(self.condition_form)
            self.condition_form.setCurrentIndex(-1)
            del blocker
        ui_call(
            self._mapping_label,
            "setText",
            msg("text.select.a.condition.type.compatible.with.the.new.process.and.usage"),
        )
        self._save_condition_editor()

    def _populate_forms(self, process, usage, current):
        self.condition_form.blockSignals(True)
        self.condition_form.clear()
        options = FLOW_FORMS[usage] if process == "FLOW" else TRANSPORT_FORMS[usage]
        for label, value in options:
            ui_call(self.condition_form, "addItem", label, value)
        self._set_combo(self.condition_form, current)
        self.condition_form.blockSignals(False)

    def _condition_form_changed(self):
        if self._loading:
            return
        process = self.condition_process.currentData()
        form = self.condition_form.currentData()
        self.condition_stack.setCurrentIndex(0 if process == "FLOW" else 1)
        if process == "FLOW":
            previous = self.parameter_unit.currentText()
            self.parameter_unit.blockSignals(True)
            self.parameter_unit.clear()
            self.parameter_unit.addItems(FLOW_UNITS.get(form, ()))
            if previous in FLOW_UNITS.get(form, ()):
                self.parameter_unit.setCurrentText(previous)
            self.parameter_unit.blockSignals(False)
        self._update_flow_visibility(form)
        ui_call(self._mapping_label, "setText", self._mapping_summary(process, form))
        self._save_condition_editor()

    def _update_flow_visibility(self, form):
        hydro = form == "hydrostatic_head"
        rate = form in {"scaled_volumetric_rate", "scaled_mass_rate"}
        seepage = form == "seepage"
        self.parameter_value.setVisible(not (hydro or seepage))
        self.parameter_unit.setVisible(True)
        self.head_elevation.setVisible(hydro or seepage)
        self.length_unit.setVisible(hydro or seepage)
        self.reference_pressure.setVisible(hydro or seepage)
        self.distribution.setVisible(rate)
        ui_call(
            self.flow_note,
            "setText",
            msg(
                "text.positive.values.indicate.inflow.injection.negative.values.indicate.outflow.extraction"
            )
            if form in {"darcy_flux", "scaled_volumetric_rate", "scaled_mass_rate"}
            else "",
        )

    def _save_condition_editor(self):
        if self._loading:
            return
        selected = self._condition_at_row()
        if not selected:
            return
        old_process, item = selected
        old_name = item.name
        new_process = self.condition_process.currentData()
        item.name = self.condition_name.text().strip()
        item.usage = self.condition_usage.currentData()
        form = self.condition_form.currentData()
        item.form = form or ""
        if old_process == "FLOW":
            p = item.parameters
            p["unit"] = self.parameter_unit.currentText()
            p["value"] = self._float_or_none(self.parameter_value.text())
            p["head_elevation"] = self._float_or_none(self.head_elevation.text())
            new_length_unit = self.length_unit.currentText()
            old_length_unit = getattr(self, "_loaded_length_unit", p.get("length_unit", "m"))
            if old_length_unit in LENGTH_TO_METRES and new_length_unit in LENGTH_TO_METRES:
                scale = LENGTH_TO_METRES[old_length_unit] / LENGTH_TO_METRES[new_length_unit]
                for key in ("reference_x", "reference_y"):
                    if finite_real(p.get(key)):
                        p[key] *= scale
                if isinstance(p.get("datum"), (list, tuple)) and len(p["datum"]) == 3:
                    p["datum"] = [p["datum"][0] * scale, p["datum"][1] * scale, p["head_elevation"]]
                if "reference_z" in p:
                    p["reference_z"] = p["head_elevation"]
            p["length_unit"] = new_length_unit
            self._loaded_length_unit = new_length_unit
            p["reference_pressure"] = self._float_or_none(self.reference_pressure.text())
            if item.form in {"hydrostatic_head", "seepage"}:
                p["value"] = p["reference_pressure"]
            p["distribution"] = self.distribution.currentText()
        else:
            item.reference_solution = self.reference_solution.currentText()
            item.constraint_id = self.constraint_selector.currentData() or ""
        if new_process != old_process:
            if old_process == "FLOW":
                self._flow.remove(item)
                constraint = self._new_constraint(item.name + "_chemistry")
                item = model.TransportConditionConfig(
                    item.name, item.usage, form or "", constraint_id=constraint.id
                )
                self._transport.append(item)
            else:
                self._transport.remove(item)
                item = model.FlowConditionConfig(item.name, item.usage, form or "", {})
                self._flow.append(item)
        self._refresh_conditions(selected=(new_process, item))
        self.changed.emit()
        if old_name != item.name:
            self.referenceRenamed.emit(new_process, old_name, item.name)

    def _load_solution_table(self, item):
        constraint = self._constraint_by_id(item.constraint_id)
        self.solution_table.blockSignals(True)
        self.solution_table.setRowCount(0)
        by_name = {x.species: x for x in (constraint.concentrations if constraint else [])}
        for species in self._primary_species:
            value = by_name.get(
                species,
                model.ConcentrationConstraint(
                    species, 7.0 if species == "H+" else 0.0, "P" if species == "H+" else "T"
                ),
            )
            row = self.solution_table.rowCount()
            self.solution_table.insertRow(row)
            species_item = ui_construct(QtWidgets.QTableWidgetItem, species)
            species_item.setFlags(species_item.flags() & ~QtCore.Qt.ItemIsEditable)
            self.solution_table.setItem(row, 0, species_item)
            self.solution_table.setItem(
                row, 1, ui_construct(QtWidgets.QTableWidgetItem, str(value.value))
            )
            type_combo = QtWidgets.QComboBox()
            labels = {
                "T": msg("text.total.concentration.t"),
                "F": msg("text.free.ion.f"),
                "P": "pH P",
                "G": msg("text.gas.equilibrium.g"),
                "M": msg("text.mineral.equilibrium.m"),
                "Z": msg("repair.charge_label"),
                "L": msg("repair.log_total_label"),
                "PE": "pe PE",
            }
            for code in CONSTRAINT_TYPES:
                ui_call(type_combo, "addItem", labels[code], code)
            normalized = canonical_constraint_type(value.constraint_type)
            if type_combo.findData(normalized) < 0:
                ui_call(type_combo, "addItem", normalized, normalized)
            type_combo.setCurrentIndex(max(0, type_combo.findData(normalized)))
            associated = QtWidgets.QComboBox()
            self.solution_table.setCellWidget(row, 2, type_combo)
            self.solution_table.setCellWidget(row, 3, associated)
            self._populate_association(associated, normalized, value.associated_species)
            unit = ui_construct(QtWidgets.QTableWidgetItem, self._constraint_unit(normalized))
            unit.setFlags(unit.flags() & ~QtCore.Qt.ItemIsEditable)
            self.solution_table.setItem(row, 4, unit)
            type_combo.currentIndexChanged.connect(
                lambda _index, r=row: self._constraint_type_changed(r)
            )
            associated.currentIndexChanged.connect(self._save_solution_table)
        self.solution_table.blockSignals(False)
        self._load_initial_minerals(constraint)
        self.reference_solution.setVisible(item.form == "zero_gradient_outflow")

    def _save_solution_table(self):
        if self._loading:
            return
        selected = self._condition_at_row()
        if not selected or selected[0] != "TRANSPORT":
            return
        constraint = self._constraint_by_id(selected[1].constraint_id)
        if constraint is None:
            return
        values = []
        for row in range(self.solution_table.rowCount()):
            constraint_type = self.solution_table.cellWidget(row, 2).currentData()
            associated = self.solution_table.cellWidget(row, 3).currentData() or None
            values.append(
                model.ConcentrationConstraint(
                    self.solution_table.item(row, 0).text(),
                    self._float_or_none(self.solution_table.item(row, 1).text()),
                    constraint_type,
                    associated,
                )
            )
        minerals = []
        for row in range(self.initial_minerals_table.rowCount()):
            if self.initial_minerals_table.item(row, 0).checkState() == QtCore.Qt.Checked:
                minerals.append(
                    model.MineralConstraint(
                        self.initial_minerals_table.item(row, 1).text(),
                        self._float_or_none(self.initial_minerals_table.item(row, 2).text()),
                        self._float_or_none(self.initial_minerals_table.item(row, 3).text()),
                        self.initial_minerals_table.cellWidget(row, 4).currentData(),
                    )
                )
        constraint.concentrations = values
        constraint.minerals = minerals
        selected[1].solution_components = []
        self.changed.emit()

    def _migrate_embedded_constraints(self):
        existing = {item.id for item in self._constraints}
        for condition in self._transport:
            if condition.constraint_id and condition.constraint_id in existing:
                condition.solution_components = []
                continue
            if not condition.solution_components and condition.reference_solution:
                reference = next(
                    (item for item in self._transport if item.name == condition.reference_solution),
                    None,
                )
                if reference and reference.constraint_id:
                    condition.constraint_id = reference.constraint_id
                    continue
            identifier = self._unique_name(join_text("", [condition.name, "_chemistry"]), existing)
            concentrations = deepcopy(condition.solution_components) or self._blank_components()
            self._constraints.append(
                model.ChemicalConstraintConfig(identifier, identifier, concentrations, [])
            )
            existing.add(identifier)
            condition.constraint_id = identifier
            condition.solution_components = []

    def _constraint_by_id(self, identifier):
        return next((item for item in self._constraints if item.id == identifier), None)

    def _new_constraint(self, prefix="water_chemistry"):
        identifier = self._unique_name(prefix, {item.id for item in self._constraints})
        item = model.ChemicalConstraintConfig(identifier, identifier, self._blank_components(), [])
        self._constraints.append(item)
        return item

    def _refresh_constraint_selector(self, selected=""):
        self.constraint_selector.blockSignals(True)
        self.constraint_selector.clear()
        for item in self._constraints:
            ui_call(self.constraint_selector, "addItem", item.name or item.id, item.id)
        index = self.constraint_selector.findData(selected)
        if index >= 0:
            self.constraint_selector.setCurrentIndex(index)
        self.constraint_selector.blockSignals(False)

    def _add_constraint(self):
        selected = self._condition_at_row()
        if not selected or selected[0] != "TRANSPORT":
            return
        item = self._new_constraint()
        selected[1].constraint_id = item.id
        self._refresh_constraint_selector(item.id)
        self._load_solution_table(selected[1])
        self.changed.emit()

    def _constraint_selection_changed(self):
        if self._loading:
            return
        selected = self._condition_at_row()
        if not selected or selected[0] != "TRANSPORT":
            return
        selected[1].constraint_id = self.constraint_selector.currentData() or ""
        self._load_solution_table(selected[1])
        self.changed.emit()

    @staticmethod
    def _constraint_unit(kind):
        return {"P": "—", "PE": "—", "G": "bar", "L": "log10(mol/L)"}.get(kind, "mol/L")

    def _constraint_type_changed(self, row):
        type_combo = self.solution_table.cellWidget(row, 2)
        associated = self.solution_table.cellWidget(row, 3)
        current = associated.currentData() if associated else ""
        self._populate_association(associated, type_combo.currentData(), current)
        unit = self.solution_table.item(row, 4)
        if unit:
            ui_call(unit, "setText", self._constraint_unit(type_combo.currentData()))
        self._save_solution_table()

    def _populate_association(self, combo, constraint_type, selected=""):
        if combo is None:
            return
        combo.blockSignals(True)
        combo.clear()
        choices = (
            self._passive_gases
            if constraint_type == "G"
            else self._minerals
            if constraint_type == "M"
            else []
        )
        ui_call(combo, "addItem", "", "")
        for name in choices:
            ui_call(combo, "addItem", name, name)
        combo.setCurrentIndex(max(0, combo.findData(selected)))
        combo.setEnabled(bool(choices))
        combo.blockSignals(False)

    def _load_initial_minerals(self, constraint):
        self.initial_minerals_table.blockSignals(True)
        self.initial_minerals_table.setRowCount(0)
        existing = {item.mineral_id: item for item in (constraint.minerals if constraint else [])}
        for mineral_name in self._minerals:
            value = existing.get(mineral_name)
            row = self.initial_minerals_table.rowCount()
            self.initial_minerals_table.insertRow(row)
            use = ui_construct(QtWidgets.QTableWidgetItem)
            use.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsUserCheckable)
            use.setCheckState(QtCore.Qt.Checked if value else QtCore.Qt.Unchecked)
            self.initial_minerals_table.setItem(row, 0, use)
            name = ui_construct(QtWidgets.QTableWidgetItem, mineral_name)
            name.setFlags(name.flags() & ~QtCore.Qt.ItemIsEditable)
            self.initial_minerals_table.setItem(row, 1, name)
            self.initial_minerals_table.setItem(
                row,
                2,
                ui_construct(
                    QtWidgets.QTableWidgetItem, str(value.volume_fraction if value else 0.0)
                ),
            )
            self.initial_minerals_table.setItem(
                row,
                3,
                ui_construct(QtWidgets.QTableWidgetItem, str(value.surface_area if value else 0.0)),
            )
            unit = QtWidgets.QComboBox()
            for code in MINERAL_SURFACE_UNITS:
                ui_call(unit, "addItem", code, code)
            if value:
                if unit.findData(value.surface_area_unit) < 0:
                    ui_call(unit, "addItem", value.surface_area_unit, value.surface_area_unit)
                unit.setCurrentIndex(unit.findData(value.surface_area_unit))
            unit.currentIndexChanged.connect(self._save_solution_table)
            self.initial_minerals_table.setCellWidget(row, 4, unit)
        self.initial_minerals_table.blockSignals(False)

    def _add_condition(self):
        names = set(self.flow_names() + self.transport_names())
        name = self._unique_name("condition", names)
        self._flow.append(
            model.FlowConditionConfig(
                name, "boundary", "fixed_liquid_pressure", {"value": 101325.0, "unit": "Pa"}
            )
        )
        self._refresh_conditions()
        self.changed.emit()

    def _copy_condition(self):
        selected = self._condition_at_row()
        if not selected:
            return
        process, item = selected
        copy = deepcopy(item)
        copy.name = self._unique_name(
            item.name + "_copy", set(self.flow_names() + self.transport_names())
        )
        (self._flow if process == "FLOW" else self._transport).append(copy)
        self._refresh_conditions()
        self.changed.emit()

    def _delete_condition(self):
        selected = self._condition_at_row()
        if not selected:
            return
        process, item = selected
        if self.confirm_condition_delete and (
            not self.confirm_condition_delete(process, item.name)
        ):
            return
        (self._flow if process == "FLOW" else self._transport).remove(item)
        self._refresh_conditions()
        self.changed.emit()

    def _blank_components(self):
        return [
            model.ConcentrationConstraint(x, 7.0 if x == "H+" else 0.0, "P" if x == "H+" else "T")
            for x in self._primary_species
        ]

    def _refresh_reference_solutions(self):
        current = self.reference_solution.currentText()
        self.reference_solution.blockSignals(True)
        self.reference_solution.clear()
        ui_call(
            self.reference_solution,
            "addItems",
            [x.name for x in self._transport if x.form != "zero_gradient_outflow"],
        )
        self.reference_solution.setCurrentText(current)
        self.reference_solution.blockSignals(False)

    @staticmethod
    def _mapping_summary(process, form):
        if process == "FLOW":
            return {
                "fixed_liquid_pressure": "LIQUID_PRESSURE DIRICHLET",
                "hydrostatic_head": "LIQUID_PRESSURE HYDROSTATIC",
                "darcy_flux": "LIQUID_FLUX NEUMANN",
                "seepage": "LIQUID_PRESSURE SEEPAGE",
                "scaled_volumetric_rate": "RATE SCALED_VOLUMETRIC_RATE",
                "scaled_mass_rate": "RATE SCALED_MASS_RATE",
            }.get(form, msg("text.not.selected"))
        return {
            "initial_solution": "DIRICHLET_ZERO_GRADIENT",
            "inflow_solution": "DIRICHLET_ZERO_GRADIENT",
            "fixed_solution": "DIRICHLET",
            "zero_gradient_outflow": "ZERO_GRADIENT",
            "injection_solution": "DIRICHLET",
        }.get(form, msg("text.not.selected"))

    @staticmethod
    def _form_label(process, form):
        options = FLOW_FORMS if process == "FLOW" else TRANSPORT_FORMS
        return next(
            (label for values in options.values() for label, value in values if value == form), form
        )

    @staticmethod
    def _set_combo(combo, value):
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    @staticmethod
    def _float_or_none(text):
        try:
            value = float(text)
            return value if finite_real(value) else None
        except ValueError:
            return None

    @staticmethod
    def _unique_name(prefix, existing):
        if prefix not in existing:
            return prefix
        index = 1
        while join_text("", [prefix, "_", index]) in existing:
            index += 1
        return join_text("", [prefix, "_", index])
