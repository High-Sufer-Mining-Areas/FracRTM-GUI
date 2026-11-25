"""Stage-specific locations for completed task records."""

import json
from pathlib import Path

from fracrtm.messages import message as msg

from .metadata import project_data

STAGE_DIRECTORIES = {
    "dfn": "dfn/generation",
    "octree": "octree/generation/octree",
    "upscale": "octree/generation/upscale",
    "pflotran": "results",
}


def archive_root(project, stage):
    return Path(project) / STAGE_DIRECTORIES[stage]


def published_records(project):
    root = Path(project)
    project_data(root)
    paths = []
    for path in (root / "results").glob("run-*/result.json"):
        if json.loads(path.read_text(encoding="utf-8")).get("stage") != "pflotran":
            raise ValueError(msg("project.unsupported_format"))
        paths.append(path)
    for stage in ("dfn", "octree", "upscale"):
        for path in archive_root(root, stage).glob("*/result.json"):
            if json.loads(path.read_text(encoding="utf-8")).get("stage") != stage:
                raise ValueError(msg("project.unsupported_format"))
            paths.append(path)
    return paths
