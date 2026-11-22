from __future__ import annotations

import copy
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from fracrtm.messages import join_text, prepare_json
from fracrtm.messages import message as msg

DFN_SCHEMA_VERSION = "1.1"
SUPPORTED_DFN_SCHEMA_VERSIONS = {"1.0", DFN_SCHEMA_VERSION}
BOUNDARY_FACES = ("+x", "-x", "+y", "-y", "+z", "-z")
ORIENTATION_MODES = {"dip_strike", "theta_phi", "trend_plunge"}


class DfnConfigError(ValueError):
    pass


def _finite(value: Any) -> bool:
    from .contracts import finite_real

    return finite_real(value)


@dataclass
class StopRuleConfig:
    mode: str | None = "p32"
    n_poly: int | None = 100

    def validate(self):
        errors = []
        if self.mode not in {"p32", "count"}:
            errors.append(msg("validation.termination.mode.must.be.p32.or.count"))
        if self.mode == "count" and (not isinstance(self.n_poly, int) or self.n_poly <= 0):
            errors.append(
                msg("validation.for.count.based.termination.npoly.must.be.greater.than.0")
            )
        return errors

    @classmethod
    def from_dict(cls, d):
        return cls(mode=d.get("mode", "p32"), n_poly=d.get("n_poly", 100))


@dataclass
class SizeDistributionConfig:
    distribution: str = "tpl"
    alpha: float = 2.0
    log_mean: float = 1.0
    log_std: float = 0.5
    exp_mean: float = 1.0
    min_radius: float = 1.0
    max_radius: float = 10.0
    constant_radius: float = 5.0

    def minimum_radius(self):
        return self.constant_radius if self.distribution == "constant" else self.min_radius

    def validate(self):
        e = []
        if self.distribution not in {"tpl", "log_normal", "exp", "constant"}:
            return [msg("validation.invalid.size.distribution")]
        if self.distribution == "tpl" and (not _finite(self.alpha) or self.alpha <= 0):
            e.append(msg("validation.power.law.exponent.alpha.must.be.greater.than.0"))
        if self.distribution == "log_normal" and (
            not _finite(self.log_mean) or not _finite(self.log_std) or self.log_std <= 0
        ):
            e.append(
                msg(
                    "validation.log.mean.must.be.finite.and.log.standard.deviation.must.be.greater.than.0"
                )
            )
        if self.distribution == "exp" and (not _finite(self.exp_mean) or self.exp_mean <= 0):
            e.append(msg("validation.exponential.mean.must.be.greater.than.0"))
        if self.distribution == "constant":
            if not _finite(self.constant_radius) or self.constant_radius <= 0:
                e.append(msg("validation.fixed.radius.must.be.greater.than.0"))
        elif (
            not _finite(self.min_radius)
            or self.min_radius <= 0
            or (not _finite(self.max_radius))
            or (self.max_radius <= self.min_radius)
        ):
            e.append(msg("validation.maximum.radius.must.exceed.a.positive.minimum.radius"))
        return e

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass
class OrientationConfig:
    dip: float = 60.0
    strike: float = 0.0
    theta: float = 60.0
    phi: float = 0.0
    trend: float = 0.0
    plunge: float = 0.0
    kappa: float = 20.0

    def validate(self, mode="dip_strike"):
        e = []
        ranges = {
            "dip_strike": (
                (msg("text.dip"), self.dip, 0, 90, True),
                (msg("text.strike"), self.strike, 0, 360, False),
            ),
            "theta_phi": (("Theta", self.theta, 0, 180, True), ("Phi", self.phi, 0, 360, False)),
            "trend_plunge": (
                (msg("text.trend"), self.trend, 0, 360, False),
                (msg("text.plunge"), self.plunge, 0, 90, True),
            ),
        }
        for label, value, low, high, inclusive in ranges.get(mode, ()):
            if (
                not _finite(value)
                or value < low
                or value > high
                or (not inclusive and value == high)
            ):
                e.append(msg("validation.range", field=label))
        if not _finite(self.kappa) or self.kappa < 0:
            e.append(msg("validation.kappa.must.be.finite.and.nonnegative"))
        return e

    @classmethod
    def from_dict(cls, d):
        return cls(
            **{
                key: None if d[key] is None else float(d[key])
                for key in cls.__dataclass_fields__
                if key in d
            }
        )


@dataclass
class HydraulicConfig:
    relationship: str = "correlated"
    constant_aperture: float = 0.0001
    alpha: float = 5e-05
    beta: float = 0.5

    def validate(self):
        if self.relationship not in {"constant", "correlated"}:
            return [msg("validation.aperture.model.must.be.constant.or.radius.dependent")]
        if self.relationship == "constant":
            return (
                []
                if _finite(self.constant_aperture) and self.constant_aperture > 0
                else [msg("validation.constant.aperture.must.be.greater.than.0")]
            )
        e = []
        if not _finite(self.alpha) or self.alpha <= 0:
            e.append(msg("validation.aperture.coefficient.alpha.must.be.greater.than.0"))
        if not _finite(self.beta):
            e.append(msg("validation.aperture.exponent.beta.must.be.finite"))
        return e

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass
class StochasticFamilyConfig:
    name: str = "Family 1"
    shape: str = "ell"
    size: SizeDistributionConfig = field(default_factory=SizeDistributionConfig)
    orientation: OrientationConfig = field(default_factory=OrientationConfig)
    p32: float | None = 0.1
    probability: float | None = 1.0
    hydraulic: HydraulicConfig = field(default_factory=HydraulicConfig)
    aspect: float = 1.0
    beta_distribution: int = 0
    beta: float = 0.0
    number_of_points: int = 8

    def validate(self, stop_mode, orientation_mode):
        e = []
        if not self.name.strip():
            e.append(msg("validation.name.must.not.be.empty"))
        if self.shape not in {"ell", "rect"}:
            e.append(msg("validation.invalid.fracture.shape"))
        e += (
            self.size.validate()
            + self.orientation.validate(orientation_mode)
            + self.hydraulic.validate()
        )
        if not _finite(self.aspect) or self.aspect <= 0:
            e.append(msg("validation.aspect.ratio.must.be.greater.than.0"))
        if self.beta_distribution not in {0, 1}:
            e.append(msg("validation.invalid.in.plane.rotation.mode"))
        if self.beta_distribution == 1 and (not _finite(self.beta)):
            e.append(msg("validation.beta.must.be.finite"))
        if self.shape == "ell" and (
            not isinstance(self.number_of_points, int) or self.number_of_points < 5
        ):
            e.append(msg("validation.fracture.boundary.must.have.at.least.5.vertices"))
        if stop_mode == "p32" and (not _finite(self.p32) or self.p32 <= 0):
            e.append(msg("validation.p32.must.be.greater.than.0"))
        if stop_mode == "count" and (not _finite(self.probability) or self.probability <= 0):
            e.append(msg("validation.family.probability.must.be.greater.than.0"))
        return [join_text("", [self.name, ": ", x]) for x in e]

    @classmethod
    def from_dict(cls, d):
        p32 = d.get("p32", 0.1)
        probability = d.get("probability", 1)
        return cls(
            name=str(d.get("name", "Family 1")),
            shape=str(d.get("shape", "ell")),
            size=SizeDistributionConfig.from_dict(d.get("size", {})),
            orientation=OrientationConfig.from_dict(d.get("orientation", {})),
            p32=None if p32 is None else float(p32),
            probability=None if probability is None else float(probability),
            hydraulic=HydraulicConfig.from_dict(d.get("hydraulic", {})),
            aspect=float(d.get("aspect", 1)),
            beta_distribution=int(d.get("beta_distribution", 0)),
            beta=float(d.get("beta", 0)),
            number_of_points=int(d.get("number_of_points", 8)),
        )


def blank_stochastic_family_draft(name: str) -> StochasticFamilyConfig:
    return StochasticFamilyConfig(
        name=name,
        shape=None,
        size=SizeDistributionConfig(
            distribution=None,
            alpha=None,
            log_mean=None,
            log_std=None,
            exp_mean=None,
            min_radius=None,
            max_radius=None,
            constant_radius=None,
        ),
        orientation=OrientationConfig(
            dip=None, strike=None, theta=None, phi=None, trend=None, plunge=None, kappa=None
        ),
        p32=None,
        probability=None,
        hydraulic=HydraulicConfig(relationship=None, constant_aperture=None, alpha=None, beta=None),
        aspect=1.0,
        beta_distribution=0,
        beta=0.0,
        number_of_points=8,
    )


@dataclass
class UserDefinedFractureConfig:
    id: str = "fracture_1"
    name: str = "Fracture 1"
    shape: str = "rect"
    radii: float = 0.6
    aspect_ratio: float = 1.0
    translation: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    normal_vector: list[float] = field(default_factory=lambda: [0.0, 0.0, 1.0])
    permeability: float = 1e-12

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.id.strip():
            errors.append(msg("validation.id.must.not.be.empty"))
        if not self.name.strip():
            errors.append(msg("validation.name.must.not.be.empty"))
        if self.shape != "rect":
            errors.append(msg("validation.custom_fracture.shape"))
        if not _finite(self.radii) or self.radii <= 0:
            errors.append(msg("validation.radii.must.be.greater.than.0"))
        if not _finite(self.aspect_ratio) or self.aspect_ratio <= 0:
            errors.append(msg("validation.aspect.ratio.must.be.greater.than.0.2"))
        if len(self.translation) != 3 or not all((_finite(x) for x in self.translation)):
            errors.append(msg("validation.the.center.must.contain.three.finite.coordinates"))
        if len(self.normal_vector) != 3 or not all((_finite(x) for x in self.normal_vector)):
            errors.append(msg("validation.normal.vector.must.contain.three.finite.values"))
        elif math.isclose(sum((float(x) ** 2 for x in self.normal_vector)), 0.0, abs_tol=1e-30):
            errors.append(msg("validation.normal.vector.must.not.be.0.0.0"))
        if not _finite(self.permeability) or self.permeability <= 0:
            errors.append(msg("validation.permeability.must.be.greater.than.0"))
        return [join_text("", [self.name, ": ", item]) for item in errors]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserDefinedFractureConfig":
        if "radii" in data or "normal_vector" in data:
            return cls(
                id=str(data["id"])
                if "id" in data
                else str(data.get("name", "fracture_1")).strip().replace(" ", "_").lower(),
                name=str(data.get("name", "Fracture 1")),
                shape=str(data.get("shape", "rect")),
                radii=None if data.get("radii") is None else float(data["radii"]),
                aspect_ratio=None
                if data.get("aspect_ratio") is None
                else float(data["aspect_ratio"]),
                translation=[
                    None if x is None else float(x) for x in data.get("translation", [0, 0, 0])
                ],
                normal_vector=[
                    None if x is None else float(x)
                    for x in data.get("normal_vector", [None, None, None])
                ],
                permeability=None
                if data.get("permeability") is None
                else float(data["permeability"]),
            )
        raise DfnConfigError(msg("repair.legacy_orientation", name=data.get("name", "fracture")))


@dataclass
class FractureModelConfig:
    stochastic_families: list[StochasticFamilyConfig] = field(
        default_factory=lambda: [StochasticFamilyConfig()]
    )
    user_defined_fractures: list[UserDefinedFractureConfig] = field(default_factory=list)


@dataclass
class ConnectivityConfig:
    filter_by_boundary: bool = True
    boundary_faces: list[str] = field(default_factory=lambda: ["+x", "-x"])
    keep_only_largest_cluster: bool = True
    keep_isolated_fractures: bool = False

    def validate(self):
        e = []
        unknown = set(self.boundary_faces) - set(BOUNDARY_FACES)
        if unknown:
            e.append(msg("validation.boundary", face=sorted(unknown)))
        if self.filter_by_boundary and len(self.boundary_faces) < 2:
            e.append(msg("text.select.at.least.two.boundary.faces.to.filter.the.network"))
        return e

    @classmethod
    def from_dict(cls, d):
        return cls(
            bool(d.get("filter_by_boundary", True)),
            [str(x).lower() for x in d.get("boundary_faces", [])],
            bool(d.get("keep_only_largest_cluster", True)),
            bool(d.get("keep_isolated_fractures", False)),
        )


@dataclass
class FramConfig:
    enabled: bool | None = True
    rejects_per_fracture: int | None = 10
    radii_list_increase: float | None = 0.1
    print_reject_reasons: bool | None = False
    triple_intersections: bool | None = False

    def validate(self, h=None, minimum_radius=None):
        e = []
        if not isinstance(self.rejects_per_fracture, int) or self.rejects_per_fracture <= 0:
            e.append(msg("validation.retries.per.fracture.must.be.greater.than.0"))
        if not _finite(self.radii_list_increase) or self.radii_list_increase < 0:
            e.append(msg("validation.the.radius.list.increase.must.be.nonnegative"))
        if self.enabled and minimum_radius and _finite(h):
            if h >= minimum_radius / 10:
                e.append(
                    msg(
                        "text.with.fram.enabled.h.should.be.less.than.1.10.of.the.minimum.stochastic"
                    )
                )
            if h <= minimum_radius / 1000:
                e.append(
                    msg(
                        "text.with.fram.enabled.h.should.exceed.1.1000.of.the.minimum.stochastic.fracture.radius"
                    )
                )
        return e

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass
class OutputConfig:
    output_all_radii: bool = False
    output_accepted_radii_per_family: bool = True
    output_final_radii_per_family: bool = True
    create_output_report: bool = True

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass
class DfnConfig:
    schema_version: str = DFN_SCHEMA_VERSION
    domain_size: list[float | None] = field(default_factory=lambda: [100.0, 100.0, 50.0])
    domain_size_increase: list[float | None] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    h: float | None = 0.05
    seed: int | None = 12345
    stop_rule: StopRuleConfig = field(default_factory=StopRuleConfig)
    orientation_mode: str | None = "dip_strike"
    fracture_model: FractureModelConfig = field(default_factory=FractureModelConfig)
    connectivity: ConnectivityConfig = field(default_factory=ConnectivityConfig)
    fram: FramConfig = field(default_factory=FramConfig)
    output: OutputConfig = field(default_factory=OutputConfig)

    @property
    def stochastic_families(self):
        return self.fracture_model.stochastic_families

    @stochastic_families.setter
    def stochastic_families(self, value):
        self.fracture_model.stochastic_families = value

    @property
    def user_defined_fractures(self):
        return self.fracture_model.user_defined_fractures

    @user_defined_fractures.setter
    def user_defined_fractures(self, value):
        self.fracture_model.user_defined_fractures = value

    @property
    def composition_label(self):
        if self.stochastic_families and self.user_defined_fractures:
            return msg("text.stochastic.user.defined")
        if self.user_defined_fractures:
            return msg("text.user.defined.fractures")
        if self.stochastic_families:
            return msg("text.stochastic.fractures")
        return msg("text.undefined")

    def uses_stochastic(self):
        return bool(self.stochastic_families)

    def uses_user_defined(self):
        return bool(self.user_defined_fractures)

    def minimum_stochastic_radius(self):
        values = [x.size.minimum_radius() for x in self.stochastic_families]
        values = [x for x in values if _finite(x) and x > 0]
        return min(values) if values else None

    def normalized_domain_size_increase(self) -> list[float]:
        values = list(self.domain_size_increase)
        return [0.0 if value is None else float(value) for value in values]

    def validate_user_blocking(self) -> list[str]:
        errors: list[str] = []
        if len(self.domain_size) != 3 or any((value is None for value in self.domain_size)):
            errors.append(msg("text.the.domain.dimensions.are.incomplete"))
        elif not all((_finite(value) and float(value) > 0 for value in self.domain_size)):
            errors.append(msg("validation.domain.dimensions.must.be.greater.than.0"))
        expansion = list(self.domain_size_increase)
        if len(expansion) != 3 or any(
            (value is not None and (not _finite(value) or float(value) < 0) for value in expansion)
        ):
            errors.append(msg("validation.domain.extension.must.be.nonnegative"))
        if not self.stochastic_families and (not self.user_defined_fractures):
            errors.append(msg("text.no.fractures.have.been.added"))
        if self.stochastic_families:
            if self.seed is None:
                errors.append(msg("text.random.seed.has.not.been.set"))
            elif (
                not isinstance(self.seed, int)
                or isinstance(self.seed, bool)
                or (not 1 <= self.seed <= 4294967295)
            ):
                errors.append(msg("validation.random.seed.must.be.an.integer.from.1.to.4294967295"))
            if self.stop_rule.mode is None:
                errors.append(msg("text.stochastic.fracture.termination.mode.has.not.been.set"))
            elif self.stop_rule.mode not in {"p32", "count"}:
                errors.append(msg("validation.invalid.stochastic.fracture.termination.mode"))
            if self.orientation_mode is None:
                errors.append(
                    msg("text.stochastic.fracture.orientation.convention.has.not.been.set")
                )
            elif self.orientation_mode not in ORIENTATION_MODES:
                errors.append(msg("validation.invalid.stochastic.fracture.orientation.convention"))
            for family in self.stochastic_families:
                if self.stop_rule.mode == "p32" and (not _finite(family.p32) or family.p32 <= 0):
                    errors.append(msg("validation.family_p32", name=family.name))
                elif self.stop_rule.mode == "count" and (
                    not _finite(family.probability) or family.probability <= 0
                ):
                    errors.append(msg("validation.family_weight", name=family.name))
                elif family.validate(self.stop_rule.mode, self.orientation_mode):
                    errors.append(msg("validation.family_incomplete", name=family.name))
            if self.stop_rule.mode == "count":
                if not isinstance(self.stop_rule.n_poly, int) or self.stop_rule.n_poly <= 0:
                    errors.append(msg("text.stochastic.fracture.target.count.has.not.been.set"))
                elif not math.isclose(
                    sum((item.probability for item in self.stochastic_families)),
                    1.0,
                    rel_tol=1e-08,
                    abs_tol=1e-08,
                ):
                    errors.append(
                        msg("validation.stochastic.family.generation.weights.must.sum.to.1")
                    )
        for fracture in self.user_defined_fractures:
            if fracture.validate():
                errors.append(msg("validation.custom_incomplete", name=fracture.name))
        # DFNGen requires a mesh scale even when FRAM is disabled.
        if self.h is None:
            errors.append(msg("text.dfn.mesh.scale.h.has.not.been.set"))
        elif not _finite(self.h) or self.h <= 0:
            errors.append(msg("validation.dfn.mesh.scale.h.must.be.greater.than.0"))
        if self.connectivity.filter_by_boundary and (not self.connectivity.boundary_faces):
            errors.append(
                msg("text.boundary.filtering.is.enabled.but.no.boundary.faces.have.been.selected")
            )
        return errors

    def validate(self):
        from .contracts import numeric_type_issues

        typed_issues = numeric_type_issues(self)
        if typed_issues:
            return [
                msg("validation.resource_detail", path=path, file="dfn_config.json", reason=reason)
                for path, reason in typed_issues
            ]
        e = []
        if self.schema_version != DFN_SCHEMA_VERSION:
            e.append(msg("validation.dfn_version", version=DFN_SCHEMA_VERSION))
        if len(self.domain_size) != 3 or not all((_finite(x) and x > 0 for x in self.domain_size)):
            e.append(msg("validation.the.domain.must.have.three.positive.lengths"))
        if len(self.domain_size_increase) != 3 or any(
            (
                value is not None and (not _finite(value) or float(value) < 0)
                for value in self.domain_size_increase
            )
        ):
            e.append(msg("validation.generation.domain.extension.must.be.nonnegative"))
        if not _finite(self.h) or self.h <= 0:
            e.append(msg("validation.dfn.mesh.scale.h.must.be.greater.than.0"))
        if not self.stochastic_families and (not self.user_defined_fractures):
            e.append(msg("text.add.at.least.one.stochastic.family.or.custom.fracture"))
        if self.stochastic_families:
            if (
                not isinstance(self.seed, int)
                or isinstance(self.seed, bool)
                or (not 1 <= self.seed <= 4294967295)
            ):
                e.append(msg("validation.random.seed.must.be.an.integer.between.1.and.4294967295"))
            if self.orientation_mode not in ORIENTATION_MODES:
                e.append(msg("validation.invalid.orientation.convention"))
            e += self.stop_rule.validate()
            names = [x.name.strip() for x in self.stochastic_families]
            if len(names) != len(set(names)):
                e.append(msg("validation.stochastic.family.names.must.be.unique"))
            for x in self.stochastic_families:
                e += x.validate(self.stop_rule.mode, self.orientation_mode)
            if (
                self.stop_rule.mode == "count"
                and all(_finite(x.probability) for x in self.stochastic_families)
                and (
                    not math.isclose(
                        sum((x.probability for x in self.stochastic_families)),
                        1,
                        rel_tol=1e-08,
                        abs_tol=1e-08,
                    )
                )
            ):
                e.append(
                    msg("validation.for.count.based.termination.family.probabilities.must.sum.to.1")
                )
        if self.user_defined_fractures:
            names = [x.name.strip() for x in self.user_defined_fractures]
            ids = [x.id.strip() for x in self.user_defined_fractures]
            if len(names) != len(set(names)):
                e.append(msg("validation.custom.fracture.names.must.be.unique"))
            if len(ids) != len(set(ids)):
                e.append(msg("validation.custom.fracture.ids.must.be.unique"))
            for x in self.user_defined_fractures:
                e += x.validate()
        return (
            e
            + self.connectivity.validate()
            + self.fram.validate(self.h, self.minimum_stochastic_radius())
        )

    def to_dict(self):
        data = {
            "schema_version": DFN_SCHEMA_VERSION,
            "domain_size": copy.deepcopy(self.domain_size),
            "domain_size_increase": copy.deepcopy(self.domain_size_increase),
            "h": self.h,
            "fracture_model": asdict(self.fracture_model),
            "connectivity": asdict(self.connectivity),
            "fram": asdict(self.fram),
            "output": asdict(self.output),
        }
        if self.uses_stochastic():
            data.update(
                {
                    "seed": self.seed,
                    "stop_rule": asdict(self.stop_rule),
                    "orientation_mode": self.orientation_mode,
                }
            )
        return data

    def copy(self):
        return copy.deepcopy(self)

    def save_json(self, filepath):
        from fracrtm.project.storage import atomic_json

        atomic_json(Path(filepath), prepare_json(self.to_dict()))

    @classmethod
    def load_json(cls, filepath):
        try:
            with Path(filepath).open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            raise DfnConfigError(msg("validation.dfn_read", reason=exc)) from exc
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data):
        if (
            not isinstance(data, dict)
            or str(data.get("schema_version")) not in SUPPORTED_DFN_SCHEMA_VERSIONS
        ):
            raise DfnConfigError(msg("validation.dfn_unsupported", version=DFN_SCHEMA_VERSION))
        required = {"schema_version", "domain_size"}
        missing = sorted(required - set(data))
        if missing:
            raise DfnConfigError(
                msg("validation.required.dfn.configuration.fields.are.missing")
                + join_text(", ", missing)
            )
        from .contracts import check_serialized_numbers

        check_serialized_numbers(data)
        d = copy.deepcopy(data)
        if "fracture_model" in d:
            model = d["fracture_model"] or {}
            stochastic_data = model.get("stochastic_families", [])
            user_data = model.get("user_defined_fractures", [])
        else:
            legacy_mode = str(d.get("generation_mode", "stochastic"))
            stochastic_data = (
                d.get("stochastic_families", []) if legacy_mode in {"stochastic", "hybrid"} else []
            )
            user_data = (
                d.get("deterministic_fractures", [])
                if legacy_mode in {"deterministic", "hybrid"}
                else []
            )
        if stochastic_data:
            random_required = {"seed", "stop_rule", "orientation_mode"}
            random_missing = sorted(random_required - set(d))
            if random_missing:
                raise DfnConfigError(
                    msg("text.stochastic.fracture.configuration.fields.are.missing")
                    + join_text(", ", random_missing)
                )
            if d["seed"] is not None and (
                not isinstance(d["seed"], int) or isinstance(d["seed"], bool)
            ):
                raise DfnConfigError(msg("validation.dfn.configuration.seed.must.be.an.integer"))
        legacy_fram = d.get("fram") or {}
        h_value = d.get("h", legacy_fram.get("h"))
        return cls(
            schema_version=DFN_SCHEMA_VERSION,
            domain_size=[None if x is None else float(x) for x in d["domain_size"]],
            domain_size_increase=[
                None if x is None else float(x) for x in d.get("domain_size_increase", [0, 0, 0])
            ],
            h=None if h_value is None else float(h_value),
            seed=None if d.get("seed") is None else int(d.get("seed", 1)),
            stop_rule=StopRuleConfig.from_dict(d.get("stop_rule", {"mode": "p32", "n_poly": 100})),
            orientation_mode=d.get("orientation_mode", "dip_strike"),
            fracture_model=FractureModelConfig(
                stochastic_families=[StochasticFamilyConfig.from_dict(x) for x in stochastic_data],
                user_defined_fractures=[UserDefinedFractureConfig.from_dict(x) for x in user_data],
            ),
            connectivity=ConnectivityConfig.from_dict(d.get("connectivity", {})),
            fram=FramConfig.from_dict(legacy_fram),
            output=OutputConfig.from_dict(d.get("output", {})),
        )
