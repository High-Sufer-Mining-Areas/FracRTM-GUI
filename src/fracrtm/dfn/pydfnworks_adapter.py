from __future__ import annotations

import copy
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fracrtm.config.dfn_config import BOUNDARY_FACES, DfnConfig
from fracrtm.messages import join_text
from fracrtm.messages import message as msg

# Serialize native calls because pydfnWorks changes the process directory.
NATIVE_DIRECTORY_LOCK = threading.RLock()

ORIENTATION_OPTION_MAP = {"theta_phi": 0, "trend_plunge": 1, "dip_strike": 2}
# UDFM requires the reduced DFN mesh.
UDFM_REDUCED_MESH_VISUAL_MODE = True


def serialize_dfn_parameter_value(value: Any) -> Any:
    # DFNGen requires numeric booleans, including nested values.
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, list):
        return [serialize_dfn_parameter_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple((serialize_dfn_parameter_value(item) for item in value))
    if isinstance(value, dict):
        return {key: serialize_dfn_parameter_value(item) for key, item in value.items()}
    return copy.deepcopy(value)


class DfnAdapterError(RuntimeError):
    pass


class PydfnworksAdapter:
    def __init__(self, config: DfnConfig, dfn_factory: Callable[..., Any] | None = None) -> None:
        self.config = config.copy()
        self._dfn_factory = dfn_factory

    def validate(self) -> None:
        errors = self.config.validate()
        if errors:
            raise DfnAdapterError(
                msg("text.dfn.configuration.is.invalid") + join_text("\n- ", errors)
            )

    def create_dfn_object(self, jobname: str | Path, ncpu: int = 1) -> Any:
        if not str(jobname).strip():
            raise DfnAdapterError(msg("text.jobname.cannot.be.empty"))
        if ncpu < 1:
            raise DfnAdapterError(msg("text.ncpu.must.be.at.least.1"))
        factory = self._dfn_factory or self._load_default_factory()
        return factory(jobname=str(jobname), ncpu=int(ncpu))

    @staticmethod
    def _load_default_factory() -> Callable[..., Any]:
        try:
            from pydfnworks import DFNWORKS
        except ImportError as exc:
            raise DfnAdapterError(
                msg("text.pydfnworks.is.not.installed.in.the.current.python.environment")
            ) from exc
        return DFNWORKS

    @staticmethod
    def _set_param(dfn: Any, name: str, value: Any) -> None:
        try:
            entry = dfn.params[name]
        except (AttributeError, KeyError, TypeError) as exc:
            raise DfnAdapterError(
                msg(
                    "diagnostic.pydfnworks_adapter.the.installed.pydfnworks.version.has.no.dfn.parameter.name",
                    name=f"{name!r}",
                )
            ) from exc
        serialized = serialize_dfn_parameter_value(value)
        if isinstance(entry, dict) and "value" in entry:
            entry["value"] = serialized
        else:
            dfn.params[name] = serialized

    def runtime_parameters(self) -> dict[str, Any]:
        values = self.resolved_parameters()
        values["framOn"] = True if self.config.fram.enabled is None else self.config.fram.enabled
        if self.config.uses_stochastic() and self.config.stop_rule.mode == "count":
            values["nPoly"] = self.config.stop_rule.n_poly
        return {name: serialize_dfn_parameter_value(value) for name, value in values.items()}

    def apply_global_parameters(self, dfn: Any) -> None:
        values = self.runtime_parameters()
        for name, value in values.items():
            self._set_param(dfn, name, value)

    @staticmethod
    def _hydraulic_arguments(hydraulic: Any) -> dict[str, Any]:
        if hydraulic.relationship == "constant":
            return {
                "hy_variable": "aperture",
                "hy_function": "constant",
                "hy_params": {"mu": hydraulic.constant_aperture},
            }
        return {
            "hy_variable": "aperture",
            "hy_function": "correlated",
            "hy_params": {"alpha": hydraulic.alpha, "beta": hydraulic.beta},
        }

    def add_stochastic_families(self, dfn: Any) -> None:
        if not self.config.uses_stochastic():
            return
        for family in self.config.stochastic_families:
            kwargs: dict[str, Any] = {
                "shape": family.shape,
                "aspect": family.aspect,
                "distribution": family.size.distribution,
                "kappa": family.orientation.kappa,
                "beta_distribution": family.beta_distribution,
                "beta": family.beta,
                "number_of_points": family.number_of_points,
            }
            if self.config.orientation_mode == "theta_phi":
                kwargs.update(theta=family.orientation.theta, phi=family.orientation.phi)
            elif self.config.orientation_mode == "trend_plunge":
                kwargs.update(trend=family.orientation.trend, plunge=family.orientation.plunge)
            else:
                kwargs.update(dip=family.orientation.dip, strike=family.orientation.strike)
            if family.size.distribution == "tpl":
                kwargs.update(
                    alpha=family.size.alpha,
                    min_radius=family.size.min_radius,
                    max_radius=family.size.max_radius,
                )
            elif family.size.distribution == "log_normal":
                kwargs.update(
                    log_mean=family.size.log_mean,
                    log_std=family.size.log_std,
                    min_radius=family.size.min_radius,
                    max_radius=family.size.max_radius,
                )
            elif family.size.distribution == "exp":
                kwargs.update(
                    exp_mean=family.size.exp_mean,
                    min_radius=family.size.min_radius,
                    max_radius=family.size.max_radius,
                )
            else:
                kwargs["constant"] = family.size.constant_radius
            if self.config.stop_rule.mode == "p32":
                kwargs["p32"] = family.p32
            else:
                kwargs["probability"] = family.probability
            kwargs.update(self._hydraulic_arguments(family.hydraulic))
            dfn.add_fracture_family(**kwargs)

    def add_user_defined_fractures(self, dfn: Any) -> None:
        for fracture in self.config.user_defined_fractures:
            dfn.add_user_fract(
                shape="rect",
                radii=fracture.radii,
                aspect_ratio=fracture.aspect_ratio,
                translation=list(fracture.translation),
                normal_vector=list(fracture.normal_vector),
                permeability=fracture.permeability,
            )

    def build(self, jobname: str | Path, ncpu: int = 1) -> Any:
        self.validate()
        dfn = self.create_dfn_object(jobname, ncpu)
        self.apply_global_parameters(dfn)
        self.add_stochastic_families(dfn)
        self.add_user_defined_fractures(dfn)
        return dfn

    def resolved_parameters(self) -> dict[str, Any]:
        cfg = self.config
        uses_stochastic = cfg.uses_stochastic()
        fram_enabled = True if cfg.fram.enabled is None else bool(cfg.fram.enabled)
        selected = set(cfg.connectivity.boundary_faces)
        if not cfg.connectivity.filter_by_boundary or not selected:
            selected = {"+x", "-x"}
        boundary_faces = [face in selected for face in BOUNDARY_FACES]
        return {
            "domainSize": list(cfg.domain_size),
            "domainSizeIncrease": cfg.normalized_domain_size_increase(),
            "seed": cfg.seed if uses_stochastic else 1,
            "stopCondition": 1 if not uses_stochastic or cfg.stop_rule.mode == "p32" else 0,
            "orientationOption": ORIENTATION_OPTION_MAP[cfg.orientation_mode]
            if uses_stochastic
            else 0,
            "angleOption": "degree",
            "h": cfg.h,
            "visualizationMode": UDFM_REDUCED_MESH_VISUAL_MODE,
            "disableFram": not fram_enabled,
            "rejectsPerFracture": cfg.fram.rejects_per_fracture,
            "radiiListIncrease": cfg.fram.radii_list_increase,
            "printRejectReasons": cfg.fram.print_reject_reasons,
            "tripleIntersections": cfg.fram.triple_intersections,
            "boundaryFaces": boundary_faces,
            "keepOnlyLargestCluster": cfg.connectivity.keep_only_largest_cluster,
            "ignoreBoundaryFaces": False,
            "keepIsolatedFractures": cfg.connectivity.keep_isolated_fractures,
            "outputAllRadii": cfg.output.output_all_radii,
            "outputAcceptedRadiiPerFamily": cfg.output.output_accepted_radii_per_family,
            "outputFinalRadiiPerFamily": cfg.output.output_final_radii_per_family,
        }
