from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from fracrtm.messages import message as msg

_guard = threading.Lock()
_local_locks = {}


class ProjectBusyError(RuntimeError):
    pass


class ProjectLock:
    def __init__(self, project, kind="write"):
        identity = os.path.normcase(str(Path(project).resolve())) + ":" + kind
        self.key = hashlib.sha256(identity.encode()).hexdigest()
        self.stream = None
        self.acquired = False
        self.kind = kind

    def acquire(self):
        with _guard:
            entry = _local_locks.setdefault(
                self.key, {"lock": threading.RLock(), "depth": 0, "stream": None}
            )
        if not entry["lock"].acquire(blocking=False):
            raise ProjectBusyError(msg("repair.project_busy"))
        try:
            if entry["depth"] == 0:
                folder = Path(tempfile.gettempdir()) / "fracrtm-project-locks"
                folder.mkdir(parents=True, exist_ok=True)
                stream = (folder / self.key).open("a+b")
                try:
                    if os.name == "nt":
                        import msvcrt

                        stream.seek(0, 2)
                        if stream.tell() == 0:
                            stream.write(b"0")
                            stream.flush()
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    stream.close()
                    raise ProjectBusyError(msg("repair.project_busy")) from None
                entry["stream"] = stream
            elif self.kind == "session":
                raise ProjectBusyError(msg("repair.project_busy"))
            entry["depth"] += 1
            self.acquired = True
            return self
        except BaseException:
            entry["lock"].release()
            raise

    def release(self):
        if not self.acquired:
            return
        entry = _local_locks[self.key]
        entry["depth"] -= 1
        if not entry["depth"]:
            stream = entry["stream"]
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()
            entry["stream"] = None
        self.acquired = False
        entry["lock"].release()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *args):
        self.release()


def atomic_bytes(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="." + path.name + ".", suffix=".tmp", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def atomic_text(path, text):
    atomic_bytes(path, text.encode("utf-8"))


def atomic_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def _safe(root, relative):
    if not str(relative) or str(relative) in {".", ".."}:
        raise ValueError(msg("repair.transaction_path"))
    path = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError(msg("repair.transaction_path"))
    path.resolve().relative_to(root.resolve())
    return path


def _kind(value):
    if not isinstance(value, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,31}", value) is None:
        raise ValueError(msg("repair.transaction_corrupt"))
    return value


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_copy(source, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix="." + target.name, delete=False
        ) as stream:
            temporary = Path(stream.name)
            with Path(source).open("rb") as original:
                shutil.copyfileobj(original, stream, 1024 * 1024)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def recover_project(project):
    root = Path(project).resolve()
    with ProjectLock(root):
        marker = _safe(root, ".fracrtm/pending.json")
        if not marker.is_file():
            return False
        value = json.loads(marker.read_text(encoding="utf-8"))
        revision = _safe(root, value["revision"])
        pointer = _safe(root, ".fracrtm/" + _kind(value["kind"]) + ".json")
        if not re.fullmatch(r"\.fracrtm/commits/[0-9a-f]{32}", value["revision"]):
            raise ValueError(msg("repair.transaction_corrupt"))
        if set(value["old"]) != set(value["new"]):
            raise ValueError(msg("repair.transaction_corrupt"))
        current = json.loads(pointer.read_text(encoding="utf-8")) if pointer.is_file() else {}
        committed = current.get("revision") == value["revision"]
        side = "new" if committed else "old"
        # Validate all recovery files before replacing live files.
        for relative, expected in value[side].items():
            _safe(root, relative)
            if expected is not None and file_hash(_safe(revision / side, relative)) != expected:
                raise ValueError(msg("repair.transaction_corrupt"))
        for relative, expected in value[side].items():
            target = _safe(root, relative)
            if expected is None:
                target.unlink(missing_ok=True)
            else:
                atomic_copy(_safe(revision / side, relative), target)
        marker.unlink()
        _discard_completed_transaction(root, revision, pointer)
        return True


def _discard_completed_transaction(root, revision, pointer):

    if (root / ".fracrtm/pending.json").exists():
        return
    revision.resolve().relative_to((root / ".fracrtm/commits").resolve())
    if revision.is_symlink():
        raise ValueError(msg("repair.transaction_path"))
    # Once pending is removed, neither backup is needed for restart recovery.
    try:
        pointer.unlink(missing_ok=True)
        shutil.rmtree(revision)
    except OSError:
        import logging

        logging.getLogger(__name__).exception("Committed transaction cleanup failed: %s", revision)


class FileTransaction:
    """Commit related project files with rollback and restart recovery."""

    def __init__(self, project, kind="model"):
        self.root = Path(project).resolve()
        self.kind = _kind(kind)
        self.writes = {}

    def add_bytes(self, relative, content):
        _safe(self.root, relative)
        self.writes[str(relative)] = bytes(content)

    def add_file(self, relative, source):
        _safe(self.root, relative)
        self.writes[str(relative)] = Path(source)

    def add_text(self, relative, text):
        self.add_bytes(relative, text.encode("utf-8"))

    def add_json(self, relative, value):
        self.add_text(
            relative, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        )

    def commit(self):
        with ProjectLock(self.root):
            recover_project(self.root)
            revision = _safe(self.root, ".fracrtm/commits/" + uuid.uuid4().hex)
            revision.mkdir(parents=True)
            marker = _safe(self.root, ".fracrtm/pending.json")
            pointer = _safe(self.root, ".fracrtm/" + self.kind + ".json")
            before_pointer = pointer.read_bytes() if pointer.exists() else None
            record = {
                "revision": revision.relative_to(self.root).as_posix(),
                "kind": self.kind,
                "new": {},
                "old": {},
            }
            try:
                for relative, content in self.writes.items():
                    target = _safe(self.root, relative)
                    staged = _safe(revision / "new", relative)
                    if isinstance(content, Path):
                        atomic_copy(content, staged)
                    else:
                        atomic_bytes(staged, content)
                    record["new"][relative] = file_hash(staged)
                    record["old"][relative] = None
                    if target.exists():
                        old = _safe(revision / "old", relative)
                        atomic_copy(target, old)
                        record["old"][relative] = file_hash(old)
                atomic_json(revision / "files.json", record)
                atomic_json(marker, record)
                for relative in self.writes:
                    atomic_copy(_safe(revision / "new", relative), _safe(self.root, relative))
                atomic_json(pointer, {"revision": record["revision"], "files": record["new"]})
                marker.unlink()
            except BaseException:
                if before_pointer is None:
                    pointer.unlink(missing_ok=True)
                else:
                    atomic_bytes(pointer, before_pointer)
                if marker.exists():
                    recover_project(self.root)
                raise
            _discard_completed_transaction(self.root, revision, pointer)
            return revision


_resource_overlays = threading.local()


def resource_path(project, relative, default=None):
    key = (str(Path(project).resolve()), str(relative).replace("\\", "/"))
    return getattr(_resource_overlays, "paths", {}).get(
        key, default if default is not None else Path(project) / relative
    )


class ResourceImports:
    def __init__(self, project):
        self.project = Path(project).resolve() if project is not None else None
        self.pending = {}
        self._temporary = None

    def stage(self, source, category):
        source = Path(source).resolve()
        digest = file_hash(source)
        relative = f"{category}/{source.name}"
        target = _safe(self.project, relative)
        if target.exists() and file_hash(target) == digest:
            return relative
        if target.exists() or relative in self.pending:
            relative = f"{category}/{source.stem}_{digest[:12]}{source.suffix}"
        if self._temporary is None:
            self._temporary = tempfile.TemporaryDirectory(prefix="fracrtm-edit-")
        staged = Path(self._temporary.name) / digest
        atomic_copy(source, staged)
        if file_hash(staged) != digest:
            raise ValueError(msg("repair.transaction_corrupt"))
        self.pending[relative] = staged
        return relative

    def resolve(self, relative):
        return self.pending.get(
            str(relative), self.project / relative if self.project is not None else Path(relative)
        )

    @contextmanager
    def overlay(self):
        previous = getattr(_resource_overlays, "paths", {})
        added = {(str(self.project), name): path for name, path in self.pending.items()}
        _resource_overlays.paths = {**previous, **added}
        try:
            yield
        finally:
            _resource_overlays.paths = previous

    def commit_config(self, config):
        if self.project is None:
            return
        with ProjectLock(self.project):
            return self._commit_config(config)

    def _commit_config(self, config):
        if self.project is None:
            return
        transaction = FileTransaction(self.project, "edit")
        for relative, path in self.pending.items():
            target = _safe(self.project, relative)
            if target.exists() and file_hash(target) != file_hash(path):
                raise FileExistsError(msg("repair.resource_changed", path=relative))
            transaction.add_file(relative, path)
        transaction.add_json("pflotran/model_config.json", config.to_dict())
        transaction.commit()
        self.pending.clear()
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None


class DirectoryReplacement:
    """Retain the previous directory until generation succeeds."""

    def __init__(self, target, *, overwrite=False, seed_existing=False):
        self.target = Path(target).resolve()
        self.overwrite = overwrite
        self.seed_existing = seed_existing
        self.backup = self.target.with_name("." + self.target.name + ".previous")
        self.marker = self.target.with_name("." + self.target.name + ".replacement.json")
        self.lock = ProjectLock(self.target)
        self.committed = False
        self.started = False

    def __enter__(self):
        self.lock.acquire()
        try:
            recover_replacement(self.target)
            if self.backup.exists():
                raise ValueError(msg("repair.transaction_corrupt"))
            if (
                self.target.exists()
                and (not self.target.is_dir() or any(self.target.iterdir()))
                and not self.overwrite
            ):
                raise FileExistsError(msg("repair.overwrite_refused", path=self.target))
            self.target.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(
                self.marker,
                {
                    "target": str(self.target),
                    "backup": str(self.backup),
                    "had_previous": self.target.exists(),
                },
            )
            self.started = True
            if self.target.exists():
                os.replace(self.target, self.backup)
                if self.seed_existing:
                    shutil.copytree(self.backup, self.target)
            return self
        except BaseException:
            try:
                if self.started:
                    recover_replacement(self.target)
            finally:
                self.lock.release()
            raise

    def commit(self):
        atomic_json(
            self.marker, {"target": str(self.target), "backup": str(self.backup), "committed": True}
        )
        self.committed = True
        if self.backup.exists():
            shutil.rmtree(self.backup)
        self.marker.unlink()

    def __exit__(self, *args):
        try:
            if self.started and not self.committed:
                if self.target.exists():
                    shutil.rmtree(self.target)
                if self.backup.exists():
                    os.replace(self.backup, self.target)
                self.marker.unlink(missing_ok=True)
        finally:
            self.lock.release()


def recover_replacement(target):
    target = Path(target).resolve()
    backup = target.with_name("." + target.name + ".previous")
    marker = target.with_name("." + target.name + ".replacement.json")
    if not marker.is_file():
        return False
    state = json.loads(marker.read_text(encoding="utf-8"))
    if (
        state.get("target") != str(target)
        or state.get("backup") != str(backup)
        or set(state) - {"target", "backup", "committed", "had_previous"}
    ):
        raise ValueError(msg("repair.transaction_corrupt"))
    if backup.is_symlink() or target.is_symlink():
        raise ValueError(msg("repair.transaction_path"))
    if state.get("committed"):
        if backup.exists():
            shutil.rmtree(backup)
    elif backup.exists():
        if target.exists():
            shutil.rmtree(target)
        os.replace(backup, target)
    elif target.exists() and not state.get("had_previous", True):
        # Remove only the directory created by the interrupted task.
        shutil.rmtree(target)
    marker.unlink()
    return True
