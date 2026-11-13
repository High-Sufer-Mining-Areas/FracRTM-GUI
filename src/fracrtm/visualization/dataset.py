from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from fracrtm.messages import message as msg

CONTINUOUS = "continuous"
CATEGORICAL = "categorical"


@dataclass(frozen=True)
class VisualizationArray:
    name: str
    association: str
    kind: str = CONTINUOUS
    unit: str = ""
    preferred_scale: str = "linear"
    labels: dict[int, str] = field(default_factory=dict)
    display_name: str | None = None

    @property
    def label(self):
        return self.display_name if self.display_name is not None else self.name

    def __post_init__(self):
        if self.association not in {"point", "cell"}:
            raise ValueError(msg("text.association.must.be.point.or.cell"))
        if self.kind not in {CONTINUOUS, CATEGORICAL}:
            raise ValueError(msg("text.kind.must.be.continuous.or.categorical"))
        if self.preferred_scale not in {"linear", "log"}:
            raise ValueError(msg("text.preferred.scale.must.be.linear.or.log"))


@dataclass
class VisualizationDataset:
    name: str
    geometry: Any
    arrays: dict[str, VisualizationArray] = field(default_factory=dict)
    time_values: tuple[float | None, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    snapshots: Sequence[Any] = ()

    @property
    def default_array(self) -> str | None:
        return self.metadata.get("default_array") or next(iter(self.arrays), None)

    @property
    def default_representation(self) -> str:
        return self.metadata.get("default_representation", "surface")
