"""Project format 1.0 metadata."""

import json
import os
from pathlib import Path

from fracrtm.messages import message as msg

PROJECT_SCHEMA = "1.0"


def project_data(project):
    path = Path(project) / "project.json"
    if not path.is_file():
        return {"project_schema": PROJECT_SCHEMA}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("project_schema") != PROJECT_SCHEMA:
        raise ValueError(msg("project.unsupported_format"))
    return data


def read_record(project, relative):
    return project_data(project).get("artifact_records", {}).get(relative)


def add_records(transaction, project, updates):
    data = project_data(project)
    data.setdefault("artifact_records", {}).update(updates)
    transaction.add_json("project.json", data)


def write_record(project, relative, value):
    from .storage import FileTransaction, ProjectLock

    with ProjectLock(project):
        transaction = FileTransaction(project)
        add_records(transaction, project, {relative: value})
        transaction.commit()


def application_logs(project_id):
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/state"))
    return base / "FracRTM-GUI/logs" / project_id
