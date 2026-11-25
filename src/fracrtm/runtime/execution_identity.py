from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from fracrtm.messages import message as msg
from fracrtm.project.runs import update_run
from fracrtm.runtime.wsl_arguments import dfn_python_args

PROBE = """import hashlib, importlib.metadata, json, os, platform, shutil, sys
result = {"python": sys.executable, "python_version": sys.version, "platform": platform.platform(), "home": os.environ.get("HOME"), "path": os.environ.get("PATH"), "tools": {}, "packages": {}}
result["installed_packages"] = sorted((distribution.metadata.get("Name", ""), distribution.version) for distribution in importlib.metadata.distributions())
for name in ("pydfnworks", "numpy", "scipy", "h5py"):
    try: result["packages"][name] = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError: result["packages"][name] = None
try:
    import pydfnworks
    result["pydfnworks_module"] = pydfnworks.__file__
    from pathlib import Path
    result["pydfnworks_sources"] = {}
    for source in sorted(Path(pydfnworks.__file__).parent.rglob("*.py")):
        result["pydfnworks_sources"][str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()
except ImportError:
    result["pydfnworks_module"] = None
tools = json.loads(sys.argv[1])
tools.update({name: os.environ.get(name) for name in ("DFNGEN_EXE", "LAGRIT_EXE", "PFLOTRAN_EXE")})
for name, value in tools.items():
    resolved = shutil.which(value) if value else None
    item = {"requested": value, "resolved": resolved}
    if resolved:
        digest = hashlib.sha256()
        with open(resolved, "rb") as stream:
            for chunk in iter(lambda: stream.read(1048576), b""): digest.update(chunk)
        item.update(sha256=digest.hexdigest(), size=os.path.getsize(resolved))
    result["tools"][name] = item
print("FRACRTM_ENVIRONMENT=" + json.dumps(result))
"""


def environment_probe_command(settings, *, stage="dfn"):
    tools = {
        "PFLOTRAN": settings.pflotran_executable,
        "MPI": settings.mpi_launcher,
    }
    arguments = ["-c", PROBE, json.dumps(tools)]
    if stage == "pflotran":
        from fracrtm.runtime.wsl_arguments import wsl_prefix

        return ["wsl.exe", *wsl_prefix(settings), settings.dfn_python, *arguments]
    return ["wsl.exe", *dfn_python_args(settings, arguments)]


def freeze_execution_code(task, sync, repository, remote_root, settings):
    package = Path(repository) / "fracrtm"
    paths = sorted(
        p
        for p in package.rglob("*")
        if p.suffix in {".py", ".json"} and "__pycache__" not in p.parts
    )
    if not paths:
        raise FileNotFoundError(msg("repair.execution_source_missing"))
    archive = task.directory / "execution_source.zip"
    records = []
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in paths:
            content = path.read_bytes()
            relative = path.relative_to(package.parent).as_posix()
            bundle.writestr(relative, content)
            records.append({"path": relative, "sha256": hashlib.sha256(content).hexdigest()})
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    remote_archive = remote_root + "/execution_source.zip"
    remote_source = remote_root + "/source"
    sync._run("/bin/cp", "--", sync._wslpath(archive), remote_archive)
    checked = sync._run("/usr/bin/sha256sum", "--", remote_archive).stdout.split()
    if not checked or checked[0] != digest:
        raise ValueError(msg("repair.execution_source_changed"))
    sync._run(settings.dfn_python, "-m", "zipfile", "-e", remote_archive, remote_source)
    command = environment_probe_command(settings, stage=task.stage)
    marker = command.index("--")
    probe_output = sync._run(*command[marker + 1 :]).stdout
    probe_records = [
        line[len("FRACRTM_ENVIRONMENT=") :]
        for line in probe_output.splitlines()
        if line.startswith("FRACRTM_ENVIRONMENT=")
    ]
    if len(probe_records) != 1:
        raise ValueError(msg("repair.environment_identity"))
    environment = json.loads(probe_records[0])
    update_run(
        task,
        execution_source={
            "archive": archive.name,
            "sha256": digest,
            "files": records,
            "remote_pythonpath": remote_source,
        },
        execution_environment=environment,
    )
    return remote_source
