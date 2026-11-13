from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fracrtm.messages import join_text
from fracrtm.messages import message as msg

from .generation_records import atomic_json, safe_resource

PROJECT_SCHEMA = "2.0"
_WSLPATH_CACHE: dict[tuple[str, str, str], str] = {}


@dataclass(frozen=True)
class ProjectWorkspace:
    local_root: Path
    project_id: str
    wsl_workspace_root: str = "/root/FracRTMWorkspace"

    @classmethod
    def open(cls, root: str | Path, wsl_workspace_root: str = "/root/FracRTMWorkspace"):
        path = Path(root).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        from .storage import recover_project, recover_replacement

        recover_project(path)
        for name in ("dfn", "octree"):
            recover_replacement(path / name)
        meta_path = path / "project.json"
        data = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        location = str(path).replace("\\", "/").casefold()
        previous = data.get("project_id")
        try:
            valid_uuid = str(uuid.UUID(str(previous))) == previous
        except (ValueError, TypeError, AttributeError):
            valid_uuid = False
        # Relocated projects get new project IDs; run IDs remain unchanged.
        if not valid_uuid or data.get("identity_location") != location:
            data["project_id"] = str(uuid.uuid4())
            if previous:
                data.setdefault("identity_history", []).append(
                    {
                        "previous_id": previous,
                        "previous_location": data.get("identity_location"),
                        "changed_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            data["identity_location"] = location
            data["project_schema"] = PROJECT_SCHEMA
            data.setdefault("name", path.name)
            data.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            atomic_json(meta_path, data)
        return cls(path, data["project_id"], wsl_workspace_root.rstrip("/"))

    @property
    def display_name(self):
        try:
            return json.loads((self.local_root / "project.json").read_text(encoding="utf-8")).get(
                "name", self.local_root.name
            )
        except (OSError, ValueError):
            return self.local_root.name

    @property
    def local_dfn_dir(self):
        return self.local_root / "dfn"

    @property
    def local_octree_dir(self):
        return self.local_root / "octree"

    @property
    def local_database_dir(self):
        return self.local_root / "database"

    @property
    def local_pflotran_dir(self):
        return self.local_root / "pflotran"

    @property
    def local_results_dir(self):
        return self.local_root / "results"

    @property
    def local_logs_dir(self):
        return self.local_root / "logs"

    @property
    def wsl_job_dir(self):
        return join_text("", [self.wsl_workspace_root, "/", self.project_id])

    def ensure(self):
        for p in (
            self.local_root,
            self.local_dfn_dir,
            self.local_octree_dir,
            self.local_database_dir,
            self.local_pflotran_dir,
            self.local_pflotran_dir / "output",
            self.local_results_dir,
            self.local_logs_dir,
        ):
            p.mkdir(parents=True, exist_ok=True)
        meta = self.local_root / "project.json"
        now = datetime.now(timezone.utc).isoformat()
        created = now
        if meta.is_file():
            try:
                created = json.loads(meta.read_text(encoding="utf-8")).get("created_at", now)
            except Exception:
                pass
        data = json.loads(meta.read_text(encoding="utf-8")) if meta.is_file() else {}
        data.update(
            {
                "project_schema": PROJECT_SCHEMA,
                "project_id": self.project_id,
                "name": data.get("name", self.local_root.name),
                "identity_location": str(self.local_root).replace("\\", "/").casefold(),
                "created_at": created,
                "modified_at": now,
                "paths": {
                    "dfn": "dfn",
                    "octree": "octree",
                    "database": "database",
                    "pflotran": "pflotran",
                    "results": "results",
                },
            }
        )
        atomic_json(meta, data)

    def relative_path(self, path):
        try:
            return Path(path).resolve().relative_to(self.local_root).as_posix()
        except ValueError as exc:
            raise ValueError(
                msg("validation.path.must.be.inside.the.current.project.directory")
            ) from exc

    def resolve_local(self, relative):
        return safe_resource(self.local_root, relative)

    def to_wsl_path(self, path, distribution: str, user: str = "root", warning=None):
        windows_path = str(Path(path).resolve()).replace("\\", "/")
        cache_key = (distribution, user, windows_path.casefold())
        if cache_key in _WSLPATH_CACHE:
            return _WSLPATH_CACHE[cache_key]
        command = [
            "wsl.exe",
            "-d",
            distribution,
            "-u",
            user,
            "--",
            "/usr/bin/wslpath",
            "-a",
            windows_path,
        ]
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        if result.returncode != 0:
            raise ValueError(result.stderr.strip() or msg("text.wslpath.conversion.failed"))
        if result.stderr.strip() and warning is not None:
            warning(result.stderr.strip())
        converted = result.stdout.strip()
        if not converted:
            raise ValueError(msg("text.wslpath.did.not.return.a.converted.path"))
        _WSLPATH_CACHE[cache_key] = converted
        return converted
