from __future__ import annotations

import json
from dataclasses import dataclass

from fracrtm.messages import message as msg
from fracrtm.messages import record_message

from .generation_records import utc_now
from .runs import update_run


@dataclass(frozen=True)
class RecoveryInspection:
    state: str
    message: str
    can_resync: bool
    can_cancel: bool
    details: dict


def _payload(task, sync_service):
    record = json.loads(task.record_path.read_text(encoding="utf-8"))
    command = record.get("cancellation_command", [])
    prefix = list(sync_service._prefix)
    if not isinstance(command, list) or command[: len(prefix)] != prefix:
        raise ValueError(
            msg("text.frozen.cancellation.command.does.not.match.the.selected.wsl.environment")
        )
    args = command[len(prefix) :]
    if (
        len(args) != 8
        or args[1] != "-B"
        or args[3] != "--control"
        or (args[5] != "--token")
        or (args[7] != "--cancel")
    ):
        raise ValueError(msg("text.unsupported.frozen.task.supervisor.command"))
    if args[0] != record.get("runtime", {}).get("dfn_python"):
        raise ValueError(msg("text.frozen.supervisor.python.does.not.match.the.recorded.runtime"))
    remote = record.get("remote_root", "").rstrip("/")
    if (
        task.run_id not in remote.split("/")
        or args[4] != remote + "/task_control.json"
        or args[6] != task.run_id
    ):
        raise ValueError(
            msg("text.frozen.task.token.or.control.path.does.not.match.the.recorded.run")
        )
    sync_service.task = task
    return (record, args)


def _history(task, kind, details, **fields):
    record = json.loads(task.record_path.read_text(encoding="utf-8"))
    history = record.get("recovery_history", [])
    history.append({"at": utc_now(), "operation": kind, "details": details})
    return update_run(task, recovery_history=history, **fields)


def inspect_remote_run(task, sync_service):
    record, args = _payload(task, sync_service)
    response = sync_service._run(*args[:-1], "--inspect")
    details = json.loads(response.stdout)
    if not isinstance(details, dict) or details.get("token") != task.run_id:
        raise ValueError(msg("text.remote.inspection.token.does.not.match.this.run"))
    state = details.get("state")
    if state not in (
        "completed",
        "failed",
        "cancelled",
        "running",
        "interrupted",
        "ambiguous",
        "unknown",
    ):
        raise ValueError(msg("text.unknown.remote.supervisor.inspection.state"))
    code = details.get("returncode")
    if state in ("completed", "failed", "cancelled") and details.get("remaining") != []:
        raise ValueError(msg("text.remote.terminal.record.still.has.remaining.descendants"))
    if state == "completed" and code != 0:
        raise ValueError(msg("text.remote.success.requires.an.explicitly.recorded.zero.exit.code"))
    fields = {}
    already_complete = record.get("status") == "completed" and record.get("synchronized") is True
    can_resync = state == "completed" and (not already_complete)
    if not already_complete:
        if state == "completed":
            fields.update(
                status="awaiting_sync",
                solver_status="succeeded",
                returncode=0,
                failure_stage="",
                error="",
                synchronized=False,
            )
        elif state in ("failed", "cancelled"):
            fields.update(
                status=state,
                solver_status=state,
                returncode=code,
                finished_at=utc_now(),
                synchronized=False,
                failure_stage="cancelled" if state == "cancelled" else "remote_execution",
                error=details.get("message", state),
            )
    _history(task, "inspect", details, **fields)
    can_cancel = state == "running" and details.get("can_cancel") is True
    return RecoveryInspection(
        state, record_message(details) or state, can_resync, can_cancel, details
    )


def cancel_remote_run(task, sync_service):
    before = inspect_remote_run(task, sync_service)
    if not before.can_cancel:
        raise ValueError(
            msg("text.cancellation.requires.the.verified.running.task.identity") + before.message
        )
    _, args = _payload(task, sync_service)
    response = sync_service._run(*args)
    acknowledgement = json.loads(response.stdout)
    if not isinstance(acknowledgement, dict) or acknowledgement.get("remaining") != []:
        raise ValueError(msg("text.remote.cancellation.did.not.confirm.an.empty.task.group"))
    if (
        acknowledgement.get("terminated_group") != before.details.get("pid")
        and acknowledgement.get("already_finished") is not True
    ):
        raise ValueError(
            msg("text.remote.cancellation.acknowledgement.does.not.match.the.verified.task")
        )
    _history(task, "cancel", acknowledgement)
    return inspect_remote_run(task, sync_service)
