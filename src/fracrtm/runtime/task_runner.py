from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

_MESSAGE_MODULE = None


def _messages():
    global _MESSAGE_MODULE
    if _MESSAGE_MODULE is not None:
        return _MESSAGE_MODULE
    import importlib.util

    candidate = Path(__file__).with_name("messages.py")
    if not candidate.is_file() and "fracrtm.messages" in sys.modules:
        _MESSAGE_MODULE = sys.modules["fracrtm.messages"]
        return _MESSAGE_MODULE
    if not candidate.is_file():
        candidate = Path(__file__).parent.parent / "messages.py"
    specification = importlib.util.spec_from_file_location("fracrtm_supervisor_messages", candidate)
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    language = candidate.with_name("language.json")
    if language.is_file():
        module.set_locale(json.loads(language.read_text(encoding="utf-8"))["locale"])
    _MESSAGE_MODULE = module
    return module


def _msg(key, **values):
    return _messages().message(key, **values)


def _serialize(value):
    return _messages().render_record(value)


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(_serialize(value), indent=2), encoding="utf-8")
    tmp.replace(path)


def _proc(pid):
    text = Path(f"/proc/{pid}/stat").read_text()
    fields = text[text.rfind(")") + 2 :].split()
    return {
        "state": fields[0],
        "group": int(fields[2]),
        "session": int(fields[3]),
        "start_ticks": int(fields[19]),
    }


def _members(group):
    found = []
    for path in Path("/proc").iterdir():
        if path.name.isdigit():
            try:
                p = _proc(int(path.name))
                if p["group"] == group and p["state"] != "Z":
                    found.append(int(path.name))
            except (OSError, ValueError):
                pass
    return found


def inspect_control(path, token):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "state": "unknown",
            "token": token,
            "can_cancel": False,
            "remaining": [],
            "message": _msg(
                "supervisor.supervisor.control.file.is.missing.launch.or.completion.cannot.be.established"
            ),
        }
    if data.get("token") != token:
        raise ValueError(_msg("supervisor.task.token.mismatch.inspection.refused"))
    pid = int(data["pid"])
    if pid <= 0:
        raise ValueError(_msg("supervisor.invalid.supervisor.child.identity"))
    remaining = _members(pid)
    result = {
        "token": token,
        "pid": pid,
        "remaining": remaining,
        "can_cancel": False,
        "control_status": data.get("status"),
        "returncode": data.get("returncode"),
        "child_returncode": data.get("child_returncode"),
    }
    if data.get("status") in ("finished", "cancelled"):
        if remaining:
            return {
                **result,
                "state": "ambiguous",
                "message": _msg(
                    "supervisor.terminal.marker.has.live.group.members.do.not.infer.success.or"
                ),
            }
        code = data.get("returncode")
        if not isinstance(code, int):
            return {
                **result,
                "state": "unknown",
                "message": _msg("supervisor.terminal.control.marker.has.no.recorded.exit.code"),
            }
        state = (
            "cancelled" if data["status"] == "cancelled" else "completed" if code == 0 else "failed"
        )
        return {
            **result,
            "state": state,
            "message": _msg(
                "supervisor.remote.task.process.group.has.no.live.members", state=state
            ),
        }
    try:
        actual = _proc(pid)
    except OSError:
        state = "ambiguous" if remaining else "interrupted"
        return {
            **result,
            "state": state,
            "message": _msg(
                "supervisor.no.matching.leader.and.no.confirmed.terminal.marker.completion.is.not"
            ),
        }
    if (
        actual["start_ticks"] != data.get("start_ticks")
        or actual["group"] != pid
        or actual["session"] != pid
    ):
        return {
            **result,
            "state": "ambiguous",
            "message": _msg(
                "supervisor.pid.start.time.session.identity.differs.no.process.may.be.cancelled"
            ),
        }
    if actual["state"] == "Z":
        return {
            **result,
            "state": "interrupted",
            "message": _msg(
                "supervisor.child.is.no.longer.running.but.terminal.supervisor.evidence.is.not"
            ),
        }
    return {
        **result,
        "state": "running",
        "can_cancel": True,
        "message": _msg("supervisor.the.recorded.task.identity.is.still.running.remotely"),
    }


def cancel(path, token, grace=3.0):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("token") != token:
        raise ValueError(_msg("supervisor.task.token.mismatch.cancellation.refused"))
    pid = int(data["pid"])
    if data.get("status") in {"finished", "cancelled"}:
        remaining = _members(pid)
        if remaining:
            raise ValueError(
                _msg(
                    "supervisor.completed.task.marker.still.has.remaining.descendants.identity.must.be.reverified",
                    processes=remaining,
                )
            )
        return {"already_finished": True, "remaining": []}
    try:
        actual = _proc(pid)
    except OSError:
        if _members(pid):
            raise ValueError(
                _msg("supervisor.group.leader.missing.cannot.verify.identity.cancellation.refused")
            )
        return {"already_finished": True, "remaining": []}
    if (
        actual["start_ticks"] != data["start_ticks"]
        or actual["group"] != pid
        or actual["session"] != pid
    ):
        raise ValueError(_msg("supervisor.process.identity.changed.cancellation.refused"))
    data["cancellation_requested"] = True
    _write(path, data)
    # Verify process-group identity before sending a signal.
    os.killpg(pid, signal.SIGTERM)
    until = time.monotonic() + grace
    while _members(pid) and time.monotonic() < until:
        time.sleep(0.05)
    if _members(pid):
        os.killpg(pid, signal.SIGKILL)
    until = time.monotonic() + 3
    while _members(pid) and time.monotonic() < until:
        time.sleep(0.05)
    remaining = _members(pid)
    if remaining:
        raise RuntimeError(
            _msg("supervisor.task.group.termination.not.confirmed", processes=remaining)
        )
    return {"terminated_group": pid, "remaining": []}


def _stop_owned_child(child, grace=3.0):
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        child.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=grace)
    if _members(child.pid):
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    until = time.monotonic() + grace
    while _members(child.pid) and time.monotonic() < until:
        time.sleep(0.05)
    if _members(child.pid):
        raise RuntimeError(
            _msg("supervisor.owned.task.descendants.did.not.terminate.after.supervisor.failure")
        )


def _cleanup_remaining_session(data, grace=3.0):
    pid = int(data["pid"])
    remaining = _members(pid)
    if not remaining:
        return False
    for member in remaining:
        try:
            identity = _proc(member)
        except OSError:
            continue
        if (
            identity["group"] != pid
            or identity["session"] != pid
            or identity["start_ticks"] < data["start_ticks"]
        ):
            raise RuntimeError(
                _msg(
                    "supervisor.remaining.process.identity.is.ambiguous.refusing.group.termination"
                )
            )
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if not _members(pid):
            break
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            break
        until = time.monotonic() + grace
        while _members(pid) and time.monotonic() < until:
            time.sleep(0.05)
    if _members(pid):
        raise RuntimeError(_msg("supervisor.residual.task.group.termination.was.not.confirmed"))
    return True


def run(path, token, command):
    path = Path(path)
    if path.exists():
        raise ValueError(
            _msg("supervisor.supervisor.identity.file.already.exists.use.a.new.run.directory")
        )
    child = subprocess.Popen(command, start_new_session=True)
    try:
        data = {
            "token": token,
            "pid": child.pid,
            "command": command,
            "start_ticks": _proc(child.pid)["start_ticks"],
            "status": "running",
        }
        _write(path, data)
        code = child.wait()
    except BaseException:
        _stop_owned_child(child)
        raise
    residual_cleanup = _cleanup_remaining_session(data)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        pass
    child_code = code
    if residual_cleanup and (not data.get("cancellation_requested")):
        import sys

        print(
            _msg(
                "supervisor.task.supervisor.child.exited.while.descendants.remained.owned.session.cleaned.up"
            ),
            file=sys.stderr,
            flush=True,
        )
        code = code or 2
    data.update(
        status="cancelled" if data.get("cancellation_requested") else "finished",
        returncode=code,
        child_returncode=child_code,
        residual_descendants_cleaned=residual_cleanup,
    )
    _write(path, data)
    return code if code >= 0 else 128 - code


def main(argv=None):
    argv = _messages().configure_cli_language(argv)
    parser = _messages().argument_parser(_msg("supervisor.description"))
    parser.add_argument("--control", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--cancel", action="store_true")
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        if args.cancel and args.inspect:
            raise ValueError(_msg("supervisor.choose.inspection.or.cancellation.not.both"))
        if args.inspect:
            print(json.dumps(_serialize(inspect_control(args.control, args.token))), flush=True)
            return 0
        if args.cancel:
            print(json.dumps(_serialize(cancel(args.control, args.token))), flush=True)
            return 0
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not command:
            raise ValueError(_msg("supervisor.no.task.command.supplied"))
        return run(args.control, args.token, command)
    except Exception as exc:
        import sys

        print(_msg("supervisor.task.supervisor", detail=exc), file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
