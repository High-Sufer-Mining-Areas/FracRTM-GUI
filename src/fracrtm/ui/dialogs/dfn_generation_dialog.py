from __future__ import annotations

import copy
import json
import math
import sys

from PyQt5 import QtCore, QtGui, QtWidgets

from fracrtm.config import BlankConfigFactory
from fracrtm.config.dfn_config import (
    BOUNDARY_FACES,
    ConnectivityConfig,
    DfnConfig,
    FramConfig,
    HydraulicConfig,
    OrientationConfig,
    OutputConfig,
    SizeDistributionConfig,
    StochasticFamilyConfig,
    StopRuleConfig,
    UserDefinedFractureConfig,
    blank_stochastic_family_draft,
)
from fracrtm.messages import exception_text, join_text, prepare_json
from fracrtm.messages import message as msg
from fracrtm.ui.localization import format_message, render_text, ui_call, ui_construct
from fracrtm.ui.qt_helpers import LocalizedDialog, MessageBox
from fracrtm.ui.styles import DIALOG_STYLE, UiMetrics, UiPalette


class AdaptiveDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    def textFromValue(self, value: float) -> str:
        text = f"{value:g}"
        return text.replace("e-0", "e-").replace("e+0", "e+")


ACCENT = UiPalette.ACCENT_TEAL
ACCENT_DARK = "#256A67"
TEXT = UiPalette.TEXT
MUTED = UiPalette.MUTED
BORDER = UiPalette.BORDER
BACKGROUND = UiPalette.PAGE_BACKGROUND
PANEL = UiPalette.CARD_BACKGROUND
ERROR = UiPalette.FAILED
WARNING = UiPalette.STALE


def double_spin(
    minimum: float,
    maximum: float,
    decimals: int = 6,
    step: float = 0.1,
    suffix: str = "",
    compact_display: bool = False,
) -> QtWidgets.QDoubleSpinBox:
    widget = AdaptiveDoubleSpinBox() if compact_display else QtWidgets.QDoubleSpinBox()
    widget.setRange(minimum, maximum)
    widget.setDecimals(decimals)
    widget.setSingleStep(step)
    widget.setKeyboardTracking(False)
    if suffix:
        widget.setSuffix(suffix)
    return widget


def form_row(
    form: QtWidgets.QLayout, label: str, widget: QtWidgets.QWidget, help_text: str = ""
) -> None:
    if help_text:
        ui_call(widget, "setToolTip", help_text)
    if isinstance(form, QtWidgets.QGridLayout):
        row = form.rowCount()
        form.addWidget(ui_construct(QtWidgets.QLabel, label), row, 0)
        form.addWidget(widget, row, 1)
    else:
        ui_call(form, "addRow", label, widget)


class BasePage(QtWidgets.QWidget):
    changed = QtCore.pyqtSignal()

    def __init__(self, title: str, description: str, parent=None):
        super().__init__(parent)
        self.setObjectName("PagePanel")
        self.body_layout = QtWidgets.QVBoxLayout(self)
        self.body_layout.setContentsMargins(28, 24, 28, 24)
        self.body_layout.setSpacing(16)
        title_label = ui_construct(QtWidgets.QLabel, title)
        title_label.setObjectName("PageTitle")
        description_label = ui_construct(QtWidgets.QLabel, description)
        description_label.setObjectName("PageDescription")
        description_label.setWordWrap(True)
        description_label.hide()
        self.body_layout.addWidget(title_label)
        self.body_layout.addWidget(description_label)

    def add_stretch(self) -> None:
        self.body_layout.addStretch(1)


class NullableAdaptiveDoubleSpinBox(AdaptiveDoubleSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_set = False
        self._programmatic = False
        self._display_suffix = ""
        self.unset_text = "—"
        self.valueChanged.connect(self._mark_set_from_value_change)
        self.editingFinished.connect(self._mark_set_from_edit)

    def _mark_set_from_value_change(self, _value: float) -> None:
        if not self._programmatic:
            self._is_set = True
            self._refresh_optional_text()

    def _mark_set_from_edit(self) -> None:
        if self.lineEdit().text().strip() not in ("", self.unset_text):
            self._is_set = True
            self._refresh_optional_text()

    def interpretText(self) -> None:
        entered = self.lineEdit().text().strip()
        super().interpretText()
        if entered not in ("", self.unset_text) and (not self._programmatic):
            self._is_set = True
            self._refresh_optional_text()

    def _refresh_optional_text(self) -> None:
        if not self._is_set and self.unset_text == "":
            self.lineEdit().clear()
            return
        ui_call(self.lineEdit(), "setText", self.textFromValue(self.value()))

    def set_optional_value(self, value: float | None) -> None:
        self._programmatic = True
        try:
            super().setValue(0.0 if value is None else value)
            self._is_set = value is not None
        finally:
            self._programmatic = False
        self._refresh_optional_text()

    def setSuffix(self, suffix: str) -> None:
        self._display_suffix = suffix
        if not self._is_set and self.unset_text == "":
            self.lineEdit().clear()

    def valueFromText(self, text: str) -> float:
        if self._display_suffix and text.endswith(self._display_suffix):
            text = text[: -len(self._display_suffix)].strip()
        return super().valueFromText(text)

    def optional_value(self) -> float | None:
        return self.value() if self._is_set else None

    def textFromValue(self, value: float) -> str:
        if not self._is_set:
            return self.unset_text
        return join_text(
            "", [AdaptiveDoubleSpinBox.textFromValue(self, value), self._display_suffix]
        )


class DomainPage(BasePage):
    def __init__(self, parent=None):
        super().__init__(
            msg("text.domain.and.generation.controls"),
            msg(
                "text.the.dfnworks.domain.is.centered.at.the.origin.a.fixed.positive.integer.seed.makes"
            ),
            parent,
        )
        domain_group = ui_construct(QtWidgets.QGroupBox, msg("text.domain.dimensions.m"))
        domain_form = QtWidgets.QFormLayout(domain_group)
        self.length_x = nullable_spin(0.0, 1000000000.0, 4, 1.0)
        self.length_y = nullable_spin(0.0, 1000000000.0, 4, 1.0)
        self.length_z = nullable_spin(0.0, 1000000000.0, 4, 1.0)
        for widget in (self.length_x, self.length_y, self.length_z):
            widget.unset_text = ""
            widget.set_optional_value(None)
        form_row(domain_form, msg("text.x.direction.length"), self.length_x)
        form_row(domain_form, msg("text.y.direction.length"), self.length_y)
        form_row(domain_form, msg("text.z.direction.length"), self.length_z)
        domain_advanced = ui_construct(
            QtWidgets.QGroupBox, msg("text.generation.domain.extension.m")
        )
        domain_advanced_form = QtWidgets.QFormLayout(domain_advanced)
        self.increase_x = nullable_spin(0.0, 1000000000.0, 5, 0.1)
        self.increase_y = nullable_spin(0.0, 1000000000.0, 5, 0.1)
        self.increase_z = nullable_spin(0.0, 1000000000.0, 5, 0.1)
        for widget in (self.increase_x, self.increase_y, self.increase_z):
            widget.unset_text = ""
            widget.set_optional_value(None)
        ui_call(domain_advanced_form, "addRow", msg("text.x.direction.expansion"), self.increase_x)
        ui_call(domain_advanced_form, "addRow", msg("text.y.direction.expansion"), self.increase_y)
        ui_call(domain_advanced_form, "addRow", msg("text.z.direction.expansion"), self.increase_z)
        note = ui_construct(
            QtWidgets.QLabel,
            msg(
                "text.for.p32.termination.specify.a.p32.target.for.each.stochastic.fracture.family.for.count"
            ),
        )
        note.setObjectName("MutedLabel")
        note.setWordWrap(True)
        self.body_layout.addWidget(domain_group)
        self.body_layout.addWidget(domain_advanced)
        self.add_stretch()
        for widget in (
            self.length_x,
            self.length_y,
            self.length_z,
            self.increase_x,
            self.increase_y,
            self.increase_z,
        ):
            widget.valueChanged.connect(self.changed.emit)

    def load_config(self, config: DfnConfig) -> None:
        for widget, value in zip((self.length_x, self.length_y, self.length_z), config.domain_size):
            widget.set_optional_value(value)
        for widget, value in zip(
            (self.increase_x, self.increase_y, self.increase_z), config.domain_size_increase
        ):
            widget.set_optional_value(value)

    def apply(self, config: DfnConfig) -> None:
        config.domain_size = [
            widget.optional_value() for widget in (self.length_x, self.length_y, self.length_z)
        ]
        config.domain_size_increase = [
            widget.optional_value()
            for widget in (self.increase_x, self.increase_y, self.increase_z)
        ]


class FamilyEditorDialog(LocalizedDialog):
    def __init__(
        self,
        family: StochasticFamilyConfig,
        stop_mode: str | None,
        orientation_mode: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.family = copy.deepcopy(family)
        self.stop_mode = stop_mode
        self.orientation_mode = orientation_mode
        ui_call(self, "setWindowTitle", msg("text.stochastic.fracture.family"))
        self.setMinimumSize(860, 720)
        self.resize(920, 820)
        self.setStyleSheet(
            DIALOG_STYLE
            + "\n            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {\n                min-height: 30px;\n                max-height: 30px;\n            }\n            "
        )
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)
        self.dialog_layout = layout
        content = QtWidgets.QWidget()
        content_layout = QtWidgets.QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        left_column = QtWidgets.QVBoxLayout()
        right_column = QtWidgets.QVBoxLayout()
        left_column.setSpacing(14)
        right_column.setSpacing(14)
        self.left_column_layout = left_column
        content_layout.addLayout(left_column, 1)
        content_layout.addLayout(right_column, 1)
        general = ui_construct(QtWidgets.QGroupBox, msg("text.general.information"))
        self.general_group = general
        general_form = QtWidgets.QFormLayout(general)
        self.name_edit = ui_construct(QtWidgets.QLineEdit)
        self.intensity = nullable_spin(0.0, 1000000000.0, 8, 0.01)
        self.intensity.unset_text = ""
        form_row(general_form, msg("text.fracture.family.name"), self.name_edit)
        self.intensity_label = ui_construct(QtWidgets.QLabel)
        ui_call(general_form, "addRow", self.intensity_label, self.intensity)
        size_group = ui_construct(QtWidgets.QGroupBox, msg("text.size.distribution"))
        self.size_group = size_group
        size_form = QtWidgets.QGridLayout(size_group)
        self.size_form = size_form
        size_form.setContentsMargins(12, 16, 12, 16)
        size_form.setHorizontalSpacing(18)
        size_form.setVerticalSpacing(11)
        size_form.setColumnStretch(1, 1)
        self.distribution = QtWidgets.QComboBox()
        ui_call(self.distribution, "addItem", "...", None)
        ui_call(self.distribution, "addItem", msg("text.truncated.power.law"), "tpl")
        ui_call(self.distribution, "addItem", msg("text.lognormal"), "log_normal")
        ui_call(self.distribution, "addItem", msg("text.exponential.distribution"), "exp")
        ui_call(self.distribution, "addItem", msg("text.fixed.size"), "constant")
        self.alpha = nullable_spin(0.0, 1000.0, 6, 0.1)
        self.log_mean = nullable_spin(-1000000000.0, 1000000000.0, 6, 0.1)
        self.log_std = nullable_spin(0.0, 1000000000.0, 6, 0.1)
        self.exp_mean = nullable_spin(0.0, 1000000000.0, 6, 0.1)
        self.min_radius = nullable_spin(0.0, 1000000000.0, 6, 0.1)
        self.max_radius = nullable_spin(0.0, 1000000000.0, 6, 0.1)
        self.constant_radius = nullable_spin(0.0, 1000000000.0, 6, 0.1)
        for widget in (
            self.alpha,
            self.log_mean,
            self.log_std,
            self.exp_mean,
            self.min_radius,
            self.max_radius,
            self.constant_radius,
        ):
            widget.unset_text = ""
        for widget in (self.distribution, self.alpha, self.min_radius, self.max_radius):
            widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            widget.setMinimumHeight(UiMetrics.INPUT_HEIGHT)
        self.alpha_label = ui_construct(QtWidgets.QLabel, msg("text.power.law.exponent.alpha"))
        self.log_mean_label = ui_construct(QtWidgets.QLabel, msg("text.log.mean"))
        self.log_std_label = ui_construct(QtWidgets.QLabel, msg("text.log.std"))
        self.exp_mean_label = ui_construct(QtWidgets.QLabel, msg("text.exp.mean"))
        self.min_radius_label = ui_construct(QtWidgets.QLabel, msg("text.minimum.radius.m"))
        self.max_radius_label = ui_construct(QtWidgets.QLabel, msg("text.maximum.radius.m"))
        self.constant_radius_label = ui_construct(QtWidgets.QLabel, msg("text.fixed.radius.m"))
        self.distribution_label = ui_construct(QtWidgets.QLabel, msg("text.distribution.type"))
        self.size_rows = {
            "distribution": (self.distribution_label, self.distribution),
            "tpl": (self.alpha_label, self.alpha),
            "log_mean": (self.log_mean_label, self.log_mean),
            "log_std": (self.log_std_label, self.log_std),
            "exp": (self.exp_mean_label, self.exp_mean),
            "min_radius": (self.min_radius_label, self.min_radius),
            "max_radius": (self.max_radius_label, self.max_radius),
            "constant": (self.constant_radius_label, self.constant_radius),
        }
        for row, widgets in enumerate(self.size_rows.values()):
            size_form.addWidget(widgets[0], row, 0)
            size_form.addWidget(widgets[1], row, 1)
        orientation = ui_construct(QtWidgets.QGroupBox, msg("text.orientation.distribution"))
        self.orientation_group = orientation
        orientation_form = QtWidgets.QGridLayout(orientation)
        self.orientation_form = orientation_form
        orientation_form.setColumnStretch(1, 1)
        self.dip = nullable_spin(0.0, 90.0, 3, 1.0, "°")
        self.strike = nullable_spin(0.0, 359.999, 3, 1.0, "°")
        self.aspect = double_spin(1e-08, 1000000000.0, 6, 0.1, compact_display=True)
        self.beta = double_spin(-360.0, 360.0, 3, 1.0, "°", True)
        self.vertices = QtWidgets.QSpinBox()
        self.vertices.setRange(5, 256)
        self.theta = nullable_spin(0.0, 180.0, 3, 1.0, "°")
        self.phi = nullable_spin(0.0, 359.999, 3, 1.0, "°")
        self.trend = nullable_spin(0.0, 359.999, 3, 1.0, "°")
        self.plunge = nullable_spin(0.0, 90.0, 3, 1.0, "°")
        self.kappa = nullable_spin(0.0, 100.0, 3, 1.0)
        for widget in (
            self.dip,
            self.strike,
            self.theta,
            self.phi,
            self.trend,
            self.plunge,
            self.kappa,
        ):
            widget.unset_text = ""
        self.orientation_rows = {}
        for row, (key, label, widget) in enumerate(
            (
                ("dip", msg("text.dip"), self.dip),
                ("strike", msg("text.strike"), self.strike),
                ("theta", "θ", self.theta),
                ("phi", "φ", self.phi),
                ("trend", msg("text.trend"), self.trend),
                ("plunge", msg("text.plunge"), self.plunge),
            )
        ):
            label_widget = ui_construct(QtWidgets.QLabel, label)
            orientation_form.addWidget(label_widget, row, 0)
            orientation_form.addWidget(widget, row, 1)
            self.orientation_rows[key] = (label_widget, widget)
        form_row(
            orientation_form,
            "Kappa",
            self.kappa,
            msg(
                "text.larger.values.concentrate.the.orientations.values.near.zero.give.a.nearly.uniform.orientation.distribution"
            ),
        )
        self.orientation_row_indices = {
            "dip": 0,
            "strike": 1,
            "theta": 2,
            "phi": 3,
            "trend": 4,
            "plunge": 5,
            "kappa": 6,
        }
        hydraulic = ui_construct(QtWidgets.QGroupBox, msg("fracture.hydraulic_aperture"))
        hydraulic_form = QtWidgets.QFormLayout(hydraulic)
        self.hydraulic_model = QtWidgets.QComboBox()
        ui_call(self.hydraulic_model, "addItem", "...", None)
        ui_call(self.hydraulic_model, "addItem", msg("text.constant.aperture"), "constant")
        ui_call(self.hydraulic_model, "addItem", msg("text.radius.correlated.b.r"), "correlated")
        self.constant_aperture = nullable_spin(0.0, 1000.0, 12, 1e-05)
        self.hydraulic_alpha = nullable_spin(0.0, 1000.0, 12, 1e-05)
        self.hydraulic_beta = nullable_spin(-20.0, 20.0, 8, 0.1)
        for widget in (self.constant_aperture, self.hydraulic_alpha, self.hydraulic_beta):
            widget.unset_text = ""
        self.constant_aperture_label = ui_construct(QtWidgets.QLabel, msg("text.aperture.m"))
        self.hydraulic_alpha_label = ui_construct(QtWidgets.QLabel, "Alpha")
        self.hydraulic_beta_label = ui_construct(QtWidgets.QLabel, "Beta")
        ui_call(hydraulic_form, "addRow", msg("text.assignment.method"), self.hydraulic_model)
        ui_call(hydraulic_form, "addRow", self.constant_aperture_label, self.constant_aperture)
        ui_call(hydraulic_form, "addRow", self.hydraulic_alpha_label, self.hydraulic_alpha)
        ui_call(hydraulic_form, "addRow", self.hydraulic_beta_label, self.hydraulic_beta)
        geometry = ui_construct(QtWidgets.QGroupBox, msg("text.geometry.parameters"))
        geometry_form = QtWidgets.QFormLayout(geometry)
        self.geometry_group = geometry
        self.geometry_form = geometry_form
        self.shape = QtWidgets.QComboBox()
        ui_call(self.shape, "addItem", "...", None)
        ui_call(self.shape, "addItem", msg("text.circle.ellipse"), "ell")
        ui_call(self.shape, "addItem", msg("text.rectangle"), "rect")
        self.aspect = double_spin(1e-08, 1000000000.0, 6, 0.1, compact_display=True)
        self.beta_distribution = QtWidgets.QComboBox()
        ui_call(self.beta_distribution, "addItem", msg("text.random.rotation"), 0)
        ui_call(self.beta_distribution, "addItem", msg("text.fixed.angle"), 1)
        self.geometry_beta = double_spin(-360.0, 360.0, 3, 1.0, "°", True)
        self.geometry_beta_label = ui_construct(QtWidgets.QLabel, "Beta")
        self.number_of_points = QtWidgets.QSpinBox()
        self.number_of_points.setRange(5, 256)
        self.shape_label = ui_construct(QtWidgets.QLabel, msg("text.fracture.shape"))
        self.aspect_label = ui_construct(QtWidgets.QLabel, msg("fracture.ellipse_aspect_ratio"))
        self.beta_distribution_label = ui_construct(
            QtWidgets.QLabel, msg("fracture.in_plane_rotation")
        )
        self.number_of_points_label = ui_construct(
            QtWidgets.QLabel, msg("text.fracture.boundary.vertex.count")
        )
        ui_call(geometry_form, "addRow", self.shape_label, self.shape)
        ui_call(geometry_form, "addRow", self.aspect_label, self.aspect)
        ui_call(geometry_form, "addRow", self.beta_distribution_label, self.beta_distribution)
        ui_call(geometry_form, "addRow", self.geometry_beta_label, self.geometry_beta)
        ui_call(geometry_form, "addRow", self.number_of_points_label, self.number_of_points)
        for group in (general, size_group, orientation):
            group.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)
            left_column.addWidget(group, 0)
        left_column.addStretch(1)
        right_column.addWidget(hydraulic)
        right_column.addWidget(geometry)
        right_column.addStretch(1)
        layout.addWidget(content, 1)
        buttons = QtWidgets.QDialogButtonBox()
        ui_call(buttons, "addButton", msg("text.cancel"), QtWidgets.QDialogButtonBox.RejectRole)
        ui_call(buttons, "addButton", msg("text.ok"), QtWidgets.QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.distribution.currentIndexChanged.connect(self._update_visibility)
        self.hydraulic_model.currentIndexChanged.connect(self._update_visibility)
        self.beta_distribution.currentIndexChanged.connect(self._update_visibility)
        self.shape.currentIndexChanged.connect(self._update_visibility)
        self._load()

    def _load(self) -> None:
        family = self.family
        ui_call(self.name_edit, "setText", family.name)
        self._update_intensity_label()
        self.intensity.setMaximum(1.0 if self.stop_mode == "count" else 1000000000.0)
        self.intensity.set_optional_value(
            family.p32
            if self.stop_mode == "p32"
            else family.probability
            if self.stop_mode == "count"
            else None
        )
        self.distribution.setCurrentIndex(
            max(self.distribution.findData(family.size.distribution), 0)
        )
        for widget, value in (
            (self.alpha, family.size.alpha),
            (self.log_mean, family.size.log_mean),
            (self.log_std, family.size.log_std),
            (self.exp_mean, family.size.exp_mean),
            (self.min_radius, family.size.min_radius),
            (self.max_radius, family.size.max_radius),
            (self.constant_radius, family.size.constant_radius),
            (self.dip, family.orientation.dip),
            (self.strike, family.orientation.strike),
            (self.theta, family.orientation.theta),
            (self.phi, family.orientation.phi),
            (self.trend, family.orientation.trend),
            (self.plunge, family.orientation.plunge),
            (self.kappa, family.orientation.kappa),
        ):
            widget.set_optional_value(value)
        self.hydraulic_model.setCurrentIndex(
            max(self.hydraulic_model.findData(family.hydraulic.relationship), 0)
        )
        self.constant_aperture.set_optional_value(family.hydraulic.constant_aperture)
        self.hydraulic_alpha.set_optional_value(family.hydraulic.alpha)
        self.hydraulic_beta.set_optional_value(family.hydraulic.beta)
        self.shape.setCurrentIndex(max(0, self.shape.findData(family.shape)))
        self.aspect.setValue(family.aspect)
        self.beta_distribution.setCurrentIndex(
            max(0, self.beta_distribution.findData(family.beta_distribution))
        )
        self.geometry_beta.setValue(family.beta)
        self.number_of_points.setValue(family.number_of_points)
        self._update_visibility()

    def _update_intensity_label(self) -> None:
        visible = self.stop_mode in {"p32", "count"}
        self.intensity_label.setVisible(visible)
        self.intensity.setVisible(visible)
        if visible:
            ui_call(
                self.intensity_label,
                "setText",
                msg("text.target.p32")
                if self.stop_mode == "p32"
                else msg("text.generation.weight"),
            )

    def _update_visibility(self) -> None:
        distribution = self.distribution.currentData()
        visible_size_rows = {"distribution"}
        if distribution == "tpl":
            visible_size_rows.update(("tpl", "min_radius", "max_radius"))
        elif distribution == "log_normal":
            visible_size_rows.update(("log_mean", "log_std", "min_radius", "max_radius"))
        elif distribution == "exp":
            visible_size_rows.update(("exp", "min_radius", "max_radius"))
        elif distribution == "constant":
            visible_size_rows = {"distribution", "constant"}
        for key, widgets in self.size_rows.items():
            for widget in widgets:
                widget.setVisible(key in visible_size_rows)
        visible_orientation = {
            "dip_strike": {"dip", "strike"},
            "theta_phi": {"theta", "phi"},
            "trend_plunge": {"trend", "plunge"},
        }.get(self.orientation_mode, set())
        for key, widgets in self.orientation_rows.items():
            for widget in widgets:
                widget.setVisible(key in visible_orientation)
        fixed_beta = self.beta_distribution.currentData() == 1
        self.geometry_beta.setVisible(fixed_beta)
        self.geometry_beta_label.setVisible(fixed_beta)
        shape = self.shape.currentData()
        configured_shape = shape in {"ell", "rect"}
        self.aspect.setEnabled(configured_shape)
        self.beta_distribution.setEnabled(configured_shape)
        self.geometry_beta.setEnabled(configured_shape)
        self.number_of_points.setVisible(shape == "ell")
        self.number_of_points_label.setVisible(shape == "ell")
        constant = self.hydraulic_model.currentData() == "constant"
        correlated = self.hydraulic_model.currentData() == "correlated"
        self.constant_aperture.setVisible(constant)
        self.constant_aperture_label.setVisible(constant)
        for widget in (
            self.hydraulic_alpha,
            self.hydraulic_alpha_label,
            self.hydraulic_beta,
            self.hydraulic_beta_label,
        ):
            widget.setVisible(correlated)
        self._refresh_dynamic_group_layouts()

    def _refresh_dynamic_group_layouts(self) -> None:
        for group, form in (
            (self.size_group, self.size_form),
            (self.orientation_group, self.orientation_form),
            (self.geometry_group, self.geometry_form),
        ):
            group.setMinimumHeight(0)
            form.invalidate()
            form.activate()
            group.setMinimumHeight(group.minimumSizeHint().height())
            group.updateGeometry()
        self.left_column_layout.invalidate()
        self.dialog_layout.invalidate()
        QtCore.QTimer.singleShot(0, self._apply_dynamic_group_geometry)

    def _apply_dynamic_group_geometry(self) -> None:
        for group in (self.size_group, self.orientation_group):
            group.updateGeometry()
        self.left_column_layout.activate()

    def _build_family(self) -> StochasticFamilyConfig:
        intensity = self.intensity.optional_value()
        return StochasticFamilyConfig(
            name=self.name_edit.text().strip(),
            size=SizeDistributionConfig(
                distribution=self.distribution.currentData(),
                alpha=self.alpha.optional_value(),
                log_mean=self.log_mean.optional_value(),
                log_std=self.log_std.optional_value(),
                exp_mean=self.exp_mean.optional_value(),
                min_radius=self.min_radius.optional_value(),
                max_radius=self.max_radius.optional_value(),
                constant_radius=self.constant_radius.optional_value(),
            ),
            orientation=OrientationConfig(
                dip=self.dip.optional_value(),
                strike=self.strike.optional_value(),
                theta=self.theta.optional_value(),
                phi=self.phi.optional_value(),
                trend=self.trend.optional_value(),
                plunge=self.plunge.optional_value(),
                kappa=self.kappa.optional_value(),
            ),
            p32=intensity if self.stop_mode == "p32" else self.family.p32,
            probability=intensity if self.stop_mode == "count" else self.family.probability,
            hydraulic=HydraulicConfig(
                relationship=self.hydraulic_model.currentData(),
                constant_aperture=self.constant_aperture.optional_value(),
                alpha=self.hydraulic_alpha.optional_value(),
                beta=self.hydraulic_beta.optional_value(),
            ),
            shape=self.shape.currentData(),
            aspect=self.aspect.value(),
            beta_distribution=self.beta_distribution.currentData(),
            beta=self.geometry_beta.value(),
            number_of_points=self.number_of_points.value(),
        )

    def _accept(self) -> None:
        candidate = self._build_family()
        errors = []
        if self.stop_mode not in {"p32", "count"}:
            errors.append(
                msg("text.select.a.stopping.rule.in.the.stochastic.network.controls.first")
            )
        if self.orientation_mode not in {"dip_strike", "theta_phi", "trend_plunge"}:
            errors.append(msg("text.select.an.orientation.representation"))
        if self.distribution.currentData() is None:
            errors.append(msg("text.select.a.size.distribution"))
        if self.hydraulic_model.currentData() is None:
            errors.append(msg("text.select.an.aperture.model"))
        if self.shape.currentData() is None:
            errors.append(msg("text.select.a.fracture.shape"))
        errors.extend(candidate.validate(self.stop_mode, self.orientation_mode))
        errors = list(dict.fromkeys(errors))
        if errors:
            MessageBox.warning(
                self,
                msg("text.input.needs.correction"),
                join_text("\n", (join_text("", ["• ", error]) for error in errors)),
            )
            return
        self.family = candidate
        self.accept()

    def get_family(self) -> StochasticFamilyConfig:
        return copy.deepcopy(self.family)


class FamiliesPage(BasePage):
    def __init__(self, parent=None, summary_only: bool = False):
        super().__init__(
            msg("text.stochastic.fracture.family"),
            msg(
                "text.each.fracture.family.defines.its.size.orientation.intensity.and.aperture.model.the.ui.converts"
            ),
            parent,
        )
        self.families: list[StochasticFamilyConfig] = []
        self.stop_mode: str | None = None
        self.orientation_mode: str | None = None
        self.summary_only = summary_only
        toolbar = QtWidgets.QHBoxLayout()
        self.add_button = ui_construct(QtWidgets.QPushButton, msg("text.add"))
        self.edit_button = ui_construct(QtWidgets.QPushButton, msg("text.edit"))
        self.copy_button = ui_construct(QtWidgets.QPushButton, msg("text.duplicate"))
        self.delete_button = ui_construct(QtWidgets.QPushButton, msg("text.delete"))
        self.delete_button.setObjectName("DangerButton")
        toolbar.addWidget(self.add_button)
        toolbar.addWidget(self.edit_button)
        toolbar.addWidget(self.copy_button)
        toolbar.addWidget(self.delete_button)
        toolbar.addStretch()
        self.table = QtWidgets.QTableWidget(0, 6)
        ui_call(
            self.table,
            "setHorizontalHeaderLabels",
            [
                msg("text.name"),
                msg("text.size.distribution"),
                msg("text.orientation"),
                "Kappa",
                msg("text.target.p32"),
                msg("text.aperture.model"),
            ],
        )
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(QtCore.Qt.AlignCenter)
        for column in range(6):
            header.setSectionResizeMode(column, QtWidgets.QHeaderView.Interactive)
        for column, width in enumerate((130, 160, 180, 80, 120, 140)):
            self.table.setColumnWidth(column, width)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.disabled_note = ui_construct(
            QtWidgets.QLabel,
            msg(
                "text.the.current.generation.mode.does.not.use.stochastic.fracture.families.existing.settings.are.retained"
            ),
        )
        self.disabled_note.setObjectName("MutedLabel")
        self.disabled_note.setWordWrap(True)
        self.disabled_note.hide()
        self.body_layout.addLayout(toolbar)
        self.body_layout.addWidget(self.disabled_note)
        self.body_layout.addWidget(self.table, 1)
        self.add_button.clicked.connect(self._add)
        self.edit_button.clicked.connect(self._edit)
        self.copy_button.clicked.connect(self._copy)
        self.delete_button.clicked.connect(self._delete)
        self.table.doubleClicked.connect(self._edit)
        self.table.itemSelectionChanged.connect(self._update_button_states)
        self._update_button_states()
        if self.summary_only:
            self.findChild(QtWidgets.QLabel, "PageTitle").hide()
            for button in (self.add_button, self.edit_button, self.copy_button, self.delete_button):
                button.hide()
            self.disabled_note.hide()

    def set_active(self, active: bool) -> None:
        self.disabled_note.setVisible(not active)
        self.add_button.setEnabled(active)
        self.table.setEnabled(active)
        if active:
            self._update_button_states()
        else:
            for widget in (self.edit_button, self.copy_button, self.delete_button):
                widget.setEnabled(False)

    def set_stop_mode(self, mode: str) -> None:
        self.stop_mode = mode
        self._refresh()

    def set_orientation_mode(self, mode: str | None) -> None:
        self.orientation_mode = mode
        self._refresh()

    def load_config(self, config: DfnConfig) -> None:
        self.families = copy.deepcopy(config.stochastic_families)
        self.stop_mode = config.stop_rule.mode
        self.orientation_mode = config.orientation_mode
        self._refresh()

    def apply(self, config: DfnConfig) -> None:
        config.stochastic_families = copy.deepcopy(self.families)

    def _selected_row(self) -> int:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def _next_name(self) -> str:
        existing = {family.name for family in self.families}
        number = 1
        while msg("fracture.family_name", index=number).render() in existing:
            number += 1
        return msg("fracture.family_name", index=number).render()

    def _add(self) -> None:
        if self.summary_only:
            return
        family = blank_stochastic_family_draft(self._next_name())
        dialog = FamilyEditorDialog(family, self.stop_mode, self.orientation_mode, parent=self)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self.families.append(dialog.get_family())
            self._refresh(select_row=len(self.families) - 1)
            self.changed.emit()

    def _edit(self) -> None:
        if self.summary_only:
            return
        row = self._selected_row()
        if row < 0:
            return
        dialog = FamilyEditorDialog(
            self.families[row], self.stop_mode, self.orientation_mode, parent=self
        )
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self.families[row] = dialog.get_family()
            self._refresh(select_row=row)
            self.changed.emit()

    def _copy(self) -> None:
        if self.summary_only:
            return
        row = self._selected_row()
        if row < 0:
            return
        family = copy.deepcopy(self.families[row])
        family.name = self._next_name()
        self.families.insert(row + 1, family)
        self._refresh(select_row=row + 1)
        self.changed.emit()

    def _delete(self) -> None:
        if self.summary_only:
            return
        row = self._selected_row()
        if row < 0:
            return
        del self.families[row]
        self._refresh(select_row=min(row, len(self.families) - 1))
        self.changed.emit()

    def _refresh(self, select_row: int = -1) -> None:
        ui_call(
            self.table,
            "setHorizontalHeaderLabels",
            [
                msg("text.name"),
                msg("text.size.distribution"),
                msg("text.orientation"),
                "Kappa",
                msg("text.target.p32")
                if self.stop_mode == "p32"
                else msg("text.generation.weight")
                if self.stop_mode == "count"
                else "—",
                msg("text.aperture.model"),
            ],
        )
        self.table.setRowCount(len(self.families))
        for row, family in enumerate(self.families):

            def number(value):
                return f"{value:g}" if value is not None else "—"

            size = (
                join_text(
                    "",
                    [
                        "TPL ",
                        number(family.size.min_radius),
                        "–",
                        number(family.size.max_radius),
                        " m",
                    ],
                )
                if family.size.distribution == "tpl"
                else format_message(
                    "fractures.fixed_size", value=number(family.size.constant_radius)
                )
            )
            orientation = {
                "dip_strike": format_message(
                    "orientation.dip_strike",
                    dip=number(family.orientation.dip),
                    strike=number(family.orientation.strike),
                ),
                "theta_phi": format_message(
                    "orientation.theta_phi",
                    theta=number(family.orientation.theta),
                    phi=number(family.orientation.phi),
                ),
                "trend_plunge": format_message(
                    "orientation.trend_plunge",
                    trend=number(family.orientation.trend),
                    plunge=number(family.orientation.plunge),
                ),
            }.get(self.orientation_mode, "—")
            intensity = family.p32 if self.stop_mode == "p32" else family.probability
            hydraulic = (
                msg("text.constant") if family.hydraulic.relationship == "constant" else "b = αrᵝ"
            )
            values = [
                family.name,
                size,
                orientation,
                number(family.orientation.kappa),
                number(intensity),
                hydraulic,
            ]
            for column, value in enumerate(values):
                item = ui_construct(QtWidgets.QTableWidgetItem, value)
                if column > 0:
                    item.setTextAlignment(QtCore.Qt.AlignCenter)
                self.table.setItem(row, column, item)
        if 0 <= select_row < len(self.families):
            self.table.selectRow(select_row)
        self._update_button_states()

    def _update_button_states(self) -> None:
        selected = self._selected_row() >= 0
        self.edit_button.setEnabled(selected)
        self.copy_button.setEnabled(selected)
        self.delete_button.setEnabled(selected)


def nullable_spin(
    minimum: float, maximum: float, decimals: int = 8, step: float = 0.1, suffix: str = ""
) -> NullableAdaptiveDoubleSpinBox:
    widget = NullableAdaptiveDoubleSpinBox()
    widget.setRange(minimum, maximum)
    widget.setDecimals(decimals)
    widget.setSingleStep(step)
    widget.setKeyboardTracking(False)
    if suffix:
        widget.setSuffix(suffix)
    widget.set_optional_value(None)
    return widget


class CustomFractureEditorDialog(LocalizedDialog):
    def __init__(self, fracture: UserDefinedFractureConfig, parent=None):
        super().__init__(parent)
        self.fracture = copy.deepcopy(fracture)
        ui_call(self, "setWindowTitle", msg("text.user.defined.fractures"))
        self.setMinimumSize(820, 620)
        self.resize(900, 720)
        self.setStyleSheet(
            DIALOG_STYLE
            + "\n            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {\n                min-height: 30px;\n                max-height: 30px;\n            }\n            "
        )
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)
        content = QtWidgets.QWidget()
        columns = QtWidgets.QHBoxLayout(content)
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(14)
        left = QtWidgets.QVBoxLayout()
        right = QtWidgets.QVBoxLayout()
        columns.addLayout(left, 1)
        columns.addLayout(right, 1)
        basic = ui_construct(QtWidgets.QGroupBox, msg("text.general.information"))
        basic_form = QtWidgets.QFormLayout(basic)
        self.name_edit = ui_construct(QtWidgets.QLineEdit)
        self.shape_label = ui_construct(QtWidgets.QLabel, msg("text.rectangle"))
        ui_call(basic_form, "addRow", msg("text.fracture.name"), self.name_edit)
        ui_call(basic_form, "addRow", msg("text.shape"), self.shape_label)
        geometry = ui_construct(QtWidgets.QGroupBox, msg("text.geometry.parameters"))
        geometry_form = QtWidgets.QFormLayout(geometry)
        self.radii = nullable_spin(0.0, 1000000000.0, 8, 0.1, " m")
        self.aspect = nullable_spin(0.0, 1000000.0, 8, 0.1)
        ui_call(geometry_form, "addRow", msg("text.radii"), self.radii)
        ui_call(geometry_form, "addRow", msg("fracture.aspect_ratio"), self.aspect)
        center = ui_construct(QtWidgets.QGroupBox, msg("text.centre.coordinates"))
        center_form = QtWidgets.QFormLayout(center)
        self.tx = nullable_spin(-1000000000.0, 1000000000.0, 8, 0.1, " m")
        self.ty = nullable_spin(-1000000000.0, 1000000000.0, 8, 0.1, " m")
        self.tz = nullable_spin(-1000000000.0, 1000000000.0, 8, 0.1, " m")
        ui_call(center_form, "addRow", "X", self.tx)
        ui_call(center_form, "addRow", "Y", self.ty)
        ui_call(center_form, "addRow", "Z", self.tz)
        orientation = ui_construct(QtWidgets.QGroupBox, msg("text.orientation"))
        orientation_form = QtWidgets.QFormLayout(orientation)
        self.nx = nullable_spin(-1000000.0, 1000000.0, 8, 0.1)
        self.ny = nullable_spin(-1000000.0, 1000000.0, 8, 0.1)
        self.nz = nullable_spin(-1000000.0, 1000000.0, 8, 0.1)
        ui_call(orientation_form, "addRow", msg("text.normal.x"), self.nx)
        ui_call(orientation_form, "addRow", msg("text.normal.y"), self.ny)
        ui_call(orientation_form, "addRow", msg("text.normal.z"), self.nz)
        hydraulic = ui_construct(QtWidgets.QGroupBox, msg("text.hydraulic.properties"))
        hydraulic_form = QtWidgets.QFormLayout(hydraulic)
        self.permeability = nullable_spin(0.0, 100.0, 18, 1e-12, " m²")
        ui_call(hydraulic_form, "addRow", msg("viewer.permeability"), self.permeability)
        # Distinguish an unset value from an entered zero.
        for widget in (
            self.radii,
            self.aspect,
            self.tx,
            self.ty,
            self.tz,
            self.nx,
            self.ny,
            self.nz,
            self.permeability,
        ):
            widget.unset_text = ""
            widget.set_optional_value(None)
        left.addWidget(basic)
        left.addWidget(geometry)
        left.addStretch(1)
        right.addWidget(center)
        right.addWidget(orientation)
        right.addWidget(hydraulic)
        right.addStretch(1)
        layout.addWidget(content, 1)
        buttons = QtWidgets.QDialogButtonBox()
        ui_call(buttons, "addButton", msg("text.cancel"), QtWidgets.QDialogButtonBox.RejectRole)
        ui_call(buttons, "addButton", msg("text.ok"), QtWidgets.QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._load()

    def _load(self) -> None:
        fracture = self.fracture
        ui_call(self.name_edit, "setText", fracture.name)
        self.radii.set_optional_value(fracture.radii)
        self.aspect.set_optional_value(fracture.aspect_ratio)
        for widget, value in zip((self.tx, self.ty, self.tz), fracture.translation):
            widget.set_optional_value(value)
        for widget, value in zip((self.nx, self.ny, self.nz), fracture.normal_vector):
            widget.set_optional_value(value)
        self.permeability.set_optional_value(fracture.permeability)

    def _build_candidate(self) -> UserDefinedFractureConfig:
        return UserDefinedFractureConfig(
            id=self.fracture.id,
            name=self.name_edit.text().strip(),
            shape="rect",
            radii=self.radii.optional_value(),
            aspect_ratio=self.aspect.optional_value(),
            translation=[widget.optional_value() for widget in (self.tx, self.ty, self.tz)],
            normal_vector=[widget.optional_value() for widget in (self.nx, self.ny, self.nz)],
            permeability=self.permeability.optional_value(),
        )

    def _accept(self) -> None:
        candidate = self._build_candidate()
        errors = candidate.validate()
        if errors:
            MessageBox.warning(
                self,
                msg("text.input.needs.correction"),
                join_text("\n", (join_text("", ["— ", error]) for error in errors)),
            )
            return
        self.fracture = candidate
        self.accept()

    def get_fracture(self) -> UserDefinedFractureConfig:
        return copy.deepcopy(self.fracture)


class UserDefinedPage(BasePage):
    def __init__(self, parent=None):
        super().__init__(msg("text.user.defined.fractures"), "", parent)
        self.fractures: list[UserDefinedFractureConfig] = []
        toolbar = QtWidgets.QHBoxLayout()
        self.add_button = ui_construct(QtWidgets.QPushButton, msg("text.add"))
        self.edit_button = ui_construct(QtWidgets.QPushButton, msg("text.edit"))
        self.copy_button = ui_construct(QtWidgets.QPushButton, msg("text.duplicate"))
        self.delete_button = ui_construct(QtWidgets.QPushButton, msg("text.delete"))
        self.delete_button.setObjectName("DangerButton")
        for button in (self.add_button, self.edit_button, self.copy_button, self.delete_button):
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        self.table = QtWidgets.QTableWidget(0, 5)
        ui_call(
            self.table,
            "setHorizontalHeaderLabels",
            [
                msg("text.name"),
                msg("text.size"),
                msg("text.centre.coordinates"),
                msg("text.normal.vector"),
                msg("viewer.permeability"),
            ],
        )
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(QtCore.Qt.AlignCenter)
        for column, width in enumerate((150, 165, 205, 185, 135)):
            header.setSectionResizeMode(column, QtWidgets.QHeaderView.Interactive)
            self.table.setColumnWidth(column, width)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.body_layout.addLayout(toolbar)
        self.body_layout.addWidget(self.table, 1)
        self.add_button.clicked.connect(self._add)
        self.edit_button.clicked.connect(self._edit)
        self.copy_button.clicked.connect(self._copy)
        self.delete_button.clicked.connect(self._delete)
        self.table.doubleClicked.connect(self._edit)
        self.table.itemSelectionChanged.connect(self._update_button_states)
        self._update_button_states()

    @staticmethod
    def _number(value: float) -> str:
        return f"{value:g}"

    def _selected_row(self) -> int:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def _next_number(self) -> int:
        used = {fracture.id for fracture in self.fractures}
        number = 1
        while join_text("", ["fracture_", number]) in used:
            number += 1
        return number

    def _new_fracture(self) -> UserDefinedFractureConfig:
        number = self._next_number()
        return UserDefinedFractureConfig(
            id=join_text("", ["fracture_", number]),
            name=msg("fracture.new_name", index=number).render(),
            radii=None,
            aspect_ratio=None,
            translation=[None, None, None],
            normal_vector=[None, None, None],
            permeability=None,
        )

    def _add(self) -> None:
        dialog = CustomFractureEditorDialog(self._new_fracture(), self)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self.fractures.append(dialog.get_fracture())
            self._refresh(select_row=len(self.fractures) - 1)
            self.changed.emit()

    def _edit(self) -> None:
        row = self._selected_row()
        if row < 0:
            return
        dialog = CustomFractureEditorDialog(self.fractures[row], self)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self.fractures[row] = dialog.get_fracture()
            self._refresh(select_row=row)
            self.changed.emit()

    def _copy(self) -> None:
        row = self._selected_row()
        if row < 0:
            return
        number = self._next_number()
        copied = copy.deepcopy(self.fractures[row])
        copied.id = join_text("", ["fracture_", number])
        copied.name = msg("fracture.new_name", index=number).render()
        self.fractures.insert(row + 1, copied)
        self._refresh(select_row=row + 1)
        self.changed.emit()

    def _delete(self) -> None:
        row = self._selected_row()
        if row < 0:
            return
        del self.fractures[row]
        self._refresh(select_row=min(row, len(self.fractures) - 1))
        self.changed.emit()

    def _refresh(self, select_row: int = -1) -> None:
        self.table.setRowCount(len(self.fractures))
        for row, fracture in enumerate(self.fractures):
            values = (
                fracture.name,
                join_text(
                    "",
                    [
                        "r=",
                        self._number(fracture.radii),
                        " m, AR=",
                        self._number(fracture.aspect_ratio),
                    ],
                ),
                join_text(
                    "",
                    [
                        "(",
                        join_text(", ", (self._number(value) for value in fracture.translation)),
                        ") m",
                    ],
                ),
                join_text(
                    "",
                    [
                        "(",
                        join_text(", ", (self._number(value) for value in fracture.normal_vector)),
                        ")",
                    ],
                ),
                join_text("", [self._number(fracture.permeability), " m²"]),
            )
            for column, value in enumerate(values):
                item = ui_construct(QtWidgets.QTableWidgetItem, value)
                if column:
                    item.setTextAlignment(QtCore.Qt.AlignCenter)
                self.table.setItem(row, column, item)
        if 0 <= select_row < len(self.fractures):
            self.table.selectRow(select_row)
        self._update_button_states()

    def _update_button_states(self) -> None:
        selected = self._selected_row() >= 0
        self.edit_button.setEnabled(selected)
        self.copy_button.setEnabled(selected)
        self.delete_button.setEnabled(selected)

    def load_config(self, config: DfnConfig) -> None:
        self.fractures = copy.deepcopy(config.user_defined_fractures)
        self._refresh()

    def apply(self, config: DfnConfig) -> None:
        config.user_defined_fractures = copy.deepcopy(self.fractures)


DeterministicPage = UserDefinedPage


class RandomNetworkConfigurationDialog(LocalizedDialog):
    def __init__(self, config: DfnConfig, parent=None):
        super().__init__(parent)
        self._initial_config = config.copy()
        ui_call(self, "setWindowTitle", msg("text.stochastic.fracture.configuration"))
        self.setMinimumSize(980, 700)
        self.resize(1080, 780)
        self.setStyleSheet(DIALOG_STYLE)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(12)
        controls = ui_construct(QtWidgets.QGroupBox, msg("text.stochastic.network.controls"))
        form = QtWidgets.QFormLayout(controls)
        self.seed = ui_construct(QtWidgets.QLineEdit)
        self.seed.setValidator(
            QtGui.QRegularExpressionValidator(
                QtCore.QRegularExpression("[1-9][0-9]{0,9}"), self.seed
            )
        )
        self.stop_mode = QtWidgets.QComboBox()
        ui_call(self.stop_mode, "addItem", "...", None)
        ui_call(self.stop_mode, "addItem", msg("text.terminate.by.fracture.intensity.p32"), "p32")
        ui_call(self.stop_mode, "addItem", msg("text.terminate.by.fracture.count"), "count")
        self.orientation_mode = QtWidgets.QComboBox()
        ui_call(self.orientation_mode, "addItem", "...", None)
        ui_call(self.orientation_mode, "addItem", "Theta/Phi", "theta_phi")
        ui_call(self.orientation_mode, "addItem", msg("text.trend.plunge"), "trend_plunge")
        ui_call(self.orientation_mode, "addItem", msg("text.dip.strike"), "dip_strike")
        self.n_poly = nullable_spin(0.0, 100000000.0, 0, 1.0)
        self.n_poly.unset_text = ""
        self.n_poly.set_optional_value(None)
        self.n_poly_label = ui_construct(QtWidgets.QLabel, msg("text.target.fracture.count.npoly"))
        ui_call(form, "addRow", msg("text.random.seed"), self.seed)
        ui_call(form, "addRow", msg("text.stopping.criterion"), self.stop_mode)
        ui_call(form, "addRow", msg("text.orientation.convention"), self.orientation_mode)
        ui_call(form, "addRow", self.n_poly_label, self.n_poly)
        layout.addWidget(controls)
        self.families_page = FamiliesPage()
        layout.addWidget(self.families_page, 1)
        buttons = QtWidgets.QDialogButtonBox()
        ui_call(buttons, "addButton", msg("text.cancel"), QtWidgets.QDialogButtonBox.RejectRole)
        ui_call(buttons, "addButton", msg("text.ok"), QtWidgets.QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._load(self._initial_config)
        self.stop_mode.currentIndexChanged.connect(self._update_stop_mode)
        self.orientation_mode.currentIndexChanged.connect(self._update_orientation_mode)
        self.families_page.changed.connect(self._update_orientation_lock)

    def _load(self, config: DfnConfig) -> None:
        ui_call(self.seed, "setText", "" if config.seed is None else str(config.seed))
        self.stop_mode.setCurrentIndex(max(self.stop_mode.findData(config.stop_rule.mode), 0))
        self.orientation_mode.setCurrentIndex(
            max(self.orientation_mode.findData(config.orientation_mode), 0)
        )
        self.n_poly.set_optional_value(config.stop_rule.n_poly)
        self.families_page.load_config(config)
        self._update_stop_mode()
        self._update_orientation_lock()

    def _update_stop_mode(self) -> None:
        mode = self.stop_mode.currentData()
        self.n_poly.setVisible(mode == "count")
        self.n_poly_label.setVisible(mode == "count")
        self.families_page.set_stop_mode(mode)

    def _update_orientation_mode(self) -> None:
        self.families_page.set_orientation_mode(self.orientation_mode.currentData())

    def _update_orientation_lock(self) -> None:
        self.orientation_mode.setEnabled(not bool(self.families_page.families))

    def _collect_config(self) -> DfnConfig:
        config = self._initial_config.copy()
        seed_text = self.seed.text().strip()
        config.seed = int(seed_text) if seed_text.isdigit() else None
        config.stop_rule = StopRuleConfig(
            mode=self.stop_mode.currentData(),
            n_poly=None
            if self.n_poly.optional_value() is None
            else int(self.n_poly.optional_value()),
        )
        config.orientation_mode = self.orientation_mode.currentData()
        self.families_page.apply(config)
        return config

    def _validate_random_network(self, config: DfnConfig) -> list[str]:
        families = config.stochastic_families
        if not families:
            if any(
                (
                    config.seed is not None,
                    config.stop_rule.mode is not None,
                    config.orientation_mode is not None,
                    config.stop_rule.n_poly is not None,
                )
            ):
                return [msg("text.add.at.least.one.valid.stochastic.fracture.family")]
            return []
        errors: list[str] = []
        if (
            not isinstance(config.seed, int)
            or isinstance(config.seed, bool)
            or (not 1 <= config.seed <= 4294967295)
        ):
            errors.append(msg("validation.the.random.seed.must.be.an.integer.from.1.to.4294967295"))
        if config.stop_rule.mode not in {"p32", "count"}:
            errors.append(msg("text.select.a.stopping.rule"))
        if config.orientation_mode not in {"dip_strike", "theta_phi", "trend_plunge"}:
            errors.append(msg("text.select.an.orientation.representation"))
        if config.stop_rule.mode == "count" and (
            not isinstance(config.stop_rule.n_poly, int) or config.stop_rule.n_poly <= 0
        ):
            errors.append(msg("text.set.a.positive.integer.target.fracture.count.npoly"))
        for family in families:
            errors.extend(family.validate(config.stop_rule.mode, config.orientation_mode))
        if (
            config.stop_rule.mode == "count"
            and all((isinstance(item.probability, (int, float)) for item in families))
            and (
                not math.isclose(
                    sum((item.probability for item in families)), 1.0, rel_tol=1e-08, abs_tol=1e-08
                )
            )
        ):
            errors.append(
                msg("validation.family.weights.must.sum.to.1.when.stopping.by.fracture.count")
            )
        return list(dict.fromkeys(errors))

    def _accept(self) -> None:
        config = self._collect_config()
        errors = self._validate_random_network(config)
        if errors:
            MessageBox.warning(
                self,
                msg("text.input.needs.correction"),
                join_text("\n", (join_text("", ["• ", item]) for item in errors)),
            )
            return
        self._resolved_config = config
        self.accept()

    def get_config(self) -> DfnConfig:
        return self._resolved_config.copy()


class FractureModelPage(BasePage):
    def __init__(self, parent=None):
        super().__init__(msg("text.fractures"), "", parent)
        self._random_config = BlankConfigFactory.dfn()
        scale_group = ui_construct(QtWidgets.QGroupBox, msg("text.global.dfn.length.scale"))
        scale_form = QtWidgets.QFormLayout(scale_group)
        self.h = NullableAdaptiveDoubleSpinBox()
        self.h.setRange(0.0, 1000000000.0)
        self.h.setDecimals(10)
        self.h.setSingleStep(0.01)
        self.h.setKeyboardTracking(False)
        self.h.unset_text = ""
        self.h.set_optional_value(None)
        self.h.setMinimumWidth(260)
        ui_call(scale_form, "addRow", msg("text.minimum.feature.size.m"), self.h)
        random_group = ui_construct(QtWidgets.QGroupBox, msg("text.stochastic.fractures"))
        random_layout = QtWidgets.QVBoxLayout(random_group)
        random_actions = QtWidgets.QHBoxLayout()
        self.configure_random_button = ui_construct(
            QtWidgets.QPushButton, msg("text.configure.stochastic.fractures")
        )
        self.random_count_label = ui_construct(
            QtWidgets.QLabel, msg("text.stochastic.fracture.families.0")
        )
        random_actions.addWidget(self.configure_random_button)
        random_actions.addWidget(self.random_count_label)
        random_actions.addStretch()
        random_layout.addLayout(random_actions)
        self.families = FamiliesPage(summary_only=True)
        random_layout.addWidget(self.families, 1)
        custom_group = ui_construct(QtWidgets.QGroupBox, msg("text.user.defined.fractures"))
        custom_layout = QtWidgets.QVBoxLayout(custom_group)
        self.user_defined = UserDefinedPage()
        self.user_defined.findChild(QtWidgets.QLabel, "PageTitle").hide()
        custom_layout.addWidget(self.user_defined, 1)
        self.body_layout.addWidget(scale_group)
        self.body_layout.addWidget(random_group, 1)
        self.body_layout.addWidget(custom_group, 1)
        self.configure_random_button.clicked.connect(self._configure_random)
        self.h.valueChanged.connect(self.changed.emit)
        self.user_defined.changed.connect(self.changed.emit)

    def _configure_random(self) -> None:
        dialog = RandomNetworkConfigurationDialog(self._random_config, self)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self._random_config = dialog.get_config()
            self.families.load_config(self._random_config)
            self._refresh_random_summary()
            self.changed.emit()

    def _refresh_random_summary(self) -> None:
        ui_call(
            self.random_count_label,
            "setText",
            format_message("fractures.families", count=len(self.families.families)),
        )

    def load_config(self, config):
        self._random_config = config.copy()
        self.h.set_optional_value(config.h)
        self.families.load_config(config)
        self._refresh_random_summary()
        self.user_defined.load_config(config)

    def apply(self, config):
        config.h = self.h.optional_value()
        config.seed = self._random_config.seed
        config.stop_rule = copy.deepcopy(self._random_config.stop_rule)
        config.orientation_mode = self._random_config.orientation_mode
        self.families.apply(config)
        self.user_defined.apply(config)


class ConnectivityPage(BasePage):
    def __init__(self, parent=None):
        super().__init__(
            msg("text.connectivity"),
            msg(
                "text.boundary.connectivity.filtering.is.always.enabled.if.not.customized.inlet.x.and.outlet.x"
            ),
            parent,
        )
        filter_group = ui_construct(QtWidgets.QGroupBox, msg("text.boundary.connectivity.filter"))
        filter_layout = QtWidgets.QVBoxLayout(filter_group)
        self.filter_checkbox = ui_construct(
            QtWidgets.QCheckBox, msg("text.custom.boundary.faces.defaults.to.x.and.x.when.disabled")
        )
        filter_layout.addWidget(self.filter_checkbox)
        face_layout = QtWidgets.QGridLayout()
        self.face_checks: dict[str, QtWidgets.QCheckBox] = {}
        labels = [
            ("+x", "+X", 0, 0),
            ("-x", "−X", 0, 1),
            ("+y", "+Y", 1, 0),
            ("-y", "−Y", 1, 1),
            ("+z", "+Z", 2, 0),
            ("-z", "−Z", 2, 1),
        ]
        for key, label, row, column in labels:
            checkbox = ui_construct(QtWidgets.QCheckBox, label)
            self.face_checks[key] = checkbox
            face_layout.addWidget(checkbox, row, column)
        self.filter_checkbox.setChecked(True)
        self.face_checks["+x"].setChecked(True)
        self.face_checks["-x"].setChecked(True)
        filter_layout.addLayout(face_layout)
        cluster_group = ui_construct(QtWidgets.QGroupBox, msg("text.connected.cluster.retention"))
        cluster_layout = QtWidgets.QVBoxLayout(cluster_group)
        self.keep_largest_radio = ui_construct(
            QtWidgets.QRadioButton, msg("text.keep.only.the.largest.valid.connected.cluster")
        )
        self.keep_all_radio = ui_construct(
            QtWidgets.QRadioButton, msg("text.keep.all.valid.connected.clusters")
        )
        isolated_group = ui_construct(QtWidgets.QGroupBox, msg("text.isolated.fractures"))
        isolated_layout = QtWidgets.QVBoxLayout(isolated_group)
        self.keep_isolated = ui_construct(QtWidgets.QCheckBox, msg("text.keep.isolated.fractures"))
        cluster_layout.addWidget(self.keep_largest_radio)
        cluster_layout.addWidget(self.keep_all_radio)
        isolated_layout.addWidget(self.keep_isolated)
        self.body_layout.addWidget(filter_group)
        self.body_layout.addWidget(cluster_group)
        self.body_layout.addWidget(isolated_group)
        self.add_stretch()
        self.filter_checkbox.toggled.connect(self._update_enabled)
        for widget in (
            *self.face_checks.values(),
            self.keep_largest_radio,
            self.keep_all_radio,
            self.keep_isolated,
        ):
            if isinstance(widget, QtWidgets.QAbstractButton):
                widget.toggled.connect(self.changed.emit)

    def _update_enabled(self, checked: bool) -> None:
        if not checked:
            for face, checkbox in self.face_checks.items():
                blocked = checkbox.blockSignals(True)
                checkbox.setChecked(face in {"+x", "-x"})
                checkbox.blockSignals(blocked)
        for checkbox in self.face_checks.values():
            checkbox.setEnabled(checked)
        self.changed.emit()

    def load_config(self, config: DfnConfig) -> None:
        value = config.connectivity
        selected = set(value.boundary_faces)
        effective_faces = selected if value.filter_by_boundary and selected else {"+x", "-x"}
        self.filter_checkbox.setChecked(True)
        for face, checkbox in self.face_checks.items():
            checkbox.setChecked(face in effective_faces)
        self.keep_largest_radio.setChecked(value.keep_only_largest_cluster)
        self.keep_all_radio.setChecked(not value.keep_only_largest_cluster)
        self.keep_isolated.setChecked(value.keep_isolated_fractures)
        self._update_enabled(True)

    def apply(self, config: DfnConfig) -> None:
        custom = self.filter_checkbox.isChecked()
        config.connectivity = ConnectivityConfig(
            filter_by_boundary=True,
            boundary_faces=[face for face in BOUNDARY_FACES if self.face_checks[face].isChecked()]
            if custom
            else ["+x", "-x"],
            keep_only_largest_cluster=self.keep_largest_radio.isChecked(),
            keep_isolated_fractures=self.keep_isolated.isChecked(),
        )


class FramPage(BasePage):
    def __init__(self, parent=None):
        super().__init__("FRAM", "", parent)
        self._legacy_fram = FramConfig()
        self._legacy_output = OutputConfig()
        fram_group = ui_construct(QtWidgets.QGroupBox, msg("text.fram.geometry.quality.control"))
        fram_form = QtWidgets.QFormLayout(fram_group)
        self.state_combo = QtWidgets.QComboBox()
        ui_call(self.state_combo, "addItem", msg("text.enabled"), True)
        ui_call(self.state_combo, "addItem", msg("text.disabled"), False)
        ui_call(fram_form, "addRow", msg("text.fram.status"), self.state_combo)
        self.rejects_per_fracture = QtWidgets.QSpinBox()
        self.rejects_per_fracture.setRange(1, 1000000000)
        self.rejects_per_fracture.setValue(10)
        ui_call(
            self.rejects_per_fracture,
            "setToolTip",
            msg("text.maximum.relocation.attempts.before.a.fracture.is.rejected"),
        )
        ui_call(
            fram_form, "addRow", msg("text.maximum.relocation.attempts"), self.rejects_per_fracture
        )
        self.radii_list_increase = AdaptiveDoubleSpinBox()
        self.radii_list_increase.setRange(0.0, 1000000000.0)
        self.radii_list_increase.setDecimals(10)
        self.radii_list_increase.setSingleStep(0.01)
        self.radii_list_increase.setValue(0.1)
        ui_call(
            self.radii_list_increase,
            "setToolTip",
            msg("text.increase.fraction.for.the.presampled.radius.list.0.1.means.a.10.increase"),
        )
        ui_call(fram_form, "addRow", msg("text.radii.list.increment"), self.radii_list_increase)
        self.triple_intersections = ui_construct(
            QtWidgets.QCheckBox, msg("text.allow.triple.intersections")
        )
        ui_call(fram_form, "addRow", self.triple_intersections)
        self.print_reject_reasons = ui_construct(
            QtWidgets.QCheckBox, msg("text.output.rejection.reasons")
        )
        ui_call(
            self.print_reject_reasons,
            "setToolTip",
            msg("text.diagnostic.option.enabling.it.increases.log.output"),
        )
        ui_call(fram_form, "addRow", self.print_reject_reasons)
        self.body_layout.addWidget(fram_group)
        self.add_stretch()
        self.state_combo.currentIndexChanged.connect(self._update_control_states)
        self.state_combo.currentIndexChanged.connect(self.changed.emit)
        for widget in (
            self.rejects_per_fracture,
            self.radii_list_increase,
            self.triple_intersections,
            self.print_reject_reasons,
        ):
            signal = (
                widget.toggled if isinstance(widget, QtWidgets.QCheckBox) else widget.valueChanged
            )
            signal.connect(self.changed.emit)
        self._update_control_states()

    def _update_control_states(self) -> None:
        enabled = bool(self.state_combo.currentData())
        self.rejects_per_fracture.setEnabled(enabled)
        self.radii_list_increase.setEnabled(enabled)
        self.triple_intersections.setEnabled(enabled)
        self.print_reject_reasons.setEnabled(enabled)

    def load_config(self, config: DfnConfig) -> None:
        self._legacy_fram = copy.deepcopy(config.fram)
        self._legacy_output = copy.deepcopy(config.output)
        enabled = True if config.fram.enabled is None else bool(config.fram.enabled)
        self.state_combo.setCurrentIndex(self.state_combo.findData(enabled))
        self.rejects_per_fracture.setValue(config.fram.rejects_per_fracture or 10)
        self.radii_list_increase.setValue(
            0.1 if config.fram.radii_list_increase is None else config.fram.radii_list_increase
        )
        self.triple_intersections.setChecked(bool(config.fram.triple_intersections))
        self.print_reject_reasons.setChecked(bool(config.fram.print_reject_reasons))
        self._update_control_states()

    def apply(self, config: DfnConfig) -> None:
        enabled = bool(self.state_combo.currentData())
        fram = copy.deepcopy(self._legacy_fram)
        fram.enabled = enabled
        fram.rejects_per_fracture = self.rejects_per_fracture.value()
        fram.radii_list_increase = self.radii_list_increase.value()
        fram.triple_intersections = self.triple_intersections.isChecked()
        fram.print_reject_reasons = self.print_reject_reasons.isChecked()
        config.fram = fram
        config.output = copy.deepcopy(self._legacy_output)


class ReviewPage(BasePage):
    SUMMARY_FIELDS = (
        msg("text.current.composition"),
        msg("text.model.domain"),
        msg("text.domain.expansion"),
        msg("text.random.seed"),
        msg("text.stopping.criterion"),
        msg("text.orientation.representation"),
        msg("text.stochastic.fracture.family"),
        msg("text.user.defined.fractures"),
        msg("text.dfn.resolution.h"),
        "FRAM",
        msg("text.boundary.filter"),
        msg("text.connected.cluster.retention"),
        msg("text.isolated.fractures"),
    )

    def __init__(self, parent=None):
        super().__init__(msg("text.review.save"), "", parent)
        heading = ui_construct(QtWidgets.QLabel, msg("text.configuration.summary"))
        heading.setObjectName("SectionTitle")
        self.summary_grid = QtWidgets.QGridLayout()
        self.summary_grid.setHorizontalSpacing(28)
        self.summary_grid.setVerticalSpacing(7)
        self._summary_values: dict[str, QtWidgets.QLabel] = {}
        for row, field in enumerate(self.SUMMARY_FIELDS):
            label = ui_construct(QtWidgets.QLabel, field)
            label.setObjectName("MutedLabel")
            value = ui_construct(QtWidgets.QLabel, "—")
            value.setWordWrap(True)
            self.summary_grid.addWidget(label, row, 0)
            self.summary_grid.addWidget(value, row, 1)
            self._summary_values[field] = value
        self.summary_grid.setColumnStretch(1, 1)
        self.status_label = ui_construct(QtWidgets.QLabel)
        self.status_label.setWordWrap(True)
        self.error_label = ui_construct(QtWidgets.QLabel)
        self.error_label.setWordWrap(True)
        self.error_label.setTextFormat(QtCore.Qt.PlainText)
        self.body_layout.addWidget(heading)
        self.body_layout.addLayout(self.summary_grid)
        self.body_layout.addWidget(self.status_label)
        self.body_layout.addWidget(self.error_label)
        self.add_stretch()

    @staticmethod
    def _number(value) -> str:
        return "—" if value is None else f"{value:g}"

    def set_review(self, config: DfnConfig, errors: list[str]) -> None:
        random_count = len(config.stochastic_families)
        custom_count = len(config.user_defined_fractures)
        composition = (
            msg("text.stochastic.user.defined")
            if random_count and custom_count
            else msg("text.stochastic.fractures")
            if random_count
            else msg("text.user.defined.fractures")
            if custom_count
            else "—"
        )
        values = {
            msg("text.current.composition"): composition,
            msg("text.model.domain"): join_text(
                " × ", (self._number(value) for value in config.domain_size)
            )
            + " m",
            msg("text.domain.expansion"): join_text(
                " × ", (self._number(value) for value in config.normalized_domain_size_increase())
            )
            + " m",
            msg("text.random.seed"): ("—" if config.seed is None else str(config.seed))
            if random_count
            else msg("text.not.applicable"),
            msg("text.stopping.criterion"): {
                "p32": msg("text.terminate.by.fracture.intensity.p32"),
                "count": msg("text.terminate.by.fracture.count"),
            }.get(config.stop_rule.mode, "—")
            if random_count
            else msg("text.not.applicable"),
            msg("text.orientation.representation"): {
                "dip_strike": msg("text.dip.strike"),
                "theta_phi": "Theta/Phi",
                "trend_plunge": msg("text.trend.plunge"),
            }.get(config.orientation_mode, "—")
            if random_count
            else msg("text.not.applicable"),
            msg("text.stochastic.fracture.family"): str(random_count),
            msg("text.user.defined.fractures"): str(custom_count),
            msg("text.dfn.resolution.h"): "—"
            if config.h is None
            else join_text("", [f"{config.h:g}", " m"]),
            "FRAM": msg("text.enabled") if config.fram.enabled else msg("text.disabled"),
            msg("text.boundary.filter"): join_text(", ", config.connectivity.boundary_faces)
            if config.connectivity.filter_by_boundary and config.connectivity.boundary_faces
            else msg("text.disabled"),
            msg("text.connected.cluster.retention"): msg(
                "text.keep.only.the.largest.valid.connected.cluster"
            )
            if config.connectivity.keep_only_largest_cluster
            else msg("text.keep.all.valid.connected.clusters"),
            msg("text.isolated.fractures"): msg("text.retain")
            if config.connectivity.keep_isolated_fractures
            else msg("text.do.not.keep"),
        }
        for field, value in values.items():
            ui_call(self._summary_values[field], "setText", value)
        if errors:
            ui_call(
                self.status_label,
                "setText",
                format_message("validation.needs_correction", count=len(errors)),
            )
            self.status_label.setStyleSheet(
                join_text("", ["color: ", ERROR, "; font-weight: 600;"])
            )
            ui_call(
                self.error_label,
                "setText",
                join_text("\n", (join_text("", ["× ", error]) for error in errors)),
            )
            self.error_label.show()
        else:
            ui_call(self.status_label, "setText", msg("text.configuration.checks.passed"))
            self.status_label.setStyleSheet(
                join_text("", ["color: ", ACCENT_DARK, "; font-weight: 600;"])
            )
            self.error_label.clear()
            self.error_label.hide()


class DfnGenerationDialog(LocalizedDialog):
    config_saved = QtCore.pyqtSignal(dict)
    PAGE_NAMES = [
        msg("text.1.model.domain"),
        msg("text.2.fractures"),
        "3  FRAM",
        msg("text.4.connectivity"),
        msg("text.5.review.save"),
    ]

    def __init__(self, config: DfnConfig | dict | None = None, parent=None):
        super().__init__(parent)
        if isinstance(config, DfnConfig):
            self._initial_config = config.copy()
        elif isinstance(config, dict):
            self._initial_config = DfnConfig.from_dict(config)
        else:
            self._initial_config = BlankConfigFactory.dfn()
        self._result_config = self._initial_config.copy()
        ui_call(self, "setWindowTitle", msg("text.dfn.generation.configuration"))
        self.setMinimumSize(1100, 760)
        self.resize(1320, 880)
        self.setStyleSheet(DIALOG_STYLE)
        self._build_ui()
        self._load_config(self._initial_config)
        self._connect_signals()
        self._update_mode_dependent_ui()
        self._refresh_review()

    def _build_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 16)
        outer.setSpacing(12)
        content = QtWidgets.QHBoxLayout()
        content.setSpacing(14)
        self.navigation = QtWidgets.QListWidget()
        self.navigation.setObjectName("Navigation")
        self.navigation.setFixedWidth(220)
        for name in self.PAGE_NAMES:
            ui_call(self.navigation, "addItem", name)
        content.addWidget(self.navigation)
        self.stack = QtWidgets.QStackedWidget()
        self.domain_page = DomainPage()
        self.fracture_model_page = FractureModelPage()
        ui_call(
            self.domain_page.findChild(QtWidgets.QLabel, "PageTitle"),
            "setText",
            msg("text.model.domain"),
        )
        self.families_page = self.fracture_model_page.families
        self.user_defined_page = self.fracture_model_page.user_defined
        self.connectivity_page = ConnectivityPage()
        self.fram_page = FramPage()
        self.review_page = ReviewPage()
        self.pages: list[BasePage] = [
            self.domain_page,
            self.fracture_model_page,
            self.fram_page,
            self.connectivity_page,
            self.review_page,
        ]
        for page in self.pages:
            self.stack.addWidget(page)
        content.addWidget(self.stack, 1)
        outer.addLayout(content, 1)
        bottom = QtWidgets.QHBoxLayout()
        self.import_button = ui_construct(QtWidgets.QPushButton, msg("text.load.configuration"))
        self.export_button = ui_construct(QtWidgets.QPushButton, msg("text.export.configuration"))
        self.cancel_button = ui_construct(QtWidgets.QPushButton, msg("text.cancel"))
        self.save_button = ui_construct(QtWidgets.QPushButton, msg("text.apply.configuration"))
        self.save_button.setObjectName("PrimaryButton")
        bottom.addWidget(self.import_button)
        bottom.addWidget(self.export_button)
        bottom.addStretch()
        bottom.addWidget(self.cancel_button)
        bottom.addWidget(self.save_button)
        outer.addLayout(bottom)
        self.navigation.setCurrentRow(0)

    def _connect_signals(self) -> None:
        self.navigation.currentRowChanged.connect(self._show_page)
        self.cancel_button.clicked.connect(self.reject)
        self.save_button.clicked.connect(self._save_and_accept)
        self.import_button.clicked.connect(self._import_json)
        self.export_button.clicked.connect(self._export_json)
        for page in self.pages:
            page.changed.connect(self._on_changed)

    def _load_config(self, config: DfnConfig) -> None:
        self._editor_snapshot = None
        blockers = [QtCore.QSignalBlocker(page) for page in self.pages]
        self.setUpdatesEnabled(False)
        try:
            self.domain_page.load_config(config)
            self.fracture_model_page.load_config(config)
            self.connectivity_page.load_config(config)
            self.fram_page.load_config(config)
        finally:
            blockers.clear()
            self.setUpdatesEnabled(True)
        for page in self.pages:
            page.updateGeometry()
            page.update()
        self.stack.update()
        self.update()
        self._editor_snapshot = self._collect_config().to_dict()

    def _show_page(self, row: int) -> None:
        if row < 0:
            return
        self.stack.setCurrentIndex(row)
        if row == len(self.pages) - 1:
            self._refresh_review()

    def _step(self, direction: int) -> None:
        row = self.navigation.currentRow()
        target = max(0, min(len(self.pages) - 1, row + direction))
        self.navigation.setCurrentRow(target)

    def _on_changed(self) -> None:
        self._refresh_review()

    def _update_mode_dependent_ui(self) -> None:
        self._on_changed()

    def _collect_config(self) -> DfnConfig:
        config = self._initial_config.copy()
        self.domain_page.apply(config)
        self.fracture_model_page.apply(config)
        self.connectivity_page.apply(config)
        self.fram_page.apply(config)
        if getattr(self, "_editor_snapshot", None) is not None:
            from fracrtm.config.editor_values import preserve_unedited_numbers

            config = DfnConfig.from_dict(
                preserve_unedited_numbers(
                    self._initial_config.to_dict(), self._editor_snapshot, config.to_dict()
                )
            )
        return config

    def _refresh_review(self) -> None:
        try:
            config = self._collect_config()
            errors = config.validate_user_blocking()
        except Exception as exc:
            config = self._initial_config.copy()
            errors = [format_message("config.collect_failed", reason=exc)]
        self.review_page.set_review(config, errors)

    def _save_and_accept(self) -> None:
        config = self._collect_config()
        errors = config.validate_user_blocking()
        if errors:
            self.navigation.setCurrentRow(len(self.pages) - 1)
            MessageBox.warning(
                self,
                msg("text.configuration.checks.have.not.passed"),
                msg("text.correct.the.following.issues.on.the.review.save.page")
                + join_text("\n", (join_text("", ["• ", error]) for error in errors)),
            )
            return
        self._result_config = config
        self.config_saved.emit(config.to_dict())
        self.accept()

    def _import_json(self) -> None:
        filepath, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            render_text(msg("text.import.dfn.configuration")),
            "",
            render_text(msg("text.json.configuration.json.all.files")),
            options=QtWidgets.QFileDialog.DontUseNativeDialog,
        )
        if not filepath:
            return
        try:
            config = DfnConfig.load_json(filepath)
        except Exception as exc:
            MessageBox.critical(self, msg("text.import.failed"), exception_text(exc))
            return
        self._initial_config = config.copy()
        self._load_config(config)
        self._update_mode_dependent_ui()
        self._refresh_review()

    def _export_json(self) -> None:
        config = self._collect_config()
        filepath, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            render_text(msg("text.export.dfn.configuration")),
            render_text("dfn_config.json"),
            render_text(msg("text.json.configuration.json")),
            options=QtWidgets.QFileDialog.DontUseNativeDialog,
        )
        if not filepath:
            return
        try:
            config.save_json(filepath)
        except Exception as exc:
            MessageBox.critical(self, msg("text.export.failed"), exception_text(exc))
            return
        errors = config.validate_user_blocking()
        message = msg("text.configuration.exported")
        if errors:
            message += msg("text.note.the.current.configuration.still.has.validation.issues")
            message += join_text("\n", (join_text("", ["• ", error]) for error in errors))
        MessageBox.information(self, msg("text.export.complete"), message)

    def get_config(self) -> DfnConfig:
        return self._result_config.copy()

    def get_config_dict(self) -> dict:
        return self.get_config().to_dict()


def main() -> int:
    application = QtWidgets.QApplication(sys.argv)
    application.setApplicationName("FracRTM-GUI")
    dialog = DfnGenerationDialog()
    if dialog.exec_() == QtWidgets.QDialog.Accepted:
        print(json.dumps(prepare_json(dialog.get_config_dict()), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
