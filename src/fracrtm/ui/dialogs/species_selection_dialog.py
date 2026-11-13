from __future__ import annotations

from PyQt5 import QtCore, QtWidgets

from fracrtm.config.contracts import (
    CONSTRAINT_TYPES,
    MINERAL_SURFACE_UNITS,
    canonical_constraint_type,
)
from fracrtm.config.pflotran import (
    ChemicalConstraintConfig,
    ConcentrationConstraint,
    MineralConstraint,
)
from fracrtm.messages import exception_text
from fracrtm.messages import message as msg
from fracrtm.ui.localization import format_message, ui_call, ui_construct
from fracrtm.ui.qt_helpers import LocalizedDialog, MessageBox
from fracrtm.ui.styles import DIALOG_STYLE

COMMON_PRIMARY_SPECIES = (
    "H+",
    "HCO3-",
    "O2(aq)",
    "HS-",
    "Na+",
    "K+",
    "Ca++",
    "Mg++",
    "Cl-",
    "SO4--",
    "NO3-",
    "F-",
    "HPO4--",
    "Fe++",
    "Fe+++",
    "Al+++",
    "Mn++",
    "Cu++",
    "Zn++",
    "Ni++",
    "Pb++",
    "Cd++",
    "SiO2(aq)",
    "UO2++",
    "Tracer",
    "Tracer2",
    "Tracer3",
)
COMMON_MINERALS = (
    "Calcite",
    "Dolomite",
    "Gypsum",
    "Pyrite",
    "Goethite",
    "Ferrihydrite",
    "Gibbsite",
    "Siderite",
    "Quartz",
    "Kaolinite",
    "Albite",
    "K-Feldspar",
)
COMMON_GASES = ("CO2(g)", "O2(g)", "H2(g)", "H2S(g)", "CH4(g)", "N2(g)", "NH3(g)")


class SpeciesSelectionDialog(LocalizedDialog):
    def __init__(
        self,
        mode: str,
        database_species: list[str],
        initial_selected_species: list[str],
        primary_species: list[str] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        if mode not in {"primary", "secondary", "gas"}:
            raise ValueError(msg("validation.mode.must.be.primary.secondary.or.gas"))
        self.mode = mode
        self._database_species = list(dict.fromkeys(database_species))
        self._selected = set(initial_selected_species)
        self._primary_species = list(primary_species or [])
        titles = {
            "primary": msg("text.select.primary.species"),
            "secondary": msg("text.select.secondary.species"),
            "gas": msg("text.select.gases"),
        }
        ui_call(self, "setWindowTitle", titles[mode])
        self.resize(560, 620)
        self.setStyleSheet(DIALOG_STYLE)
        layout = QtWidgets.QVBoxLayout(self)
        title = ui_construct(QtWidgets.QLabel, titles[mode])
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        self.search = ui_construct(QtWidgets.QLineEdit)
        ui_call(self.search, "setPlaceholderText", msg("text.search.species"))
        layout.addWidget(self.search)
        self.list = QtWidgets.QListWidget()
        layout.addWidget(self.list, 1)
        self.count = ui_construct(QtWidgets.QLabel)
        self.count.setObjectName("MutedLabel")
        layout.addWidget(self.count)
        actions = QtWidgets.QHBoxLayout()
        self.clear_button = ui_construct(QtWidgets.QPushButton, msg("text.clear"))
        actions.addStretch()
        actions.addWidget(self.clear_button)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        actions.addWidget(buttons)
        layout.addLayout(actions)
        self.search.textChanged.connect(self._filter)
        self.list.itemChanged.connect(self._item_changed)
        self.clear_button.clicked.connect(self._clear)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._load()

    def _load(self) -> None:
        preferred = (
            COMMON_PRIMARY_SPECIES
            if self.mode == "primary"
            else COMMON_GASES
            if self.mode == "gas"
            else ()
        )
        ordered = [name for name in preferred if name in self._database_species]
        ordered += [name for name in self._database_species if name not in ordered]
        self.list.blockSignals(True)
        self.list.clear()
        for species in ordered:
            item = ui_construct(QtWidgets.QListWidgetItem, species)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(
                QtCore.Qt.Checked if species in self._selected else QtCore.Qt.Unchecked
            )
            ui_call(self.list, "addItem", item)
        self.list.blockSignals(False)
        self._filter("")
        self._update_count()

    def _filter(self, text: str) -> None:
        needle = text.strip().casefold()
        for index in range(self.list.count()):
            item = self.list.item(index)
            if needle:
                hidden = needle not in item.text().casefold()
            elif self.mode == "primary":
                hidden = (
                    item.text() not in COMMON_PRIMARY_SPECIES and item.text() not in self._selected
                )
            else:
                hidden = False
            item.setHidden(hidden)

    def _item_changed(self, item) -> None:
        if item.checkState() == QtCore.Qt.Checked:
            self._selected.add(item.text())
        else:
            self._selected.discard(item.text())
        self._update_count()

    def _clear(self) -> None:
        self._selected.clear()
        self.list.blockSignals(True)
        for index in range(self.list.count()):
            self.list.item(index).setCheckState(QtCore.Qt.Unchecked)
        self.list.blockSignals(False)
        self._update_count()

    def _update_count(self, *args) -> None:
        ui_call(
            self.count, "setText", format_message("selection.species", count=len(self._selected))
        )

    def selected_species(self) -> list[str]:
        return [name for name in self._database_species if name in self._selected]


class MineralSelectionDialog(LocalizedDialog):
    def __init__(self, database_minerals: list[str], initial_selected: list[str], parent=None):
        super().__init__(parent)
        self._database = list(dict.fromkeys(database_minerals))
        self._selected = set(initial_selected)
        ui_call(self, "setWindowTitle", msg("dialog.select_minerals.title"))
        self.resize(650, 650)
        self.setStyleSheet(DIALOG_STYLE)
        layout = QtWidgets.QVBoxLayout(self)
        note = ui_construct(
            QtWidgets.QLabel,
            msg("text.select.reactive.minerals.from.the.current.thermodynamic.database"),
        )
        note.setObjectName("PageDescription")
        layout.addWidget(note)
        self.search = ui_construct(QtWidgets.QLineEdit)
        ui_call(self.search, "setPlaceholderText", msg("text.search.database.minerals"))
        layout.addWidget(self.search)
        common_group = ui_construct(QtWidgets.QGroupBox, msg("text.common.minerals"))
        self.common_layout = QtWidgets.QGridLayout(common_group)
        self.common_checks = {}
        for index, mineral in enumerate(
            (name for name in COMMON_MINERALS if name in self._database)
        ):
            check = ui_construct(QtWidgets.QCheckBox, mineral)
            check.setChecked(mineral in self._selected)
            check.toggled.connect(lambda checked, name=mineral: self._toggle(name, checked))
            self.common_layout.addWidget(check, index // 2, index % 2)
            self.common_checks[mineral] = check
        layout.addWidget(common_group)
        self.results_group = ui_construct(QtWidgets.QGroupBox, msg("text.database.search.results"))
        results_layout = QtWidgets.QVBoxLayout(self.results_group)
        self.results = QtWidgets.QListWidget()
        self.results.itemChanged.connect(self._result_changed)
        results_layout.addWidget(self.results)
        self.results_group.setVisible(False)
        layout.addWidget(self.results_group, 1)
        self.count = ui_construct(QtWidgets.QLabel)
        self.count.setObjectName("MutedLabel")
        layout.addWidget(self.count)
        actions = QtWidgets.QHBoxLayout()
        clear = ui_construct(QtWidgets.QPushButton, msg("text.clear"))
        actions.addStretch()
        actions.addWidget(clear)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        actions.addWidget(buttons)
        layout.addLayout(actions)
        self.search.textChanged.connect(self._refresh_results)
        clear.clicked.connect(self._clear)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._update_count()

    def _toggle(self, name, checked):
        if checked:
            self._selected.add(name)
        else:
            self._selected.discard(name)
        self._update_count()

    def _refresh_results(self, text):
        needle = text.strip().casefold()
        self.results_group.setVisible(bool(needle))
        self.results.blockSignals(True)
        self.results.clear()
        if needle:
            for name in self._database:
                if name in self.common_checks or needle not in name.casefold():
                    continue
                item = ui_construct(QtWidgets.QListWidgetItem, name)
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                item.setCheckState(
                    QtCore.Qt.Checked if name in self._selected else QtCore.Qt.Unchecked
                )
                ui_call(self.results, "addItem", item)
        self.results.blockSignals(False)

    def _result_changed(self, item):
        self._toggle(item.text(), item.checkState() == QtCore.Qt.Checked)

    def _clear(self):
        self._selected.clear()
        for check in self.common_checks.values():
            check.blockSignals(True)
            check.setChecked(False)
            check.blockSignals(False)
        self._refresh_results(self.search.text())
        self._update_count()

    def _update_count(self):
        ui_call(
            self.count, "setText", format_message("selection.minerals", count=len(self._selected))
        )

    def selected_minerals(self):
        return [name for name in self._database if name in self._selected]


class ChemicalConstraintEditorDialog(LocalizedDialog):
    def __init__(
        self,
        constraint: ChemicalConstraintConfig,
        species: list[str],
        minerals: list[str],
        parent=None,
    ):
        super().__init__(parent)
        self._source = constraint
        self._species = species
        self._minerals = minerals
        ui_call(self, "setWindowTitle", msg("text.edit.aqueous.solution"))
        self.resize(850, 680)
        self.setStyleSheet(DIALOG_STYLE)
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        title = ui_construct(QtWidgets.QLabel, msg("text.aqueous.solution"))
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        identity = QtWidgets.QFormLayout()
        self.object_id = ui_construct(QtWidgets.QLineEdit)
        self.name = ui_construct(QtWidgets.QLineEdit)
        ui_call(identity, "addRow", msg("text.object.id"), self.object_id)
        ui_call(identity, "addRow", msg("text.solution.name"), self.name)
        layout.addLayout(identity)
        tabs = QtWidgets.QTabWidget()
        layout.addWidget(tabs, 1)
        water = QtWidgets.QWidget()
        water_layout = QtWidgets.QVBoxLayout(water)
        water_note = ui_construct(
            QtWidgets.QLabel,
            msg(
                "text.select.species.used.in.this.solution.then.set.values.and.constraint.types.p.denotes"
            ),
        )
        water_note.setObjectName("MutedLabel")
        water_note.setWordWrap(True)
        water_layout.addWidget(water_note)
        self.concentrations = QtWidgets.QTableWidget(0, 4)
        ui_call(
            self.concentrations,
            "setHorizontalHeaderLabels",
            [
                msg("text.use.species"),
                msg("input.numeric_value"),
                msg("text.constraint.type"),
                msg("text.associated.species.or.mineral"),
            ],
        )
        self.concentrations.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        water_layout.addWidget(self.concentrations)
        ui_call(tabs, "addTab", water, msg("text.aqueous.species"))
        mineral_page = QtWidgets.QWidget()
        mineral_layout = QtWidgets.QVBoxLayout(mineral_page)
        self.mineral_table = QtWidgets.QTableWidget(0, 4)
        ui_call(
            self.mineral_table,
            "setHorizontalHeaderLabels",
            [
                msg("text.use.mineral"),
                msg("text.initial.volume.fraction"),
                msg("text.specific.surface.area"),
                msg("text.unit"),
            ],
        )
        self.mineral_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        mineral_layout.addWidget(self.mineral_table)
        ui_call(tabs, "addTab", mineral_page, msg("text.initial.minerals"))
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load(self) -> None:
        ui_call(self.object_id, "setText", self._source.id)
        ui_call(self.name, "setText", self._source.name)
        concentrations = {item.species: item for item in self._source.concentrations}
        self.concentrations.setRowCount(len(self._species))
        for row, species in enumerate(self._species):
            current = concentrations.get(species)
            item = ui_construct(QtWidgets.QTableWidgetItem, species)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked if current else QtCore.Qt.Unchecked)
            self.concentrations.setItem(row, 0, item)
            value = ui_construct(QtWidgets.QLineEdit, str(current.value if current else 0.0))
            self.concentrations.setCellWidget(row, 1, value)
            kind = QtWidgets.QComboBox()
            ui_call(kind, "addItems", CONSTRAINT_TYPES)
            value = canonical_constraint_type(current.constraint_type) if current else "T"
            if kind.findText(value) < 0:
                kind.addItem(value)
            kind.setCurrentText(value)
            self.concentrations.setCellWidget(row, 2, kind)
            associated = QtWidgets.QComboBox()
            associated.setEditable(True)
            ui_call(associated, "addItem", "")
            ui_call(associated, "addItems", ["CO2(g)", "O2(g)"] + self._minerals)
            associated.setCurrentText(
                current.associated_species if current and current.associated_species else ""
            )
            self.concentrations.setCellWidget(row, 3, associated)
        minerals = {item.mineral_id: item for item in self._source.minerals}
        self.mineral_table.setRowCount(len(self._minerals))
        for row, mineral in enumerate(self._minerals):
            current = minerals.get(mineral)
            item = ui_construct(QtWidgets.QTableWidgetItem, mineral)
            item.setData(QtCore.Qt.UserRole, mineral)
            item.setData(QtCore.Qt.UserRole + 1, current.surface_area_unit if current else "m2/m3")
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked if current else QtCore.Qt.Unchecked)
            self.mineral_table.setItem(row, 0, item)
            self.mineral_table.setCellWidget(
                row,
                1,
                ui_construct(QtWidgets.QLineEdit, str(current.volume_fraction if current else 0.0)),
            )
            self.mineral_table.setCellWidget(
                row,
                2,
                ui_construct(
                    QtWidgets.QLineEdit, str(current.specific_surface_area if current else 0.0)
                ),
            )
            unit = QtWidgets.QComboBox()
            for label in MINERAL_SURFACE_UNITS:
                unit.addItem(label, label)
            selected_unit = current.surface_area_unit if current else "m2/m3"
            if unit.findData(selected_unit) < 0:
                unit.addItem(selected_unit, selected_unit)
            unit.setCurrentIndex(unit.findData(selected_unit))
            self.mineral_table.setCellWidget(row, 3, unit)

    def _accept(self) -> None:
        try:
            self.result_constraint()
        except ValueError as exc:
            MessageBox.warning(
                self, msg("validation.invalid.aqueous.solution"), exception_text(exc)
            )
            return
        self.accept()

    def result_constraint(self) -> ChemicalConstraintConfig:
        concentrations = []
        for row in range(self.concentrations.rowCount()):
            item = self.concentrations.item(row, 0)
            if item.checkState() != QtCore.Qt.Checked:
                continue
            try:
                value = float(self.concentrations.cellWidget(row, 1).text())
            except ValueError as exc:
                raise ValueError(
                    format_message("species.invalid_value", species=item.text())
                ) from exc
            kind = self.concentrations.cellWidget(row, 2).currentText()
            associated = self.concentrations.cellWidget(row, 3).currentText().strip() or None
            if kind in {"G", "M"} and (not associated):
                raise ValueError(
                    format_message("constraint.association", species=item.text(), kind=kind)
                )
            concentrations.append(ConcentrationConstraint(item.text(), value, kind, associated))
        minerals = []
        for row in range(self.mineral_table.rowCount()):
            item = self.mineral_table.item(row, 0)
            if item.checkState() != QtCore.Qt.Checked:
                continue
            try:
                volume = float(self.mineral_table.cellWidget(row, 1).text())
                surface = float(self.mineral_table.cellWidget(row, 2).text())
            except ValueError as exc:
                raise ValueError(
                    format_message("mineral.invalid_initial", name=item.text())
                ) from exc
            minerals.append(
                MineralConstraint(
                    item.data(QtCore.Qt.UserRole),
                    volume,
                    surface,
                    self.mineral_table.cellWidget(row, 3).currentData(),
                )
            )
        return ChemicalConstraintConfig(
            self.object_id.text().strip(), self.name.text().strip(), concentrations, minerals
        )
