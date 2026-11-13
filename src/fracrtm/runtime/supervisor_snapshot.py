import hashlib
import json
from pathlib import Path

from fracrtm.messages import current_locale, prepare_json
from fracrtm.messages import message as msg
from fracrtm.project.runs import update_run


def supervisor_resources(repository):
    package = Path(repository) / "fracrtm"
    return {
        "task_runner.py": package / "runtime/task_runner.py",
        "messages.py": package / "messages.py",
        "locales/en_US.json": package / "locales/en_US.json",
        "locales/zh_CN.json": package / "locales/zh_CN.json",
    }


def prepare_supervisor(task, sync, repository, remote_root):
    sources = supervisor_resources(repository)
    payloads = {name: path.read_bytes() for name, path in sources.items()}
    payloads["language.json"] = (
        json.dumps(prepare_json({"locale": current_locale()})) + "\n"
    ).encode("utf-8")
    remote_directory = str(remote_root).rstrip("/") + "/runtime"
    resources = {}
    for name, content in payloads.items():
        frozen = task.directory / "runtime" / name
        frozen.parent.mkdir(parents=True, exist_ok=True)
        with frozen.open("xb") as stream:
            stream.write(content)
        remote = remote_directory + "/" + name
        digest = hashlib.sha256(content).hexdigest()
        sync._run("/bin/mkdir", "-p", remote.rsplit("/", 1)[0])
        sync._run("/bin/cp", "--", sync._wslpath(frozen), remote)
        verified = sync._run("/usr/bin/sha256sum", "--", remote).stdout.split()
        if not verified or verified[0] != digest:
            raise ValueError(msg("text.remote.task.supervisor.checksum.mismatch.launch.refused"))
        resources[name] = {"local_snapshot": str(frozen), "remote_path": remote, "sha256": digest}
    script = resources["task_runner.py"]
    update_run(
        task,
        runtime_supervisor={
            "source": str(sources["task_runner.py"]),
            **script,
            "language": current_locale(),
            "resources": resources,
        },
    )
    return script["remote_path"]
