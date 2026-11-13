from __future__ import annotations

from .continuum_config import ContinuumConfig
from .dfn_config import (
    ConnectivityConfig,
    DfnConfig,
    FractureModelConfig,
    FramConfig,
    StopRuleConfig,
)
from .pflotran import PFLOTRANConfig


class BlankConfigFactory:
    @staticmethod
    def dfn() -> DfnConfig:
        return DfnConfig(
            domain_size=[None, None, None],
            domain_size_increase=[None, None, None],
            h=None,
            seed=None,
            stop_rule=StopRuleConfig(mode=None, n_poly=None),
            orientation_mode=None,
            fracture_model=FractureModelConfig(stochastic_families=[], user_defined_fractures=[]),
            connectivity=ConnectivityConfig(filter_by_boundary=True, boundary_faces=["+x", "-x"]),
            fram=FramConfig(enabled=True),
        )

    @staticmethod
    def continuum() -> ContinuumConfig:
        return ContinuumConfig(
            base_cell_size=None,
            refinement_levels=None,
            matrix_permeability=None,
            matrix_porosity=None,
        )

    @staticmethod
    def pflotran() -> PFLOTRANConfig:
        config = PFLOTRANConfig(template_id="blank_unconfigured")
        config.simulation.task_type = None
        config.simulation.flow_state = None
        config.simulation.saturation_state = None
        config.simulation.flow_mode = None
        config.simulation.transport_mode = None
        config.time.final_time = None
        config.time.final_unit = None
        config.time.initial_timestep = None
        config.time.initial_timestep_unit = None
        config.time.maximum_timestep = None
        config.time.maximum_timestep_unit = None
        config.grid.file = ""
        config.fluid_properties.diffusion_coefficient = None
        return config
