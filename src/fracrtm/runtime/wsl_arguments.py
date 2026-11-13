from __future__ import annotations

from typing import Iterable, Optional

from fracrtm.messages import join_text

from .settings import RuntimeSettings


def wsl_prefix(settings: RuntimeSettings) -> list[str]:
    return ["-d", settings.distribution, "-u", settings.wsl_user, "--"]


def dfn_python_args(
    settings: RuntimeSettings, python_arguments: Iterable[str], *, pythonpath: Optional[str] = None
) -> list[str]:
    args = wsl_prefix(settings)
    args.extend(["env", join_text("", ["HOME=", settings.dfnworks_home])])
    if pythonpath is not None:
        args.append(join_text("", ["PYTHONPATH=", pythonpath]))
    args.append(settings.dfn_python)
    args.extend((str(value) for value in python_arguments))
    return args
