from __future__ import annotations

import os

from PyQt5 import QtCore, QtGui, QtWidgets

from fracrtm.messages import join_text
from fracrtm.messages import message as msg
from fracrtm.project import ArtifactState
from fracrtm.ui.localization import format_message, ui_call, ui_construct

from .localization import LANGUAGE_NAMES, SUPPORTED_LOCALES, current_locale, tr
from .qt_helpers import AdaptiveDoubleSpinBox
from .styles import UiMetrics


def _overview_grid_layout():
    layout = QtWidgets.QGridLayout()
    layout.setContentsMargins(0, 4, 0, 4)
    layout.setHorizontalSpacing(UiMetrics.OVERVIEW_COLUMN_SPACING)
    layout.setVerticalSpacing(UiMetrics.OVERVIEW_ROW_SPACING)
    layout.setColumnMinimumWidth(0, UiMetrics.OVERVIEW_LABEL_WIDTH)
    layout.setColumnStretch(1, 1)
    layout.setProperty("overview_next_row", 0)
    return layout


def _overview_add_row(layout, label, value):
    row = int(layout.property("overview_next_row") or 0)
    label_widget = ui_construct(QtWidgets.QLabel, label)
    label_widget.setMinimumWidth(UiMetrics.OVERVIEW_LABEL_WIDTH)
    layout.addWidget(label_widget, row, 0, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
    layout.addWidget(value, row, 1, QtCore.Qt.AlignVCenter)
    layout.setProperty("overview_next_row", row + 1)


def _spatial_summary_layout():
    layout = QtWidgets.QGridLayout()
    layout.setContentsMargins(0, 4, 0, 4)
    layout.setHorizontalSpacing(UiMetrics.SPATIAL_COLUMN_SPACING)
    layout.setVerticalSpacing(UiMetrics.SPATIAL_ROW_SPACING)
    layout.setColumnMinimumWidth(0, UiMetrics.SPATIAL_LABEL_WIDTH)
    layout.setColumnStretch(1, 1)
    layout.setProperty("spatial_next_row", 0)
    return layout


def _spatial_summary_add_row(layout, label, value):
    row = int(layout.property("spatial_next_row") or 0)
    label_widget = ui_construct(QtWidgets.QLabel, label)
    label_widget.setMinimumWidth(UiMetrics.SPATIAL_LABEL_WIDTH)
    layout.addWidget(label_widget, row, 0, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
    layout.addWidget(
        ui_construct(QtWidgets.QLabel, value), row, 1, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter
    )
    layout.setProperty("spatial_next_row", row + 1)


class MainWindowPages:
    def _overview_page(self):
        page, layout = self._page(msg("nav.project_overview"))
        card, content = self._card(msg("text.project.information"))
        form = _overview_grid_layout()
        self.project_name = ui_construct(QtWidgets.QLabel)
        self.project_path = ui_construct(QtWidgets.QLabel)
        self.project_path.setObjectName("Monospace")
        _overview_add_row(form, msg("text.project.name"), self.project_name)
        _overview_add_row(form, msg("text.project.location"), self.project_path)
        content.addLayout(form)
        actions = QtWidgets.QHBoxLayout()
        open_dir = ui_construct(QtWidgets.QPushButton, msg("text.open.project.directory"))
        self.open_project_directory_button = open_dir
        open_dir.clicked.connect(self._open_current_project_directory)
        choose = ui_construct(QtWidgets.QPushButton, msg("text.switch.project"))
        choose.clicked.connect(self._choose_project)
        actions.addWidget(open_dir)
        actions.addWidget(choose)
        actions.addStretch(1)
        content.addLayout(actions)
        layout.addWidget(card)
        workflow, content = self._card(msg("text.workflow.status"))
        self.overview_form = _overview_grid_layout()
        content.addLayout(self.overview_form)
        layout.addWidget(workflow)
        recent, content = self._card(msg("text.most.recent.simulation"))
        self.recent_status = ui_construct(QtWidgets.QLabel, msg("text.no.simulation.record"))
        self.recent_run_id = ui_construct(QtWidgets.QLabel, "—")
        self._recent_task = None
        self.recent_runtime = ui_construct(QtWidgets.QLabel, "—")
        self.recent_finished_at = ui_construct(QtWidgets.QLabel, "—")
        self.recent_final_time = ui_construct(QtWidgets.QLabel, "—")
        self.recent_results_path = ui_construct(QtWidgets.QLabel, "—")
        self.recent_results_path.setObjectName("Monospace")
        recent_form = _overview_grid_layout()
        _overview_add_row(recent_form, msg("text.status"), self.recent_status)
        _overview_add_row(recent_form, msg("text.run.duration"), self.recent_runtime)
        _overview_add_row(recent_form, msg("text.simulation.time"), self.recent_final_time)
        content.addLayout(recent_form)
        self.recent_details = QtWidgets.QWidget()
        details_form = _overview_grid_layout()
        self.recent_details.setLayout(details_form)
        _overview_add_row(details_form, msg("text.run.id"), self.recent_run_id)
        _overview_add_row(details_form, msg("text.run.finished.at.utc"), self.recent_finished_at)
        _overview_add_row(details_form, msg("text.result.location"), self.recent_results_path)
        self.recent_run_id.setWordWrap(True)
        content.addWidget(self.recent_details)
        self.recent_details.hide()
        layout.addWidget(recent)
        layout.addStretch(1)
        card_layout = self.recent_status.parentWidget().layout()
        actions = QtWidgets.QHBoxLayout()
        actions.setContentsMargins(0, UiMetrics.OVERVIEW_ACTION_SPACING, 0, 0)
        actions.setSpacing(10)
        view_log = ui_construct(QtWidgets.QPushButton, msg("text.view.log"))
        self.recent_log_button = view_log
        view_log.clicked.connect(lambda: self._workflow().show_recent_log())
        actions.addWidget(view_log)
        self.recent_results_button = ui_construct(QtWidgets.QPushButton, msg("text.open.results"))
        self.recent_results_button.clicked.connect(lambda: self._workflow().open_recent_results())
        actions.addWidget(self.recent_results_button)
        self.recent_details_button = ui_construct(QtWidgets.QPushButton, msg("text.details"))
        self.recent_details_button.setCheckable(True)
        self.recent_details_button.toggled.connect(self.recent_details.setVisible)
        actions.addWidget(self.recent_details_button)
        for button in (view_log, self.recent_results_button, self.recent_details_button):
            button.setEnabled(False)
        actions.addStretch(1)
        card_layout.addLayout(actions)
        return page

    def _pflotran_page(self):
        page, layout = self._page(msg("nav.pflotran_model"))
        config, content = self._card(msg("pflotran.configuration"))
        self.pf_state = ui_construct(QtWidgets.QLabel)
        self.pf_process_summary = ui_construct(QtWidgets.QLabel)
        self.pf_reaction_summary = ui_construct(QtWidgets.QLabel)
        self.pf_duration_summary = ui_construct(QtWidgets.QLabel)
        self.pf_dependency_summary = ui_construct(QtWidgets.QLabel)
        form = QtWidgets.QFormLayout()
        ui_call(form, "addRow", msg("text.model.status"), self.pf_state)
        ui_call(form, "addRow", msg("text.flow.process"), self.pf_process_summary)
        ui_call(form, "addRow", msg("text.transport.process"), self.pf_reaction_summary)
        ui_call(form, "addRow", msg("text.simulation.duration"), self.pf_duration_summary)
        ui_call(form, "addRow", msg("text.model.dependencies"), self.pf_dependency_summary)
        content.addLayout(form)
        open_model = ui_construct(QtWidgets.QPushButton, msg("pflotran.configuration"))
        open_model.clicked.connect(self.open_pflotran_dialog)
        content.addWidget(open_model, 0, QtCore.Qt.AlignRight)
        layout.addWidget(config)
        input_card, content = self._card(msg("text.pflotran.input.deck"))
        self.pf_input_summary = ui_construct(QtWidgets.QLabel)
        content.addWidget(self.pf_input_summary)
        actions = QtWidgets.QHBoxLayout()
        check = ui_construct(QtWidgets.QPushButton, msg("text.validate.model"))
        self.check_pf = check
        check.clicked.connect(self._check_pflotran)
        self.generate_pf = ui_construct(QtWidgets.QPushButton, msg("text.generate.input.deck"))
        self.generate_pf.setObjectName("PrimaryButton")
        self.generate_pf.clicked.connect(self.prepare_pflotran_model)
        actions.addWidget(check)
        actions.addStretch(1)
        actions.addWidget(self.generate_pf)
        content.addLayout(actions)
        layout.addWidget(input_card)
        layout.addStretch(1)
        self.pf_chemistry_summary = ui_construct(QtWidgets.QLabel)
        ui_call(form, "insertRow", 3, msg("text.chemical.system"), self.pf_chemistry_summary)
        return page

    def _run_page(self):
        page, layout = self._page(msg("nav.simulation_run"))
        card, content = self._card(msg("nav.simulation_run"))
        form = QtWidgets.QFormLayout()
        self.sim_status = ui_construct(QtWidgets.QLabel)
        self.run_runtime = ui_construct(QtWidgets.QLabel, "—")
        self.run_final_time = ui_construct(QtWidgets.QLabel, "—")
        self.run_input_file = ui_construct(QtWidgets.QLabel, "model.in")
        ui_call(form, "addRow", msg("pflotran.input"), self.run_input_file)
        ui_call(form, "addRow", msg("text.status"), self.sim_status)
        ui_call(form, "addRow", msg("text.elapsed.time"), self.run_runtime)
        ui_call(form, "addRow", msg("text.final.simulation.time"), self.run_final_time)
        content.addLayout(form)
        actions = QtWidgets.QHBoxLayout()
        log = ui_construct(QtWidgets.QPushButton, msg("text.view.log"))
        self.run_log_button = log
        log.clicked.connect(lambda: self._workflow().show_task_log())
        self.run_pf = ui_construct(QtWidgets.QPushButton, msg("text.start.simulation"))
        self.run_pf.setObjectName("PrimaryButton")
        self.run_pf.clicked.connect(self.run_pflotran)
        actions.addWidget(log)
        actions.addStretch(1)
        actions.addWidget(self.run_pf)
        content.addLayout(actions)
        layout.addWidget(card)
        layout.addStretch(1)
        self.run_mpi_processes = QtWidgets.QSpinBox()
        self.run_mpi_processes.setRange(1, 4096)
        self.run_mpi_processes.setValue(1)
        ui_call(form, "insertRow", 1, msg("text.mpi.processes"), self.run_mpi_processes)
        self.run_available_processors_label = ui_construct(
            QtWidgets.QLabel, msg("text.not.checked")
        )
        ui_call(
            form,
            "insertRow",
            2,
            msg("text.available.processors"),
            self.run_available_processors_label,
        )
        self.stop_pf = ui_construct(QtWidgets.QPushButton, msg("text.stop.simulation"))
        self.stop_pf.clicked.connect(self._stop_pflotran)
        actions.insertWidget(max(0, actions.count() - 1), self.stop_pf)
        self.retry_sync_button = ui_construct(
            QtWidgets.QPushButton, msg("text.retry.synchronization")
        )
        self.retry_sync_button.setObjectName("retrySynchronization")
        self.retry_sync_button.clicked.connect(
            lambda: self._run_action(self._workflow().choose_resync)
        )
        actions.insertWidget(1, self.retry_sync_button)
        self.recovery_button = ui_construct(
            QtWidgets.QPushButton, msg("text.check.interrupted.task")
        )
        self.recovery_button.setObjectName("inspectInterruptedTask")
        self.recovery_button.clicked.connect(
            lambda: self._run_action(self._workflow().choose_recovery)
        )
        actions.insertWidget(2, self.recovery_button)
        return page

    def _settings_page(self):
        page, layout = self._page(msg("nav.runtime"))
        wsl, content = self._card("WSL")
        form = QtWidgets.QFormLayout()
        self.wsl_distribution = ui_construct(QtWidgets.QLineEdit, self.settings.distribution)
        self.wsl_user = ui_construct(QtWidgets.QLineEdit, self.settings.wsl_user)
        self.workspace_root = ui_construct(QtWidgets.QLineEdit, self.settings.wsl_workspace_root)
        self.wsl_python = ui_construct(QtWidgets.QLineEdit, self.settings.dfn_python)
        self.dfn_home = ui_construct(QtWidgets.QLineEdit, self.settings.dfnworks_home)
        for widget in (self.wsl_python, self.dfn_home):
            widget.setObjectName("Monospace")
        ui_call(form, "addRow", msg("text.wsl.distribution"), self.wsl_distribution)
        ui_call(form, "addRow", msg("text.wsl.user"), self.wsl_user)
        ui_call(form, "addRow", msg("repair.wsl_workspace"), self.workspace_root)
        ui_call(form, "addRow", msg("text.wsl.python"), self.wsl_python)
        ui_call(form, "addRow", msg("text.dfnworks.home"), self.dfn_home)
        content.addLayout(form)
        layout.addWidget(wsl)
        pflotran, content = self._card("PFLOTRAN")
        form = QtWidgets.QFormLayout()
        self.pf_executable = ui_construct(QtWidgets.QLineEdit, self.settings.pflotran_executable)
        self.mpi_launcher = ui_construct(QtWidgets.QLineEdit, self.settings.mpi_launcher)
        self.pf_executable.setObjectName("Monospace")
        self.mpi_launcher.setObjectName("Monospace")
        ui_call(form, "addRow", msg("text.pflotran.executable"), self.pf_executable)
        ui_call(form, "addRow", msg("text.mpi.launcher"), self.mpi_launcher)
        content.addLayout(form)
        layout.addWidget(pflotran)
        resources, content = self._card(msg("text.compute.resources"))
        form = QtWidgets.QFormLayout()
        self.available_processors_label = ui_construct(QtWidgets.QLabel, msg("text.not.checked"))
        self.default_dfn_processes = QtWidgets.QSpinBox()
        self.default_dfn_processes.setRange(1, max(1, os.cpu_count() or 1))
        self.default_dfn_processes.setValue(min(4, self.default_dfn_processes.maximum()))
        self.dfn_cpu = self.default_dfn_processes
        self.pf_processes = QtWidgets.QSpinBox()
        self.pf_processes.setRange(1, 4096)
        self.pf_processes.setValue(1)
        self.allow_oversubscribe = ui_construct(
            QtWidgets.QCheckBox, msg("text.allow.mpi.oversubscription")
        )
        ui_call(
            form, "addRow", msg("text.detected.processor.count"), self.available_processors_label
        )
        ui_call(
            form, "addRow", msg("text.default.dfn.parallel.processes"), self.default_dfn_processes
        )
        ui_call(form, "addRow", msg("text.default.pflotran.mpi.processes"), self.pf_processes)
        ui_call(form, "addRow", "", self.allow_oversubscribe)
        content.addLayout(form)
        actions = QtWidgets.QHBoxLayout()
        test = ui_construct(QtWidgets.QPushButton, msg("text.check.environment"))
        self.check_environment_button = test
        test.clicked.connect(self._test_runtime_environment)
        save = ui_construct(QtWidgets.QPushButton, msg("text.save.settings"))
        save.setObjectName("PrimaryButton")
        save.clicked.connect(self._save_settings)
        actions.addWidget(test)
        actions.addStretch(1)
        actions.addWidget(save)
        content.addLayout(actions)
        layout.addWidget(resources)
        layout.addStretch(1)
        content_layout = page.widget().layout()
        language_card, language_layout = self._card(tr("settings.language"))
        language_form = QtWidgets.QFormLayout()
        self.language_combo = QtWidgets.QComboBox()
        for locale in SUPPORTED_LOCALES:
            ui_call(self.language_combo, "addItem", LANGUAGE_NAMES[locale], locale)
        selected = self.language_combo.findData(current_locale())
        self.language_combo.setCurrentIndex(max(0, selected))
        ui_call(language_form, "addRow", tr("settings.language"), self.language_combo)
        language_layout.addLayout(language_form)
        language_layout.addWidget(ui_construct(QtWidgets.QLabel, tr("language.restart_hint")))
        content_layout.insertWidget(1, language_card)
        self.language_combo.currentIndexChanged.connect(self._change_ui_language)
        if hasattr(self, "run_mpi_processes"):
            self.run_mpi_processes.setValue(self.pf_processes.value())
            self.run_mpi_processes.valueChanged.connect(self.pf_processes.setValue)
            self.pf_processes.valueChanged.connect(self.run_mpi_processes.setValue)
        if hasattr(self, "run_available_processors_label"):
            ui_call(self.run_available_processors_label, "setText", msg("text.not.checked"))
        self.environment_status = ui_construct(QtWidgets.QLabel, msg("text.not.checked"))
        self.environment_status.setObjectName("MutedLabel")
        resource_card = self.available_processors_label.parentWidget()
        resource_card.layout().insertWidget(0, self.environment_status)
        return page

    def _card(self, title):
        card = QtWidgets.QFrame()
        card.setObjectName("Card")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(
            UiMetrics.CARD_PADDING,
            UiMetrics.CARD_PADDING,
            UiMetrics.CARD_PADDING,
            UiMetrics.CARD_PADDING,
        )
        layout.setSpacing(UiMetrics.OVERVIEW_ROW_SPACING)
        heading = ui_construct(QtWidgets.QLabel, title)
        heading.setObjectName("SectionTitle")
        layout.addWidget(heading)
        return (card, layout)

    def _page(self, title):
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        content = QtWidgets.QWidget()
        content.setObjectName("PageContent")
        layout = QtWidgets.QVBoxLayout(content)
        layout.setContentsMargins(
            UiMetrics.PAGE_MARGIN,
            UiMetrics.PAGE_MARGIN,
            UiMetrics.PAGE_MARGIN,
            UiMetrics.PAGE_MARGIN,
        )
        layout.setSpacing(UiMetrics.CARD_GAP)
        heading = ui_construct(QtWidgets.QLabel, title)
        heading.setObjectName("PageTitle")
        layout.addWidget(heading)
        scroll.setWidget(content)
        return (scroll, layout)

    def _add_nav(self, layout, label, page):
        index = self.page_stack.count()
        button = ui_construct(QtWidgets.QPushButton, label)
        button.setObjectName("NavButton")
        button.setCheckable(True)
        button.clicked.connect(lambda checked, i=index: self._switch(i))
        layout.addWidget(button)
        self.nav_buttons.append(button)
        self.page_stack.addWidget(page)

    def _build_ui(self):
        self.resize(UiMetrics.MAIN_WINDOW_WIDTH, UiMetrics.MAIN_WINDOW_HEIGHT)
        self.setMinimumSize(UiMetrics.MINIMUM_WINDOW_WIDTH, UiMetrics.MINIMUM_WINDOW_HEIGHT)
        root = QtWidgets.QWidget()
        root.setObjectName("MainContent")
        self.setCentralWidget(root)
        root_layout = QtWidgets.QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        body = QtWidgets.QHBoxLayout()
        body.setSpacing(0)
        root_layout.addLayout(body, 1)
        sidebar = QtWidgets.QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(UiMetrics.SIDEBAR_WIDTH)
        side = QtWidgets.QVBoxLayout(sidebar)
        side.setContentsMargins(16, 24, 16, 18)
        side.setSpacing(2)
        brand = ui_construct(QtWidgets.QLabel, "FracRTM")
        brand.setObjectName("Brand")
        side.addWidget(brand)
        side.addSpacing(18)
        self.page_stack = QtWidgets.QStackedWidget()
        self.nav_buttons = []
        specs = (
            (msg("text.project"), msg("nav.project_overview"), self._overview_page, 0),
            (msg("text.modeling"), msg("nav.spatial_model"), self._dfn_page, 14),
            (None, msg("nav.pflotran_model"), self._pflotran_page, 0),
            (msg("text.simulation"), msg("nav.simulation_run"), self._run_page, 14),
            (None, msg("nav.results"), self._visual_page, 0),
        )
        for group, label, builder, before_group_spacing in specs:
            if group:
                if before_group_spacing:
                    side.addSpacing(before_group_spacing)
                head = ui_construct(QtWidgets.QLabel, group)
                head.setObjectName("SideHead")
                side.addWidget(head)
            self._add_nav(side, label, builder())
        side.addStretch(1)
        self._add_nav(side, msg("nav.runtime"), self._settings_page())
        body.addWidget(sidebar)
        body.addWidget(self.page_stack, 1)
        status = QtWidgets.QFrame()
        status.setStyleSheet("background:white;border-top:1px solid #D7DEE2")
        row = QtWidgets.QHBoxLayout(status)
        row.setContentsMargins(16, 6, 16, 6)
        self.bottom_status = ui_construct(QtWidgets.QLabel, msg("status.ready"))
        row.addWidget(self.bottom_status)
        row.addStretch(1)
        root_layout.addWidget(status)
        self._switch(0)

    def _dfn_page(self):
        page, layout = self._page(msg("nav.spatial_model"))
        dfn, content = self._card(msg("spatial.fractures"))
        self.dfn_status = ui_construct(QtWidgets.QLabel)
        self.dfn_summary = _spatial_summary_layout()
        content.addWidget(self.dfn_status)
        content.addLayout(self.dfn_summary)
        actions = QtWidgets.QHBoxLayout()
        configure = ui_construct(QtWidgets.QPushButton, msg("text.configuration"))
        configure.clicked.connect(self.open_dfn_dialog)
        self.run_button = ui_construct(QtWidgets.QPushButton, msg("text.generate"))
        self.run_button.setObjectName("PrimaryButton")
        self.run_button.clicked.connect(self.run_dfn)
        actions.addStretch(1)
        actions.addWidget(configure)
        actions.addWidget(self.run_button)
        content.addLayout(actions)
        layout.addWidget(dfn)
        continuum, content = self._card(msg("text.udfm.effective.medium"))
        form = QtWidgets.QFormLayout()
        self.base_cell = AdaptiveDoubleSpinBox()
        self.base_cell.setRange(1e-06, 1000000000.0)
        self.base_cell.setDecimals(12)
        if self._continuum_config.base_cell_size is None:
            self.base_cell.setSpecialValueText("\u200b")
            self.base_cell.setValue(self.base_cell.minimum())
        else:
            self.base_cell.setValue(self._continuum_config.base_cell_size)
        self.refinement = QtWidgets.QSpinBox()
        self.refinement.setRange(1, 12)
        if self._continuum_config.refinement_levels is None:
            self.refinement.setSpecialValueText("\u200b")
            self.refinement.setValue(self.refinement.minimum())
        else:
            self.refinement.setValue(self._continuum_config.refinement_levels)
        self.minimum_cell = ui_construct(QtWidgets.QLabel)
        self.matrix_perm = ui_construct(
            QtWidgets.QLineEdit,
            ""
            if self._continuum_config.matrix_permeability is None
            else f"{self._continuum_config.matrix_permeability:g}",
        )
        validator = QtGui.QDoubleValidator(0.0, 1e100, 16, self.matrix_perm)
        validator.setNotation(QtGui.QDoubleValidator.ScientificNotation)
        self.matrix_perm.setValidator(validator)
        self.matrix_porosity = AdaptiveDoubleSpinBox()
        self.matrix_porosity.setRange(0.0, 1.0)
        self.matrix_porosity.setDecimals(12)
        if self._continuum_config.matrix_porosity is None:
            self.matrix_porosity.setSpecialValueText("\u200b")
            self.matrix_porosity.setValue(self.matrix_porosity.minimum())
        else:
            self.matrix_porosity.setValue(self._continuum_config.matrix_porosity)
        ui_call(form, "addRow", msg("text.level.0.cell.size"), self.base_cell)
        ui_call(form, "addRow", msg("mesh.refinement_levels"), self.refinement)
        ui_call(form, "addRow", msg("text.minimum.cell.size"), self.minimum_cell)
        area_row = QtWidgets.QWidget()
        area_layout = QtWidgets.QHBoxLayout(area_row)
        area_layout.setContentsMargins(0, 0, 0, 0)
        self.boundary_area_mode = QtWidgets.QComboBox()
        ui_call(
            self.boundary_area_mode, "addItem", msg("boundary_area.tool_default"), "tool_default"
        )
        ui_call(self.boundary_area_mode, "addItem", msg("boundary_area.custom"), "custom")
        self.boundary_area_value = QtWidgets.QLineEdit()
        self.boundary_area_default = ui_construct(QtWidgets.QLabel, msg("boundary_area.unknown"))
        self.boundary_area_default.setWordWrap(True)
        for widget in (
            self.boundary_area_mode,
            self.boundary_area_value,
            self.boundary_area_default,
        ):
            area_layout.addWidget(widget)
            ui_call(widget, "setToolTip", msg("boundary_area.help"))
        self._load_boundary_area_controls()
        ui_call(form, "addRow", msg("boundary_area.label"), area_row)
        ui_call(form, "addRow", msg("text.matrix.permeability.m"), self.matrix_perm)
        ui_call(form, "addRow", msg("text.matrix.porosity"), self.matrix_porosity)
        content.addLayout(form)
        self.udfm_button = ui_construct(QtWidgets.QPushButton, msg("action.generate_udfm"))
        self.udfm_button.setObjectName("PrimaryButton")
        self.udfm_button.clicked.connect(self._run_udfm)
        content.addWidget(self.udfm_button, 0, QtCore.Qt.AlignRight)
        layout.addWidget(continuum)
        layout.addStretch(1)
        self.base_cell.valueChanged.connect(self._octree_changed)
        self.refinement.valueChanged.connect(self._octree_changed)
        self.matrix_perm.textChanged.connect(self._upscale_changed)
        self.matrix_porosity.valueChanged.connect(self._upscale_changed)
        self.boundary_area_mode.currentIndexChanged.connect(self._boundary_area_changed)
        self.boundary_area_value.textChanged.connect(self._boundary_area_changed)
        return page

    def _visual_page(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        self.visual_layout = layout
        layout.setContentsMargins(
            UiMetrics.PAGE_MARGIN,
            UiMetrics.PAGE_MARGIN,
            UiMetrics.PAGE_MARGIN,
            UiMetrics.PAGE_MARGIN,
        )
        heading = ui_construct(QtWidgets.QLabel, msg("nav.results"))
        heading.setObjectName("PageTitle")
        layout.addWidget(heading)
        self.results_workspace = None
        message = ui_construct(
            QtWidgets.QLabel,
            msg("text.create.or.open.a.project.before.viewing.results.and.visualization.data"),
        )
        message.setAlignment(QtCore.Qt.AlignCenter)
        self._no_project_visual_message = message
        layout.addWidget(message, 1)
        self.regenerate_visualization_button = ui_construct(
            QtWidgets.QPushButton, msg("text.regenerate.selected.visualization")
        )
        self.regenerate_visualization_button.clicked.connect(self.regenerate_pflotran_visualization)
        page.layout().insertWidget(1, self.regenerate_visualization_button)
        return page

    def _switch(self, index):
        self.page_stack.setCurrentIndex(index)
        for item, button in enumerate(self.nav_buttons):
            button.setChecked(item == index)
        if index == 4:
            self._ensure_results_workspace()
        if index == 5 and self._available_processors is None:
            self._refresh_available_processors()

    def _refresh_pflotran_summary(self):
        report = self._pflotran_config.validate(self.workspace.local_root)
        if report.errors:
            ui_call(
                self.pf_state,
                "setText",
                format_message("validation.error_badge", count=len(report.errors)),
            )
            ui_call(
                self.pf_dependency_summary,
                "setText",
                format_message("validation.missing_badge", count=len(report.errors)),
            )
        elif report.warnings:
            ui_call(
                self.pf_state,
                "setText",
                format_message("validation.warning_badge", count=len(report.warnings)),
            )
            ui_call(self.pf_dependency_summary, "setText", msg("validation.check.required"))
        else:
            ui_call(self.pf_state, "setText", msg("text.checks.passed"))
            ui_call(self.pf_dependency_summary, "setText", msg("status.ready_badge"))
        simulation = self._pflotran_config.simulation
        ui_call(
            self.pf_process_summary, "setText", simulation.flow_mode or msg("status.not_enabled")
        )
        ui_call(
            self.pf_reaction_summary,
            "setText",
            simulation.transport_mode or msg("status.not_enabled"),
        )
        final_time = self._pflotran_config.time.final_time
        final_unit = self._pflotran_config.time.final_unit
        ui_call(
            self.pf_duration_summary,
            "setText",
            "—"
            if final_time is None
            else join_text("", [f"{final_time:g}", " ", final_unit or ""]).strip(),
        )
        input_state = self._state(self.state.pflotran_input_state)
        ui_call(self.pf_input_summary, "setText", format_message("input.status", state=input_state))

    def _overview(self):
        if self.workspace is not None:
            self._update_workflow_summary()
            return
        while self.overview_form.count():
            item = self.overview_form.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.overview_form.setProperty("overview_next_row", 0)
        for label in (msg("spatial.fractures"), "UDFM", "PFLOTRAN", msg("results.simulation")):
            state = (
                msg("status.not_generated_badge")
                if label == msg("results.simulation")
                else msg("status.unconfigured_badge")
            )
            _overview_add_row(self.overview_form, label, ui_construct(QtWidgets.QLabel, state))

    def _update_page_buttons(self):
        if self.workspace is not None:
            self._update_project_buttons()
            if hasattr(self, "recent_log_button"):
                self.recent_log_button.setEnabled(bool(self._logs))
            if hasattr(self, "run_log_button"):
                self.run_log_button.setEnabled(bool(self._logs))
            if hasattr(self, "open_project_directory_button"):
                self.open_project_directory_button.setEnabled(True)
            return
        for name in (
            "run_button",
            "udfm_button",
            "generate_pf",
            "run_pf",
            "check_pf",
            "recent_log_button",
            "run_log_button",
            "open_project_directory_button",
        ):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(False)
        if hasattr(self, "stop_pf"):
            self.stop_pf.setVisible(False)
            self.stop_pf.setEnabled(False)

    def _refresh_page_state(self):
        if self.workspace is not None:
            self._refresh_project()
            return
        cfg = self._dfn_config
        ui_call(self.project_name, "setText", "—")
        ui_call(self.project_path, "setText", "—")
        ui_call(self.dfn_status, "setText", self._state(ArtifactState.NOT_GENERATED))
        while self.dfn_summary.count():
            item = self.dfn_summary.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.dfn_summary.setProperty("spatial_next_row", 0)
        for label, value in (
            (msg("text.model.domain"), "—"),
            (msg("text.stochastic.fracture.families"), str(len(cfg.stochastic_families))),
            (msg("text.user.defined.fractures"), str(len(cfg.user_defined_fractures))),
            (msg("text.current.composition"), "—"),
            (msg("text.generation.controls"), "—"),
            (msg("text.orientation.representation"), "—"),
            ("FRAM", "—"),
        ):
            _spatial_summary_add_row(self.dfn_summary, label, value)
        ui_call(self.minimum_cell, "setText", "—")
        ui_call(self.pf_state, "setText", msg("status.unconfigured_badge"))
        ui_call(self.pf_dependency_summary, "setText", "—")
        ui_call(self.pf_process_summary, "setText", "—")
        ui_call(self.pf_reaction_summary, "setText", "—")
        ui_call(self.pf_duration_summary, "setText", "—")
        if hasattr(self, "pf_chemistry_summary"):
            ui_call(self.pf_chemistry_summary, "setText", "—")
        ui_call(self.pf_input_summary, "setText", msg("text.input.file"))
        ui_call(self.sim_status, "setText", msg("status.unconfigured_badge"))
        ui_call(self.run_input_file, "setText", "—")
        ui_call(self.run_runtime, "setText", "—")
        ui_call(self.run_final_time, "setText", "—")
        ui_call(self.recent_status, "setText", "—")
        ui_call(self.recent_runtime, "setText", "—")
        ui_call(self.recent_finished_at, "setText", "—")
        ui_call(self.recent_final_time, "setText", "—")
        ui_call(self.recent_results_path, "setText", "—")
        self._overview()
        self._buttons()

    def _update_workflow_summary(self):
        while self.overview_form.count():
            item = self.overview_form.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.overview_form.setProperty("overview_next_row", 0)
        spatial = self.state.upscale_state
        for label, value in (
            (msg("spatial.fractures"), self.state.dfn_state),
            ("UDFM", spatial),
            ("PFLOTRAN", self.state.pflotran_input_state),
            (msg("results.simulation"), self.state.simulation_state),
        ):
            _overview_add_row(
                self.overview_form, label, ui_construct(QtWidgets.QLabel, self._state(value))
            )
        ui_call(self.recent_status, "setText", self._state(self.state.simulation_state))
        if self.state.simulation_state == ArtifactState.VALID:
            ui_call(
                self.recent_results_path,
                "setText",
                str(self.workspace.local_pflotran_dir / "output"),
            )
        else:
            ui_call(self.recent_results_path, "setText", "—")

    @staticmethod
    def _state(value):
        return {
            ArtifactState.NOT_GENERATED: msg("status.not_generated_badge"),
            ArtifactState.VALID: msg("status.ready_badge"),
            ArtifactState.STALE: msg("text.stale"),
            ArtifactState.RUNNING: msg("status.running_ellipsis"),
            ArtifactState.FAILED: msg("status.failed_badge"),
            ArtifactState.UNVERIFIED: msg("state.unverified"),
        }[value]

    def _refresh_project(self):
        cfg = self._dfn_config
        ui_call(self.project_name, "setText", self.workspace.project_id)
        ui_call(self.project_path, "setText", str(self.workspace.local_root))
        ui_call(self.dfn_status, "setText", self._state(self.state.dfn_state))
        orientation = {
            "dip_strike": msg("text.dip.strike"),
            "theta_phi": "Theta/Phi",
            "trend_plunge": msg("text.trend.plunge"),
        }.get(cfg.orientation_mode, cfg.orientation_mode)
        stop = (
            "P32"
            if cfg.stop_rule.mode == "p32"
            else format_message("fractures.target_count", count=cfg.stop_rule.n_poly)
        )
        while self.dfn_summary.count():
            item = self.dfn_summary.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.dfn_summary.setProperty("spatial_next_row", 0)
        domain = "—"
        if len(cfg.domain_size) == 3 and all((value is not None for value in cfg.domain_size)):
            domain = join_text(
                "",
                [
                    f"{cfg.domain_size[0]:g}",
                    " × ",
                    f"{cfg.domain_size[1]:g}",
                    " × ",
                    f"{cfg.domain_size[2]:g}",
                    " m",
                ],
            )
        summary_rows = [
            (msg("text.model.domain"), domain),
            (msg("text.stochastic.fracture.families"), str(len(cfg.stochastic_families))),
            (msg("text.user.defined.fractures"), str(len(cfg.user_defined_fractures))),
            (msg("text.current.composition"), cfg.composition_label),
        ]
        if cfg.uses_stochastic():
            summary_rows.extend(
                (
                    (msg("text.generation.controls"), stop),
                    (msg("text.orientation.representation"), orientation),
                )
            )
        summary_rows.append(
            ("FRAM", msg("text.enabled") if cfg.fram.enabled else msg("text.disabled"))
        )
        for label, value in summary_rows:
            _spatial_summary_add_row(self.dfn_summary, label, value)
        if (
            self._continuum_config.base_cell_size is None
            or self._continuum_config.refinement_levels is None
        ):
            ui_call(self.minimum_cell, "setText", "—")
        else:
            minimum = self.base_cell.value() / 2 ** self.refinement.value()
            ui_call(self.minimum_cell, "setText", join_text("", [f"{minimum:g}", " m"]))
        self._refresh_pflotran_summary()
        ui_call(self.sim_status, "setText", self._state(self.state.simulation_state))
        self._overview()
        self._buttons()
        chemistry = getattr(self, "pf_chemistry_summary", None)
        if chemistry is not None:
            config = self._pflotran_config
            if not config.simulation.transport_mode:
                ui_call(chemistry, "setText", msg("status.not_enabled"))
            else:
                ui_call(
                    chemistry,
                    "setText",
                    format_message(
                        "chemistry.summary",
                        species=len(config.chemistry.aqueous_species.primary_species),
                        minerals=len(config.chemistry.selected_minerals),
                    ),
                )

    def _update_project_buttons(self):
        running = self._process is not None or bool(self._workers)
        self.run_button.setEnabled(not running)
        self.udfm_button.setEnabled(not running and self.state.dfn_state == ArtifactState.VALID)
        self.generate_pf.setEnabled(not running and self.state.upscale_state == ArtifactState.VALID)
        self.run_pf.setEnabled(
            not running and self.state.pflotran_input_state == ArtifactState.VALID
        )
        running_pf = self._process is not None and self._active_task_type == "pflotran"
        if hasattr(self, "stop_pf"):
            self.stop_pf.setVisible(running_pf)
            self.stop_pf.setEnabled(running_pf)
