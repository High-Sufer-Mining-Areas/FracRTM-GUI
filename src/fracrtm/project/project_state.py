from dataclasses import dataclass
from enum import Enum


class ArtifactState(Enum):
    NOT_GENERATED = "not_generated"
    VALID = "valid"
    STALE = "stale"
    RUNNING = "running"
    FAILED = "failed"
    UNVERIFIED = "unverified"


@dataclass
class ProjectState:
    dfn_state: ArtifactState = ArtifactState.NOT_GENERATED
    octree_state: ArtifactState = ArtifactState.NOT_GENERATED
    upscale_state: ArtifactState = ArtifactState.NOT_GENERATED
    pflotran_input_state: ArtifactState = ArtifactState.NOT_GENERATED
    simulation_state: ArtifactState = ArtifactState.NOT_GENERATED
    pflotran_config_state: ArtifactState = ArtifactState.NOT_GENERATED

    def invalidate_from_dfn(self):
        self.dfn_state = ArtifactState.STALE
        self.octree_state = ArtifactState.STALE
        self.upscale_state = ArtifactState.STALE
        self.pflotran_input_state = ArtifactState.STALE
        self.simulation_state = ArtifactState.STALE

    def invalidate_from_octree(self):
        self.octree_state = ArtifactState.STALE
        self.upscale_state = ArtifactState.STALE
        self.pflotran_input_state = ArtifactState.STALE
        self.simulation_state = ArtifactState.STALE

    def invalidate_from_upscale(self):
        self.upscale_state = ArtifactState.STALE
        self.pflotran_input_state = ArtifactState.STALE
        self.simulation_state = ArtifactState.STALE

    def invalidate_from_pflotran_config(self):
        self.pflotran_config_state = ArtifactState.STALE
        self.pflotran_input_state = ArtifactState.STALE
        self.simulation_state = ArtifactState.STALE
