from __future__ import annotations

from PyQt5 import QtCore, QtGui, QtWidgets

from fracrtm.config import pflotran as model
from fracrtm.messages import join_text
from fracrtm.messages import message as msg
from fracrtm.ui.localization import mark_verbatim, ui_call, ui_construct

from .pflotran_controls import CompactDoubleSpinBox, ScientificDoubleSpinBox, ScientificValueEdit
from .region_conditions_widget import RegionConditionsWidget
from .spatial_assignment_widget import SpatialAssignmentWidget


class PFLOTRANPageBuilder:
    def _page(self, title: str, description: str) -> QtWidgets.QVBoxLayout:
        page = QtWidgets.QWidget()
        page.setObjectName("PagePanel")
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(25, 22, 25, 22)
        heading = ui_construct(QtWidgets.QLabel, title)
        heading.setObjectName("PageTitle")
        note = ui_construct(QtWidgets.QLabel, description)
        note.setObjectName("PageDescription")
        note.hide()
        note.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(note)
        self.stack.addWidget(page)
        return layout

    def _table(self, headers: list[str], rows: int = 5) -> QtWidgets.QTableWidget:
        table = QtWidgets.QTableWidget(0, len(headers))
        ui_call(table, "setHorizontalHeaderLabels", headers)
        table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setMinimumHeight(92 + rows * 28)
        table.itemChanged.connect(self._mark_dirty)
        self._tables.append(table)
        return table

    def _build_task(self) -> None:
        layout = self._page(
            msg("section.simulation_process"),
            msg(
                "text.select.the.physical.processes.to.solve.later.pages.and.parameters.update.automatically.when.solute"
            ),
        )
        processes = ui_construct(QtWidgets.QGroupBox, msg("section.simulation_process"))
        process_layout = QtWidgets.QVBoxLayout(processes)
        self.flow_enabled_check = ui_construct(QtWidgets.QCheckBox, msg("text.subsurface.flow"))
        self.flow_enabled_check.setChecked(False)
        ui_call(
            self.flow_enabled_check,
            "setToolTip",
            msg("text.enable.pflotran.groundwater.flow.richards"),
        )
        self.transport_enabled_check = ui_construct(
            QtWidgets.QCheckBox, msg("text.solute.transport")
        )
        ui_call(
            self.transport_enabled_check,
            "setToolTip",
            msg("text.enabling.solute.transport.also.enables.groundwater.flow"),
        )
        process_layout.addWidget(self.flow_enabled_check)
        process_layout.addWidget(self.transport_enabled_check)
        state_row = QtWidgets.QHBoxLayout()
        state_row.addWidget(ui_construct(QtWidgets.QLabel, msg("text.flow.mode")))
        self.flow_state_combo = QtWidgets.QComboBox()
        ui_call(self.flow_state_combo, "addItem", msg("text.not.set"), None)
        ui_call(self.flow_state_combo, "addItem", msg("text.transient"), "transient")
        ui_call(self.flow_state_combo, "addItem", msg("text.steady.state"), "steady_state")
        state_row.addWidget(self.flow_state_combo)
        state_row.addStretch()
        process_layout.addLayout(state_row)
        layout.addWidget(processes)
        mapping = ui_construct(QtWidgets.QGroupBox, msg("text.model.configuration.summary"))
        mapping_layout = QtWidgets.QVBoxLayout(mapping)
        self.task_mapping = ui_construct(QtWidgets.QLabel)
        self.task_mapping.hide()
        self.task_mapping.setWordWrap(True)
        self.task_mapping.setObjectName("MutedLabel")
        mapping_layout.addWidget(self.task_mapping)
        layout.addWidget(mapping)
        layout.addStretch()
        self.flow_enabled_check.toggled.connect(self._flow_selection_changed)
        self.transport_enabled_check.toggled.connect(self._transport_selection_changed)
        self.flow_state_combo.currentIndexChanged.connect(self._mark_dirty)

    def _build_grid(self) -> None:
        layout = self._page(
            msg("text.mesh.data"),
            msg(
                "text.automatically.read.the.computational.mesh.material.regions.effective.medium.datasets.and.boundary.faces.generated"
            ),
        )
        toolbar = QtWidgets.QHBoxLayout()
        toolbar.addStretch()
        self.rescan_spatial_button = ui_construct(QtWidgets.QPushButton, msg("text.rescan"))
        self.rescan_spatial_button.clicked.connect(self._rescan_spatial_data)
        toolbar.addWidget(self.rescan_spatial_button)
        layout.addLayout(toolbar)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 8, 0)
        files = ui_construct(
            QtWidgets.QGroupBox, msg("text.computational.grid.and.material.regions")
        )
        file_layout = QtWidgets.QVBoxLayout(files)
        file_form = QtWidgets.QFormLayout()
        self.mesh = mark_verbatim(ui_construct(QtWidgets.QLineEdit))
        self.mesh.setReadOnly(True)
        self.material_file = mark_verbatim(ui_construct(QtWidgets.QLineEdit))
        self.material_file.setReadOnly(True)
        self.mesh_file_status = ui_construct(QtWidgets.QLabel, msg("text.not.found"))
        self.material_file_status = ui_construct(QtWidgets.QLabel, msg("text.not.found"))

        def file_row(editor, status):
            row = QtWidgets.QWidget()
            row_layout = QtWidgets.QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(editor, 1)
            row_layout.addWidget(status)
            return row

        ui_call(
            file_form,
            "addRow",
            msg("viewer.computational_mesh"),
            file_row(self.mesh, self.mesh_file_status),
        )
        ui_call(
            file_form,
            "addRow",
            msg("viewer.material_regions"),
            file_row(self.material_file, self.material_file_status),
        )
        file_layout.addLayout(file_form)
        summary_box = ui_construct(QtWidgets.QGroupBox, msg("text.grid.summary"))
        summary_form = QtWidgets.QFormLayout(summary_box)
        self.mesh_summary_labels = {}
        for key, label in (
            ("type", msg("text.mesh.type")),
            ("cells", msg("text.number.of.mesh.cells")),
            ("connections", msg("text.number.of.connections")),
            ("materials", msg("text.number.of.materials")),
            ("bounds", msg("text.coordinate.extent")),
            ("unit", msg("text.coordinate.unit")),
        ):
            value = ui_construct(QtWidgets.QLabel, "—")
            value.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            self.mesh_summary_labels[key] = value
            ui_call(summary_form, "addRow", label, value)
        file_layout.addWidget(summary_box)
        content_layout.addWidget(files)
        self.datasets = self._table(
            ["ID", msg("text.name"), msg("text.relative.file"), msg("text.hdf5.dataset.name")], 2
        )
        self.datasets.setVisible(False)
        dataset_group = ui_construct(QtWidgets.QGroupBox, msg("text.available.spatial.datasets"))
        dataset_layout = QtWidgets.QVBoxLayout(dataset_group)
        dataset_note = ui_construct(
            QtWidgets.QLabel,
            msg(
                "text.automatically.read.udfm.effective.medium.results.datasets.can.be.referenced.by.material.properties.and"
            ),
        )
        dataset_note.setObjectName("MutedLabel")
        dataset_note.setWordWrap(True)
        dataset_layout.addWidget(dataset_note)
        self.spatial_dataset_table = QtWidgets.QTableWidget(0, 6)
        self.spatial_dataset_table.setProperty("fracrtm_label_columns", [1])
        ui_call(
            self.spatial_dataset_table,
            "setHorizontalHeaderLabels",
            [
                msg("text.status"),
                msg("viewer.property"),
                msg("text.dataset.name"),
                msg("text.number.of.cells"),
                msg("text.value.range"),
                msg("text.unit"),
            ],
        )
        self.spatial_dataset_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.spatial_dataset_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.spatial_dataset_table.verticalHeader().setVisible(False)
        self.spatial_dataset_table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeToContents
        )
        self.spatial_dataset_table.horizontalHeader().setSectionResizeMode(
            2, QtWidgets.QHeaderView.Stretch
        )
        dataset_layout.addWidget(self.spatial_dataset_table)
        content_layout.addWidget(dataset_group)
        boundary_group = ui_construct(QtWidgets.QGroupBox, msg("text.available.boundary.faces"))
        boundary_layout = QtWidgets.QVBoxLayout(boundary_group)
        boundary_note = ui_construct(
            QtWidgets.QLabel,
            msg(
                "text.automatically.identify.computational.mesh.boundary.faces.define.their.boundary.conditions.on.regions.conditions"
            ),
        )
        boundary_note.setObjectName("MutedLabel")
        boundary_note.setWordWrap(True)
        boundary_layout.addWidget(boundary_note)
        self.boundary_tree = QtWidgets.QTreeWidget()
        self.boundary_tree.setProperty("fracrtm_label_columns", [1])
        ui_call(
            self.boundary_tree,
            "setHeaderLabels",
            [
                msg("text.status"),
                msg("text.boundary.face"),
                msg("text.internal.id"),
                msg("text.face.count"),
            ],
        )
        self.boundary_tree.header().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.boundary_tree.header().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.boundary_tree.setRootIsDecorated(False)
        boundary_layout.addWidget(self.boundary_tree)
        content_layout.addWidget(boundary_group)
        check_group = ui_construct(QtWidgets.QGroupBox, msg("text.data.validation.status"))
        check_layout = QtWidgets.QVBoxLayout(check_group)
        check_head = QtWidgets.QHBoxLayout()
        self.spatial_check_status = ui_construct(QtWidgets.QLabel, msg("status.not_checked"))
        self.spatial_check_status.setObjectName("SectionTitle")
        self.spatial_details_button = ui_construct(QtWidgets.QPushButton, msg("text.view.details"))
        self.spatial_details_button.setCheckable(True)
        check_head.addWidget(self.spatial_check_status, 1)
        check_head.addWidget(self.spatial_details_button)
        check_layout.addLayout(check_head)
        self.spatial_check_details = QtWidgets.QTreeWidget()
        self.spatial_check_details.setProperty("fracrtm_label_columns", [0, 1])
        ui_call(
            self.spatial_check_details, "setHeaderLabels", [msg("text.severity"), msg("text.issue")]
        )
        self.spatial_check_details.header().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.spatial_check_details.setVisible(False)
        self.spatial_details_button.toggled.connect(self.spatial_check_details.setVisible)
        check_layout.addWidget(self.spatial_check_details)
        content_layout.addWidget(check_group)
        gravity_group = ui_construct(QtWidgets.QGroupBox, msg("text.gravity.settings"))
        gravity_form = QtWidgets.QFormLayout(gravity_group)
        self.gravity_mode = QtWidgets.QComboBox()
        ui_call(self.gravity_mode, "addItem", msg("text.standard.gravity"), "standard")
        ui_call(self.gravity_mode, "addItem", msg("text.ignore.gravity"), "off")
        ui_call(self.gravity_mode, "addItem", msg("text.custom"), "custom")
        self.gravity_custom_panel = QtWidgets.QWidget()
        gravity_vector_layout = QtWidgets.QHBoxLayout(self.gravity_custom_panel)
        gravity_vector_layout.setContentsMargins(0, 0, 0, 0)
        self.gravity_edits = []
        for label in ("Gx", "Gy", "Gz"):
            gravity_vector_layout.addWidget(ui_construct(QtWidgets.QLabel, label))
            edit = ui_construct(QtWidgets.QLineEdit, "0")
            edit.setValidator(QtGui.QDoubleValidator(edit))
            edit.setMaximumWidth(110)
            self.gravity_edits.append(edit)
            gravity_vector_layout.addWidget(edit)
        gravity_vector_layout.addWidget(ui_construct(QtWidgets.QLabel, "m/s²"))
        gravity_vector_layout.addStretch()
        ui_call(gravity_form, "addRow", msg("text.gravity.mode"), self.gravity_mode)
        ui_call(gravity_form, "addRow", msg("text.gravity.vector"), self.gravity_custom_panel)
        self.gravity_mode.currentIndexChanged.connect(self._update_gravity_ui)
        content_layout.addWidget(gravity_group)
        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

    def _build_time(self) -> None:
        layout = self._page(
            msg("text.time.solvers"),
            msg(
                "text.set.simulation.duration.and.time.steps.then.use.pflotran.recommended.solver.settings.or.configure"
            ),
        )
        time_group = ui_construct(QtWidgets.QGroupBox, msg("text.simulation.time.settings"))
        time_form = QtWidgets.QFormLayout(time_group)
        self.final_time_value = CompactDoubleSpinBox()
        self.initial_timestep_value = CompactDoubleSpinBox()
        self.maximum_timestep_value = CompactDoubleSpinBox()
        self.final_time_unit = QtWidgets.QComboBox()
        self.initial_timestep_unit = QtWidgets.QComboBox()
        self.maximum_timestep_unit = QtWidgets.QComboBox()
        for value in (
            self.final_time_value,
            self.initial_timestep_value,
            self.maximum_timestep_value,
        ):
            value.setRange(0.0, 1000000000000.0)
            value.setSpecialValueText("\u200b")
            value.setMinimumWidth(170)
        for unit in (self.final_time_unit, self.initial_timestep_unit, self.maximum_timestep_unit):
            ui_call(unit, "addItem", msg("text.not.set"), None)
            for label in ("s", "min", "h", "d", "month", "y"):
                ui_call(unit, "addItem", label, label)
            unit.setMinimumWidth(90)
            unit.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        for label, value, unit in (
            (msg("text.simulation.duration"), self.final_time_value, self.final_time_unit),
            (
                msg("text.initial.time.step"),
                self.initial_timestep_value,
                self.initial_timestep_unit,
            ),
            (
                msg("text.maximum.time.step"),
                self.maximum_timestep_value,
                self.maximum_timestep_unit,
            ),
        ):
            row = QtWidgets.QWidget()
            row_layout = QtWidgets.QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(value)
            row_layout.addWidget(unit)
            row_layout.addStretch()
            ui_call(time_form, "addRow", label, row)
        layout.addWidget(time_group)
        solver_group = ui_construct(QtWidgets.QGroupBox, msg("text.numerical.solver.settings"))
        solver_layout = QtWidgets.QVBoxLayout(solver_group)
        solver_layout.addWidget(ui_construct(QtWidgets.QLabel, msg("text.solver.parameter.mode")))
        self.recommended_radio = ui_construct(
            QtWidgets.QRadioButton, msg("text.pflotran.recommended.settings")
        )
        self.custom_radio = ui_construct(QtWidgets.QRadioButton, msg("text.custom.settings"))
        self.solver_mode_group = QtWidgets.QButtonGroup(self)
        self.solver_mode_group.addButton(self.recommended_radio)
        self.solver_mode_group.addButton(self.custom_radio)
        self.recommended_radio.setChecked(True)
        solver_layout.addWidget(self.recommended_radio)
        solver_layout.addWidget(self.custom_radio)
        self.solver_mode_stack = QtWidgets.QStackedWidget()
        recommended_page = QtWidgets.QWidget()
        recommended_layout = QtWidgets.QVBoxLayout(recommended_page)
        self.recommended_summary_label = ui_construct(QtWidgets.QLabel)
        self.recommended_summary_label.setWordWrap(True)
        self.recommended_summary_label.setObjectName("MutedLabel")
        recommended_layout.addWidget(self.recommended_summary_label)
        recommended_layout.addStretch()
        custom_page = QtWidgets.QWidget()
        custom_layout = QtWidgets.QVBoxLayout(custom_page)
        self.process_tab_widget = QtWidgets.QTabWidget()
        self.flow_solver_page, self.flow_solver_widgets = self._create_process_solver_page()
        self.transport_solver_page, self.transport_solver_widgets = (
            self._create_process_solver_page()
        )
        ui_call(self.process_tab_widget, "addTab", self.flow_solver_page, msg("text.flow.flow"))
        ui_call(
            self.process_tab_widget,
            "addTab",
            self.transport_solver_page,
            msg("text.solute.transport.transport"),
        )
        self.no_process_solver_label = ui_construct(
            QtWidgets.QLabel, msg("text.no.configurable.simulation.process.is.enabled")
        )
        self.no_process_solver_label.setObjectName("MutedLabel")
        custom_layout.addWidget(self.no_process_solver_label)
        custom_layout.addWidget(self.process_tab_widget, 1)
        self.solver_mode_stack.addWidget(recommended_page)
        self.solver_mode_stack.addWidget(custom_page)
        solver_layout.addWidget(self.solver_mode_stack, 1)
        layout.addWidget(solver_group, 1)
        self.recommended_radio.toggled.connect(self._update_solver_mode_ui)
        self.custom_radio.toggled.connect(self._update_solver_mode_ui)

    def _build_chemistry(self) -> None:
        layout = self._page(
            msg("text.chemistry"),
            msg(
                "validation.define.the.thermodynamic.database.aqueous.and.gas.species.minerals.and.mineral.kinetics.parameters.required"
            ),
        )
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 8, 0)
        database_group = ui_construct(QtWidgets.QGroupBox, msg("text.thermodynamic.database"))
        database_form = QtWidgets.QFormLayout(database_group)
        self.database_path_edit = mark_verbatim(ui_construct(QtWidgets.QLineEdit))
        self.database_path_edit.setReadOnly(True)
        self.select_database_button = ui_construct(QtWidgets.QPushButton, msg("text.select.file"))
        database_row = QtWidgets.QWidget()
        database_row_layout = QtWidgets.QHBoxLayout(database_row)
        database_row_layout.setContentsMargins(0, 0, 0, 0)
        database_row_layout.addWidget(self.database_path_edit, 1)
        database_row_layout.addWidget(self.select_database_button)
        self._database_status = "invalid"
        self._database_message = ""
        self.database_path_edit.setProperty("fracrtm_user_content", True)
        self.database_status_label = ui_construct(
            QtWidgets.QLabel, msg("text.database.status.not.loaded")
        )
        ui_call(database_form, "addRow", msg("text.database.file"), database_row)
        ui_call(database_form, "addRow", self.database_status_label)
        content_layout.addWidget(database_group)
        aqueous_group = ui_construct(QtWidgets.QGroupBox, msg("text.aqueous.species"))
        aqueous_form = QtWidgets.QFormLayout(aqueous_group)
        self.primary_species_summary = ui_construct(QtWidgets.QLineEdit)
        self.primary_species_summary.setReadOnly(True)
        self.secondary_species_summary = ui_construct(QtWidgets.QLineEdit)
        self.secondary_species_summary.setReadOnly(True)
        self.select_primary_species_button = ui_construct(
            QtWidgets.QPushButton, msg("text.select.2")
        )
        self.select_secondary_species_button = ui_construct(
            QtWidgets.QPushButton, msg("text.select.2")
        )
        ui_call(
            aqueous_form,
            "addRow",
            msg("chemistry.primary_species"),
            self._summary_selection_row(
                self.primary_species_summary, self.select_primary_species_button
            ),
        )
        ui_call(
            aqueous_form,
            "addRow",
            msg("text.secondary.species"),
            self._summary_selection_row(
                self.secondary_species_summary, self.select_secondary_species_button
            ),
        )
        content_layout.addWidget(aqueous_group)
        gas_group = ui_construct(QtWidgets.QGroupBox, msg("text.gas.species.optional"))
        gas_layout = QtWidgets.QVBoxLayout(gas_group)
        gas_form = QtWidgets.QFormLayout()
        self.passive_gas_summary = ui_construct(QtWidgets.QLineEdit)
        self.passive_gas_summary.setReadOnly(True)
        self.select_passive_gas_button = ui_construct(
            QtWidgets.QPushButton, msg("action.select_gas_species")
        )
        ui_call(
            gas_form,
            "addRow",
            msg("text.selected.gases"),
            self._summary_selection_row(self.passive_gas_summary, self.select_passive_gas_button),
        )
        gas_layout.addLayout(gas_form)
        gas_note = ui_construct(
            QtWidgets.QLabel, msg("text.all.gases.are.treated.as.passive.in.this.version")
        )
        gas_note.setObjectName("MutedLabel")
        gas_layout.addWidget(gas_note)
        content_layout.addWidget(gas_group)
        minerals_group = ui_construct(QtWidgets.QGroupBox, msg("text.minerals.minerals"))
        minerals_form = QtWidgets.QFormLayout(minerals_group)
        self.selected_minerals_summary = ui_construct(QtWidgets.QLineEdit)
        self.selected_minerals_summary.setReadOnly(True)
        self.select_minerals_button = ui_construct(
            QtWidgets.QPushButton, msg("text.select.minerals")
        )
        ui_call(
            minerals_form,
            "addRow",
            msg("text.selected.minerals"),
            self._summary_selection_row(
                self.selected_minerals_summary, self.select_minerals_button
            ),
        )
        content_layout.addWidget(minerals_group)
        options_group = ui_construct(QtWidgets.QGroupBox, msg("text.chemistry.options"))
        options_layout = QtWidgets.QVBoxLayout(options_group)
        self.log_formulation = ui_construct(QtWidgets.QCheckBox, msg("text.use.logarithmic.form"))
        self.activity_coefficients = QtWidgets.QComboBox()
        ui_call(
            self.activity_coefficients, "addItem", msg("text.default.not.explicitly.set"), "default"
        )
        ui_call(
            self.activity_coefficients,
            "addItem",
            msg("text.update.every.time.step.timestep"),
            "timestep",
        )
        self.update_porosity = ui_construct(
            QtWidgets.QCheckBox, msg("text.update.porosity.from.reactions")
        )
        self.update_permeability = ui_construct(
            QtWidgets.QCheckBox, msg("text.update.permeability.from.reactions")
        )
        options_layout.addWidget(self.log_formulation)
        activity_row = QtWidgets.QHBoxLayout()
        activity_row.addWidget(ui_construct(QtWidgets.QLabel, msg("text.activity.coefficients")))
        activity_row.addWidget(self.activity_coefficients)
        activity_row.addStretch()
        options_layout.addLayout(activity_row)
        options_layout.addWidget(self.update_porosity)
        options_layout.addWidget(self.update_permeability)
        content_layout.addWidget(options_group)
        kinetics_group = ui_construct(QtWidgets.QGroupBox, msg("text.mineral.kinetics"))
        kinetics_layout = QtWidgets.QVBoxLayout(kinetics_group)
        self.mineral_kinetics_table = QtWidgets.QTableWidget(0, 8)
        ui_call(
            self.mineral_kinetics_table,
            "setHorizontalHeaderLabels",
            [
                msg("text.mineral.name"),
                msg("text.rate.constant"),
                msg("text.rate.unit"),
                msg("text.surface.area.model"),
                msg("text.surface.area.per.mass"),
                msg("text.area.unit"),
                msg("text.controlling.species"),
                "Alpha",
            ],
        )
        self.mineral_kinetics_table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeToContents
        )
        self.mineral_kinetics_table.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.Stretch
        )
        self.mineral_kinetics_table.verticalHeader().setVisible(False)
        kinetics_layout.addWidget(self.mineral_kinetics_table)
        kinetic_actions = QtWidgets.QHBoxLayout()
        self.add_kinetic_mineral_button = ui_construct(
            QtWidgets.QPushButton, msg("text.add.mineral")
        )
        self.delete_kinetic_mineral_button = ui_construct(
            QtWidgets.QPushButton, msg("text.delete.selected")
        )
        kinetic_actions.addWidget(self.add_kinetic_mineral_button)
        kinetic_actions.addWidget(self.delete_kinetic_mineral_button)
        kinetic_actions.addStretch()
        kinetics_layout.addLayout(kinetic_actions)
        content_layout.addWidget(kinetics_group)
        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.select_database_button.clicked.connect(self._select_thermodynamic_database)
        self.select_primary_species_button.clicked.connect(
            lambda: self._select_aqueous_species("primary")
        )
        self.select_secondary_species_button.clicked.connect(
            lambda: self._select_aqueous_species("secondary")
        )
        self.select_passive_gas_button.clicked.connect(self._select_passive_gases)
        self.select_minerals_button.clicked.connect(self._select_minerals)
        self.add_kinetic_mineral_button.clicked.connect(self._add_kinetic_mineral)
        self.delete_kinetic_mineral_button.clicked.connect(self._delete_kinetic_minerals)
        self.activity_coefficients.currentIndexChanged.connect(self._mark_dirty)

    def _build_conditions(self) -> None:
        layout = self._page(
            msg("text.regions.conditions"),
            msg(
                "text.define.spatial.regions.and.reusable.conditions.regions.describe.where.a.condition.applies.and.conditions"
            ),
        )
        self.region_conditions_editor = RegionConditionsWidget(self._project_directory, self)
        self.region_conditions_editor.changed.connect(self._mark_dirty)
        self.region_conditions_editor.locateRequested.connect(
            lambda: self._open_spatial_viewer("region")
        )
        self.region_conditions_editor.changed.connect(self._refresh_spatial_locator)
        self.region_conditions_editor.region_table.itemSelectionChanged.connect(
            self._refresh_spatial_locator
        )
        self.constraints = self._table(
            [
                "ID",
                msg("text.name"),
                msg("text.concentration.constraints"),
                msg("text.mineral.constraints"),
            ],
            1,
        )
        self.constraints.hide()
        layout.addWidget(self.region_conditions_editor, 1)

    def _build_coupling(self) -> None:
        layout = self._page(
            msg("text.assignments"),
            msg(
                "text.associate.the.model.spatially.by.selecting.regions.and.conditions.material.mapping.is.read.automatically"
            ),
        )
        self.spatial_assignment_editor = SpatialAssignmentWidget(self)
        self.spatial_assignment_editor.set_reference_providers(
            self._assignment_region_choices,
            self._assignment_flow_choices,
            self._assignment_transport_choices,
        )
        self.spatial_assignment_editor.changed.connect(self._spatial_assignment_changed)
        self.region_conditions_editor.referenceRenamed.connect(
            self.spatial_assignment_editor.rename_reference
        )
        self.region_conditions_editor.referenceRenamed.connect(self._rename_output_reference)
        self.spatial_assignment_editor.showMaterialsRequested.connect(
            self._show_selected_material_properties
        )
        self.spatial_assignment_editor.locateRequested.connect(
            lambda: self._open_spatial_viewer("assignment")
        )
        self.spatial_assignment_editor.changed.connect(self._refresh_spatial_locator)
        self.spatial_assignment_editor.assignmentSelected.connect(self._refresh_spatial_locator)
        layout.addWidget(self.spatial_assignment_editor, 1)
        self.region_conditions_editor.confirm_region_delete = self._confirm_region_delete
        self.region_conditions_editor.confirm_condition_delete = self._confirm_condition_delete

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        header = QtWidgets.QHBoxLayout()
        title_box = QtWidgets.QVBoxLayout()
        title = ui_construct(QtWidgets.QLabel, msg("text.pflotran.model.configuration"))
        title.setObjectName("PageTitle")
        note = ui_construct(
            QtWidgets.QLabel,
            msg(
                "text.build.the.model.through.processes.data.properties.conditions.spatial.assignment.and.review.the.software"
            ),
        )
        note.setObjectName("PageDescription")
        note.hide()
        title_box.addWidget(title)
        title_box.addWidget(note)
        header.addLayout(title_box)
        header.addStretch()
        self.status = ui_construct(QtWidgets.QLabel, msg("status.not_checked"))
        self.status.setStyleSheet(
            "padding:6px 12px;border-radius:12px;background:#eef2f1;color:#52646a;"
        )
        header.addWidget(self.status, 0, QtCore.Qt.AlignTop)
        root.addLayout(header)
        self.body_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        root.addWidget(self.body_splitter, 1)
        editor_host = QtWidgets.QWidget()
        body = QtWidgets.QHBoxLayout(editor_host)
        body.setContentsMargins(0, 0, 0, 0)
        self.navigation = QtWidgets.QListWidget()
        self.navigation.setObjectName("Navigation")
        self.navigation.setFixedWidth(220)
        self._nav_labels = (
            msg("section.simulation_process"),
            msg("text.mesh.data"),
            msg("text.materials"),
            msg("text.chemistry"),
            msg("text.regions"),
            msg("text.conditions"),
            msg("text.assignments"),
            msg("text.time.solvers"),
            msg("text.output.review"),
        )
        for number, label in enumerate(self._nav_labels, 1):
            ui_call(self.navigation, "addItem", join_text("", [number, "  ", label]))
        self.stack = QtWidgets.QStackedWidget()
        body.addWidget(self.navigation)
        body.addWidget(self.stack, 1)
        self._build_task()
        self._build_grid()
        self._build_time()
        self._build_materials()
        self._build_chemistry()
        self._build_conditions()
        self._build_coupling()
        self._build_output_review()
        self.body_splitter.addWidget(editor_host)
        self.body_splitter.setStretchFactor(0, 1)
        self.navigation.currentRowChanged.connect(self._show_page)
        self.navigation.setCurrentRow(0)
        actions = QtWidgets.QHBoxLayout()
        reset = ui_construct(QtWidgets.QPushButton, msg("text.clear.configuration"))
        reset.clicked.connect(self._reset)
        import_button = ui_construct(QtWidgets.QPushButton, msg("text.load.configuration"))
        import_button.clicked.connect(self._import_json)
        export_button = ui_construct(QtWidgets.QPushButton, msg("text.export.configuration"))
        export_button.clicked.connect(self._export_json)
        check = ui_construct(QtWidgets.QPushButton, msg("text.validate.model"))
        check.clicked.connect(self._refresh_review)
        cancel = ui_construct(QtWidgets.QPushButton, msg("text.cancel"))
        cancel.clicked.connect(self.reject)
        save = ui_construct(QtWidgets.QPushButton, msg("text.save.configuration"))
        save.setObjectName("PrimaryButton")
        save.clicked.connect(self._accept)
        actions.addWidget(reset)
        actions.addWidget(import_button)
        actions.addWidget(export_button)
        actions.addStretch()
        actions.addWidget(check)
        actions.addWidget(cancel)
        actions.addWidget(save)
        root.addLayout(actions)
        for label in self.findChildren(QtWidgets.QLabel):
            if label.objectName() == "PageDescription":
                label.hide()

    def _build_output_review(self) -> None:
        layout = self._page(
            msg("text.output.review"),
            msg(
                "text.configure.field.snapshots.mass.conservation.diagnostics.observation.files.and.run.records"
            ),
        )
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content)

        def schedule_editor(form, prefix: str, timestep_default: int) -> None:
            mode = QtWidgets.QComboBox()
            ui_call(mode, "addItem", msg("text.uniform.time.intervals"), "periodic_time")
            ui_call(mode, "addItem", msg("text.specified.times"), "specified_times")
            ui_call(mode, "addItem", msg("text.every.n.time.steps"), "periodic_timestep")
            stack = QtWidgets.QStackedWidget()
            interval_page = QtWidgets.QWidget()
            interval_form = QtWidgets.QFormLayout(interval_page)
            interval = CompactDoubleSpinBox()
            interval.setRange(0, 1000000000000.0)
            interval.setValue(1.0)
            interval_unit = QtWidgets.QComboBox()
            ui_call(interval_unit, "addItems", ["s", "min", "h", "d", "month", "y"])
            interval_row = QtWidgets.QWidget()
            interval_box = QtWidgets.QHBoxLayout(interval_row)
            interval_box.setContentsMargins(0, 0, 0, 0)
            interval_box.addWidget(interval)
            interval_box.addWidget(interval_unit)
            interval_box.addStretch()
            ui_call(interval_form, "addRow", msg("text.output.interval"), interval_row)
            stack.addWidget(interval_page)
            times_page = QtWidgets.QWidget()
            times_form = QtWidgets.QFormLayout(times_page)
            times = ui_construct(QtWidgets.QLineEdit)
            ui_call(times, "setPlaceholderText", "0.1, 0.5, 1, 2, 5, 10")
            times_unit = QtWidgets.QComboBox()
            ui_call(times_unit, "addItems", ["s", "min", "h", "d", "month", "y"])
            ui_call(times_form, "addRow", msg("text.specified.times.comma.separated"), times)
            ui_call(times_form, "addRow", msg("text.time.unit"), times_unit)
            stack.addWidget(times_page)
            timestep = QtWidgets.QSpinBox()
            timestep.setRange(1, 2147483647)
            timestep.setValue(timestep_default)
            timestep_page = QtWidgets.QWidget()
            timestep_form = QtWidgets.QFormLayout(timestep_page)
            ui_call(timestep_form, "addRow", msg("text.time.step.interval"), timestep)
            stack.addWidget(timestep_page)
            setattr(self, join_text("", [prefix, "_mode"]), mode)
            setattr(self, join_text("", [prefix, "_schedule_stack"]), stack)
            setattr(self, join_text("", [prefix, "_interval"]), interval)
            setattr(self, join_text("", [prefix, "_interval_unit"]), interval_unit)
            setattr(self, join_text("", [prefix, "_times"]), times)
            setattr(self, join_text("", [prefix, "_times_unit"]), times_unit)
            setattr(self, join_text("", [prefix, "_timestep_interval"]), timestep)
            ui_call(form, "addRow", msg("text.output.schedule"), mode)
            ui_call(form, "addRow", stack)
            mode.currentIndexChanged.connect(lambda _=0, p=prefix: self._update_schedule_stack(p))

        snapshot = ui_construct(QtWidgets.QGroupBox, msg("text.1.field.snapshots"))
        form = QtWidgets.QFormLayout(snapshot)
        self.output_format = QtWidgets.QComboBox()
        ui_call(self.output_format, "addItem", "VTK", "VTK")
        ui_call(self.output_format, "addItem", "HDF5", "HDF5")
        ui_call(form, "addRow", msg("text.snapshot.format"), self.output_format)
        schedule_editor(form, "output", 10)
        scope = QtWidgets.QWidget()
        scope_layout = QtWidgets.QHBoxLayout(scope)
        scope_layout.setContentsMargins(0, 0, 0, 0)
        self.print_initial = ui_construct(QtWidgets.QCheckBox, msg("text.output.initial.state"))
        self.print_final = ui_construct(QtWidgets.QCheckBox, msg("text.output.final.state"))
        self.print_initial.setChecked(True)
        self.print_final.setChecked(True)
        scope_layout.addWidget(self.print_initial)
        scope_layout.addWidget(self.print_final)
        scope_layout.addStretch()
        ui_call(form, "addRow", msg("text.snapshot.scope"), scope)
        self.velocity_at_center = ui_construct(
            QtWidgets.QCheckBox, msg("text.output.cell.centered.velocity.velocity.at.center")
        )
        ui_call(form, "addRow", msg("text.velocity.2"), self.velocity_at_center)
        variables = ui_construct(QtWidgets.QGroupBox, msg("text.output.variables"))
        variable_layout = QtWidgets.QVBoxLayout(variables)
        self.flow_output_group = ui_construct(QtWidgets.QGroupBox, msg("text.flow.results"))
        flow_layout = QtWidgets.QGridLayout(self.flow_output_group)
        self.flow_output_checks = {}
        flow_variables = (
            (msg("text.liquid.pressure"), "liquid_pressure"),
            (msg("text.liquid.head"), "liquid_head"),
            (msg("text.liquid.saturation"), "liquid_saturation"),
            (msg("viewer.porosity"), "porosity"),
            (msg("viewer.permeability"), "permeability"),
            (msg("text.x.direction.permeability"), "permeability_x"),
            (msg("text.y.direction.permeability"), "permeability_y"),
            (msg("text.z.direction.permeability"), "permeability_z"),
        )
        for index, (label, keyword) in enumerate(flow_variables):
            check = ui_construct(QtWidgets.QCheckBox, label)
            self.flow_output_checks[keyword] = check
            flow_layout.addWidget(check, index // 3, index % 3)
        variable_layout.addWidget(self.flow_output_group)
        self.chem_output_group = ui_construct(
            QtWidgets.QGroupBox, msg("text.chemical.and.mineral.results")
        )
        chemistry_layout = QtWidgets.QGridLayout(self.chem_output_group)
        self.chem_output_checks = {}
        chemistry_variables = (
            ("pH", "ph"),
            (msg("text.all.primary.species.concentrations.total"), "all_primary_species"),
            (msg("text.all.chemistry.variables.all"), "all_species"),
            (msg("text.secondary.species.concentrations"), "secondary_species"),
            (msg("text.mineral.volume.fractions"), "mineral_volume_fraction"),
            (msg("text.mineral.reaction.rate"), "mineral_rate"),
            (msg("text.mineral.saturation.index"), "mineral_saturation_index"),
        )
        for index, (label, keyword) in enumerate(chemistry_variables):
            check = ui_construct(QtWidgets.QCheckBox, label)
            self.chem_output_checks[keyword] = check
            chemistry_layout.addWidget(check, index // 3, index % 3)
        variable_layout.addWidget(self.chem_output_group)
        ui_call(form, "addRow", variables)
        content_layout.addWidget(snapshot)
        diagnostics = ui_construct(
            QtWidgets.QGroupBox, msg("text.2.mass.and.conservation.diagnostics")
        )
        diagnostics_layout = QtWidgets.QVBoxLayout(diagnostics)
        flags = QtWidgets.QWidget()
        flags_layout = QtWidgets.QHBoxLayout(flags)
        flags_layout.setContentsMargins(0, 0, 0, 0)
        self.mass_flowrate = ui_construct(
            QtWidgets.QCheckBox, msg("text.output.mass.flow.rate.mass.flowrate")
        )
        self.mass_balance = ui_construct(
            QtWidgets.QCheckBox, msg("text.output.mass.balance.mass.balance")
        )
        self.mass_flowrate.setChecked(True)
        self.mass_balance.setChecked(True)
        flags_layout.addWidget(self.mass_flowrate)
        flags_layout.addWidget(self.mass_balance)
        flags_layout.addStretch()
        diagnostics_layout.addWidget(flags)
        self.mass_balance_file_group = ui_construct(
            QtWidgets.QGroupBox, msg("text.write.mass.balance.file.mass.balance.file")
        )
        self.mass_balance_file_group.setCheckable(True)
        self.mass_balance_file_group.setChecked(False)
        mass_form = QtWidgets.QFormLayout(self.mass_balance_file_group)
        schedule_editor(mass_form, "mass_balance_file", 1)
        self.mass_balance_file_regions = mark_verbatim(ui_construct(QtWidgets.QLineEdit))
        self.mass_balance_file_regions.setReadOnly(True)
        choose_mass_regions = ui_construct(QtWidgets.QPushButton, msg("text.select.region"))
        mass_region_row = QtWidgets.QWidget()
        mass_region_layout = QtWidgets.QHBoxLayout(mass_region_row)
        mass_region_layout.setContentsMargins(0, 0, 0, 0)
        mass_region_layout.addWidget(self.mass_balance_file_regions, 1)
        mass_region_layout.addWidget(choose_mass_regions)
        ui_call(mass_form, "addRow", msg("text.reporting.region"), mass_region_row)
        mass_scope = QtWidgets.QWidget()
        mass_scope_layout = QtWidgets.QHBoxLayout(mass_scope)
        mass_scope_layout.setContentsMargins(0, 0, 0, 0)
        self.mass_balance_file_print_initial = ui_construct(
            QtWidgets.QCheckBox, msg("text.output.initial.state")
        )
        self.mass_balance_file_print_final = ui_construct(
            QtWidgets.QCheckBox, msg("text.output.final.state")
        )
        self.mass_balance_file_print_initial.setChecked(True)
        self.mass_balance_file_print_final.setChecked(True)
        mass_scope_layout.addWidget(self.mass_balance_file_print_initial)
        mass_scope_layout.addWidget(self.mass_balance_file_print_final)
        mass_scope_layout.addStretch()
        ui_call(mass_form, "addRow", msg("text.output.scope"), mass_scope)
        diagnostics_layout.addWidget(self.mass_balance_file_group)
        content_layout.addWidget(diagnostics)
        self.observation_group = ui_construct(
            QtWidgets.QGroupBox, msg("text.3.observation.file.observation.file")
        )
        self.observation_group.setCheckable(True)
        self.observation_group.setChecked(False)
        observation_form = QtWidgets.QFormLayout(self.observation_group)
        self.observation_regions = mark_verbatim(ui_construct(QtWidgets.QLineEdit))
        self.observation_regions.setReadOnly(True)
        choose_observations = ui_construct(
            QtWidgets.QPushButton, msg("text.select.observation.point")
        )
        observation_row = QtWidgets.QWidget()
        observation_layout = QtWidgets.QHBoxLayout(observation_row)
        observation_layout.setContentsMargins(0, 0, 0, 0)
        observation_layout.addWidget(self.observation_regions, 1)
        observation_layout.addWidget(choose_observations)
        ui_call(observation_form, "addRow", msg("text.observation.point"), observation_row)
        schedule_editor(observation_form, "observation", 1)
        observation_scope = QtWidgets.QWidget()
        observation_scope_layout = QtWidgets.QHBoxLayout(observation_scope)
        observation_scope_layout.setContentsMargins(0, 0, 0, 0)
        self.observation_print_initial = ui_construct(
            QtWidgets.QCheckBox, msg("text.output.initial.state")
        )
        self.observation_print_final = ui_construct(
            QtWidgets.QCheckBox, msg("text.output.final.state")
        )
        self.observation_print_initial.setChecked(True)
        self.observation_print_final.setChecked(True)
        observation_scope_layout.addWidget(self.observation_print_initial)
        observation_scope_layout.addWidget(self.observation_print_final)
        observation_scope_layout.addStretch()
        ui_call(observation_form, "addRow", msg("text.output.scope"), observation_scope)
        self.observation_flow_output_checks = {
            key: ui_construct(QtWidgets.QCheckBox, self)
            for key in vars(model.FlowOutputVariables())
            if key != "velocity"
        }
        self.observation_chem_output_checks = {
            key: ui_construct(QtWidgets.QCheckBox, self)
            for key in vars(model.ChemistryOutputVariables())
        }
        for check in (
            *self.observation_flow_output_checks.values(),
            *self.observation_chem_output_checks.values(),
        ):
            check.hide()
        self.observation_output_summary = ui_construct(
            QtWidgets.QLabel, msg("text.no.variable.selected")
        )
        self.observation_output_summary.setObjectName("MutedLabel")
        choose_observation_outputs = ui_construct(
            QtWidgets.QPushButton, msg("text.select.output.variables")
        )
        observation_variable_row = QtWidgets.QWidget()
        observation_variable_layout = QtWidgets.QHBoxLayout(observation_variable_row)
        observation_variable_layout.setContentsMargins(0, 0, 0, 0)
        observation_variable_layout.addWidget(self.observation_output_summary, 1)
        observation_variable_layout.addWidget(choose_observation_outputs)
        ui_call(observation_form, "addRow", msg("text.output.variables"), observation_variable_row)
        content_layout.addWidget(self.observation_group)
        records = ui_construct(QtWidgets.QGroupBox, msg("text.run.and.reproducibility"))
        records_form = QtWidgets.QFormLayout(records)
        self.save_final_input = ui_construct(
            QtWidgets.QCheckBox, msg("text.save.final.pflotran.input.record")
        )
        self.save_final_input.setChecked(True)
        ui_call(self.save_final_input, "setText", msg("repair.export_final_input"))
        ui_call(self.save_final_input, "setToolTip", msg("repair.export_final_input_note"))
        ui_call(records_form, "addRow", msg("text.run.record"), self.save_final_input)
        content_layout.addWidget(records)
        review_group = ui_construct(QtWidgets.QGroupBox, msg("text.model.integrity.checks"))
        review_layout = QtWidgets.QVBoxLayout(review_group)
        self.review_summary = ui_construct(QtWidgets.QLabel)
        self.review_summary.setObjectName("SectionTitle")
        self.review_output = QtWidgets.QTreeWidget()
        self.review_output.setProperty("fracrtm_label_columns", [0, 1, 2])
        ui_call(
            self.review_output,
            "setHeaderLabels",
            [msg("text.severity"), msg("text.location"), msg("text.issue")],
        )
        self.review_output.header().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        review_layout.addWidget(self.review_summary)
        review_layout.addWidget(self.review_output, 1)
        content_layout.addWidget(review_group, 1)
        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        choose_mass_regions.clicked.connect(self._choose_mass_balance_regions)
        choose_observations.clicked.connect(self._choose_observation_regions)
        choose_observation_outputs.clicked.connect(self._choose_observation_outputs)
        self.review_output.itemDoubleClicked.connect(self._jump_to_review_item)
        tracked = (
            self.output_format,
            self.output_mode,
            self.output_interval,
            self.output_interval_unit,
            self.output_times,
            self.output_times_unit,
            self.output_timestep_interval,
            self.print_initial,
            self.print_final,
            self.velocity_at_center,
            self.mass_flowrate,
            self.mass_balance,
            self.mass_balance_file_group,
            self.mass_balance_file_mode,
            self.mass_balance_file_interval,
            self.mass_balance_file_interval_unit,
            self.mass_balance_file_times,
            self.mass_balance_file_times_unit,
            self.mass_balance_file_timestep_interval,
            self.mass_balance_file_print_initial,
            self.mass_balance_file_print_final,
            self.observation_group,
            self.observation_mode,
            self.observation_interval,
            self.observation_interval_unit,
            self.observation_times,
            self.observation_times_unit,
            self.observation_timestep_interval,
            self.observation_print_initial,
            self.observation_print_final,
            self.save_final_input,
            *self.flow_output_checks.values(),
            *self.chem_output_checks.values(),
            *self.observation_flow_output_checks.values(),
            *self.observation_chem_output_checks.values(),
        )
        for widget in tracked:
            signal = (
                getattr(widget, "toggled", None)
                or getattr(widget, "textChanged", None)
                or getattr(widget, "valueChanged", None)
                or getattr(widget, "currentIndexChanged", None)
                or getattr(widget, "toggled", None)
            )
            if signal is not None:
                signal.connect(self._mark_dirty)
        self._update_schedule_stack("output")
        self._update_schedule_stack("mass_balance_file")
        self._update_schedule_stack("observation")
        self._refresh_observation_output_summary()
        self.flow_output_group.hide()
        self.chem_output_group.hide()
        owner = self.flow_output_group.parentWidget()
        holder = QtWidgets.QWidget(owner)
        holder_layout = QtWidgets.QHBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        self.output_variable_summary = ui_construct(QtWidgets.QLabel)
        self.output_variable_summary.setObjectName("MutedLabel")
        choose = ui_construct(QtWidgets.QPushButton, msg("text.select.output.variables"))
        choose.clicked.connect(self._choose_outputs)
        holder_layout.addWidget(self.output_variable_summary, 1)
        holder_layout.addWidget(choose)
        owner.layout().addWidget(holder)
        for _, check in self._output_checks():
            check.toggled.connect(lambda _: self._refresh_output_summary())
        self._refresh_output_summary()

    def _build_materials(self) -> None:
        layout = self._page(
            msg("text.materials"),
            msg(
                "text.select.a.material.on.the.left.and.set.its.basic.hydraulic.and.transport.properties"
            ),
        )
        self.global_transport_group = ui_construct(
            QtWidgets.QGroupBox, msg("text.global.transport.parameters")
        )
        global_transport_form = QtWidgets.QFormLayout(self.global_transport_group)
        self.diffusion_coefficient = ScientificValueEdit(None, optional=True)
        ui_call(
            global_transport_form,
            "addRow",
            msg("text.molecular.diffusion.coefficient"),
            self._unit_row(self.diffusion_coefficient, "m²/s"),
        )
        layout.addWidget(self.global_transport_group)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        left = ui_construct(QtWidgets.QGroupBox, msg("text.material.list"))
        left_layout = QtWidgets.QVBoxLayout(left)
        self.materials = QtWidgets.QListWidget()
        self.materials.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.materials.setMinimumWidth(230)
        self.materials.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.materials.setTextElideMode(QtCore.Qt.ElideRight)
        left_layout.addWidget(self.materials)
        material_buttons = QtWidgets.QHBoxLayout()
        self.add_material_button = ui_construct(QtWidgets.QPushButton, msg("text.add.material"))
        self.delete_material_button = ui_construct(
            QtWidgets.QPushButton, msg("text.delete.material")
        )
        self.add_material_button.clicked.connect(self._add_material)
        self.delete_material_button.clicked.connect(self._remove_material)
        material_buttons.addWidget(self.add_material_button)
        material_buttons.addWidget(self.delete_material_button)
        left_layout.addLayout(material_buttons)
        right = QtWidgets.QScrollArea()
        right.setWidgetResizable(True)
        right.setFrameShape(QtWidgets.QFrame.NoFrame)
        editor = QtWidgets.QWidget()
        editor_layout = QtWidgets.QVBoxLayout(editor)
        basic = ui_construct(QtWidgets.QGroupBox, msg("text.material.identity"))
        basic_form = QtWidgets.QFormLayout(basic)
        self.material_name = ui_construct(QtWidgets.QLineEdit)
        self.material_numeric_id = QtWidgets.QSpinBox()
        self.material_numeric_id.setRange(1, 999999)
        ui_call(basic_form, "addRow", msg("text.material.name"), self.material_name)
        ui_call(basic_form, "addRow", msg("text.numeric.material.id"), self.material_numeric_id)
        hydraulic = ui_construct(QtWidgets.QGroupBox, msg("text.basic.properties"))
        hydraulic_form = QtWidgets.QFormLayout(hydraulic)
        self.porosity_source_combo = QtWidgets.QComboBox()
        ui_call(self.porosity_source_combo, "addItem", msg("text.constant"), "constant")
        ui_call(self.porosity_source_combo, "addItem", msg("text.dataset"), "dataset")
        self.porosity_stack = QtWidgets.QStackedWidget()
        porosity_constant_page = QtWidgets.QWidget()
        porosity_constant_form = QtWidgets.QFormLayout(porosity_constant_page)
        porosity_constant_form.setContentsMargins(0, 0, 0, 0)
        self.porosity_value_spin = CompactDoubleSpinBox()
        self.porosity_value_spin.setRange(1e-12, 1.0)
        ui_call(
            porosity_constant_form, "addRow", msg("text.porosity.value"), self.porosity_value_spin
        )
        porosity_dataset_page = QtWidgets.QWidget()
        porosity_dataset_form = QtWidgets.QFormLayout(porosity_dataset_page)
        porosity_dataset_form.setContentsMargins(0, 0, 0, 0)
        self.porosity_dataset_combo = QtWidgets.QComboBox()
        self.porosity_dataset_combo.setEditable(False)
        ui_call(
            porosity_dataset_form,
            "addRow",
            msg("text.porosity.dataset"),
            self.porosity_dataset_combo,
        )
        self.porosity_stack.addWidget(porosity_constant_page)
        self.porosity_stack.addWidget(porosity_dataset_page)
        ui_call(hydraulic_form, "addRow", msg("text.porosity.source"), self.porosity_source_combo)
        ui_call(hydraulic_form, "addRow", self.porosity_stack)
        self.permeability_source_combo = QtWidgets.QComboBox()
        ui_call(self.permeability_source_combo, "addItem", msg("text.constant"), "constant")
        ui_call(self.permeability_source_combo, "addItem", msg("text.dataset"), "dataset")
        self.permeability_form_combo = QtWidgets.QComboBox()
        ui_call(self.permeability_form_combo, "addItem", msg("text.isotropic"), "isotropic")
        ui_call(self.permeability_form_combo, "addItem", msg("text.anisotropic"), "anisotropic")
        self.permeability_stack = QtWidgets.QStackedWidget()
        constant_iso_page = QtWidgets.QWidget()
        constant_iso_form = QtWidgets.QFormLayout(constant_iso_page)
        constant_iso_form.setContentsMargins(0, 0, 0, 0)
        self.permeability_k_spin = ScientificDoubleSpinBox()
        ui_call(
            constant_iso_form,
            "addRow",
            msg("text.permeability.k"),
            self._unit_row(self.permeability_k_spin, "m²"),
        )
        constant_aniso_page = QtWidgets.QWidget()
        constant_aniso_form = QtWidgets.QFormLayout(constant_aniso_page)
        constant_aniso_form.setContentsMargins(0, 0, 0, 0)
        self.permeability_kx_spin = ScientificDoubleSpinBox()
        self.permeability_ky_spin = ScientificDoubleSpinBox()
        self.permeability_kz_spin = ScientificDoubleSpinBox()
        ui_call(
            constant_aniso_form,
            "addRow",
            msg("text.x.direction.permeability.kx"),
            self._unit_row(self.permeability_kx_spin, "m²"),
        )
        ui_call(
            constant_aniso_form,
            "addRow",
            msg("text.y.direction.permeability.ky"),
            self._unit_row(self.permeability_ky_spin, "m²"),
        )
        ui_call(
            constant_aniso_form,
            "addRow",
            msg("text.z.direction.permeability.kz"),
            self._unit_row(self.permeability_kz_spin, "m²"),
        )
        dataset_iso_page = QtWidgets.QWidget()
        dataset_iso_form = QtWidgets.QFormLayout(dataset_iso_page)
        dataset_iso_form.setContentsMargins(0, 0, 0, 0)
        self.permeability_dataset_combo = QtWidgets.QComboBox()
        ui_call(
            dataset_iso_form,
            "addRow",
            msg("text.permeability.dataset"),
            self.permeability_dataset_combo,
        )
        dataset_aniso_page = QtWidgets.QWidget()
        dataset_aniso_form = QtWidgets.QFormLayout(dataset_aniso_page)
        dataset_aniso_form.setContentsMargins(0, 0, 0, 0)
        self.permeability_dataset_x_combo = QtWidgets.QComboBox()
        self.permeability_dataset_y_combo = QtWidgets.QComboBox()
        self.permeability_dataset_z_combo = QtWidgets.QComboBox()
        ui_call(
            dataset_aniso_form,
            "addRow",
            msg("text.x.direction.dataset"),
            self.permeability_dataset_x_combo,
        )
        ui_call(
            dataset_aniso_form,
            "addRow",
            msg("text.y.direction.dataset"),
            self.permeability_dataset_y_combo,
        )
        ui_call(
            dataset_aniso_form,
            "addRow",
            msg("text.z.direction.dataset"),
            self.permeability_dataset_z_combo,
        )
        for page in (constant_iso_page, constant_aniso_page, dataset_iso_page, dataset_aniso_page):
            self.permeability_stack.addWidget(page)
        ui_call(
            hydraulic_form,
            "addRow",
            msg("text.permeability.source"),
            self.permeability_source_combo,
        )
        ui_call(
            hydraulic_form, "addRow", msg("text.permeability.form"), self.permeability_form_combo
        )
        ui_call(hydraulic_form, "addRow", self.permeability_stack)
        self.transport_group = ui_construct(QtWidgets.QGroupBox, msg("text.transport.properties"))
        transport_form = QtWidgets.QFormLayout(self.transport_group)
        self.tortuosity = CompactDoubleSpinBox()
        self.tortuosity.setRange(1e-12, 1000000000000.0)
        self.longitudinal_disp = CompactDoubleSpinBox()
        self.longitudinal_disp.setRange(0, 1000000000000.0)
        self.transverse_h_disp = CompactDoubleSpinBox()
        self.transverse_h_disp.setRange(0, 1000000000000.0)
        self.transverse_v_disp = CompactDoubleSpinBox()
        self.transverse_v_disp.setRange(0, 1000000000000.0)
        ui_call(
            transport_form, "addRow", msg("text.tortuosity"), self._unit_row(self.tortuosity, "—")
        )
        ui_call(
            transport_form,
            "addRow",
            msg("text.longitudinal.dispersivity"),
            self._unit_row(self.longitudinal_disp, "m"),
        )
        ui_call(
            transport_form,
            "addRow",
            msg("text.horizontal.transverse.dispersivity"),
            self._unit_row(self.transverse_h_disp, "m"),
        )
        ui_call(
            transport_form,
            "addRow",
            msg("text.vertical.transverse.dispersivity"),
            self._unit_row(self.transverse_v_disp, "m"),
        )
        self.curve_group = ui_construct(QtWidgets.QGroupBox, msg("text.characteristic.curves"))
        curve_form = QtWidgets.QFormLayout(self.curve_group)
        self.curve_type_combo = QtWidgets.QComboBox()
        ui_call(self.curve_type_combo, "addItem", "DEFAULT", "default")
        ui_call(self.curve_type_combo, "addItem", "van Genuchten", "van_genuchten")
        ui_call(self.curve_type_combo, "addItem", "Brooks–Corey", "brooks_corey")
        self.vg_parameter_panel = QtWidgets.QWidget()
        vg_form = QtWidgets.QFormLayout(self.vg_parameter_panel)
        vg_form.setContentsMargins(0, 0, 0, 0)
        self.vg_alpha = CompactDoubleSpinBox()
        self._scientific_spin(self.vg_alpha)
        self.vg_m = CompactDoubleSpinBox()
        self.vg_m.setRange(1e-08, 0.99999999)
        self.vg_residual = CompactDoubleSpinBox()
        self.vg_residual.setRange(0, 0.99999999)
        self.vg_max_capillary = ScientificValueEdit(None, optional=True)
        self.curve_permeability_function = QtWidgets.QComboBox()
        self.curve_permeability_function.addItems(["MUALEM", "BURDINE"])
        self.curve_smooth = QtWidgets.QCheckBox()
        ui_call(self.curve_smooth, "setToolTip", msg("repair.smooth_scope"))
        ui_call(
            vg_form, "addRow", msg("repair.relative_permeability"), self.curve_permeability_function
        )
        ui_call(vg_form, "addRow", msg("repair.smooth_curve"), self.curve_smooth)
        ui_call(vg_form, "addRow", "α", self.vg_alpha)
        self.curve_shape_label = QtWidgets.QLabel("m")
        ui_call(vg_form, "addRow", self.curve_shape_label, self.vg_m)
        ui_call(vg_form, "addRow", msg("text.residual.liquid.saturation"), self.vg_residual)
        ui_call(
            vg_form,
            "addRow",
            msg("text.maximum.capillary.pressure.advanced"),
            self._unit_row(self.vg_max_capillary, "Pa"),
        )
        ui_call(curve_form, "addRow", msg("text.characteristic.curve.type"), self.curve_type_combo)
        ui_call(curve_form, "addRow", self.vg_parameter_panel)
        columns = QtWidgets.QHBoxLayout()
        property_column = QtWidgets.QVBoxLayout()
        curve_column = QtWidgets.QVBoxLayout()
        property_column.addWidget(basic)
        property_column.addWidget(hydraulic)
        property_column.addWidget(self.transport_group)
        curve_column.addWidget(self.curve_group)
        curve_column.addStretch(1)
        columns.addLayout(property_column, 1)
        columns.addLayout(curve_column, 1)
        editor_layout.addLayout(columns)
        right.setWidget(editor)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([280, 800])
        layout.addWidget(splitter, 1)
        self.materials.itemSelectionChanged.connect(self._material_selection_changed)
        self.porosity_source_combo.currentIndexChanged.connect(self._update_porosity_editor)
        self.permeability_source_combo.currentIndexChanged.connect(self._update_permeability_editor)
        self.permeability_form_combo.currentIndexChanged.connect(self._update_permeability_editor)
        self.curve_type_combo.currentIndexChanged.connect(self._update_curve_ui)
        for widget in (
            self.material_name,
            self.material_numeric_id,
            self.porosity_source_combo,
            self.porosity_value_spin,
            self.porosity_dataset_combo,
            self.permeability_source_combo,
            self.permeability_form_combo,
            self.permeability_k_spin,
            self.permeability_kx_spin,
            self.permeability_ky_spin,
            self.permeability_kz_spin,
            self.permeability_dataset_combo,
            self.permeability_dataset_x_combo,
            self.permeability_dataset_y_combo,
            self.permeability_dataset_z_combo,
            self.tortuosity,
            self.longitudinal_disp,
            self.transverse_h_disp,
            self.transverse_v_disp,
            self.curve_type_combo,
            self.vg_alpha,
            self.vg_m,
            self.vg_residual,
            self.vg_max_capillary,
            self.curve_permeability_function,
            self.curve_smooth,
        ):
            signal = (
                getattr(widget, "textChanged", None)
                or getattr(widget, "valueChanged", None)
                or getattr(widget, "currentIndexChanged", None)
            )
            if signal is not None:
                signal.connect(self._material_editor_changed)
        self.diffusion_coefficient.textChanged.connect(self._mark_dirty)
