from dataclasses import dataclass, fields

from PyQt5 import QtCore

from fracrtm.messages import message as msg


def _clean_runtime_value(value: object, name: str) -> str:
    cleaned = str(value).strip()
    if not cleaned:
        raise ValueError(msg("diagnostic.settings.name.cannot.be.empty", name=name))
    if "\r" in cleaned or "\n" in cleaned:
        raise ValueError(msg("runtime.newline", field=name))
    if cleaned[0] in "'\"" or cleaned[-1] in "'\"":
        raise ValueError(
            msg(
                "diagnostic.settings.name.must.not.be.wrapped.in.quotes.cleaned",
                name=name,
                cleaned=f"{cleaned!r}",
            )
        )
    return cleaned


def settings_store():
    """Open user settings using the active Qt storage format."""
    return QtCore.QSettings(
        QtCore.QSettings.defaultFormat(), QtCore.QSettings.UserScope, "FracRTM-GUI", "settings"
    )


@dataclass
class RuntimeSettings:
    distribution: str = "Ubuntu-22.04"
    wsl_user: str = "root"
    dfn_python: str = "/root/venvs/dfnworks-2.8/bin/python"
    dfnworks_home: str = "/root/.fracrtm-dfnworks-2.8"
    pflotran_executable: str = "/root/pflotran/src/pflotran/pflotran"
    mpi_launcher: str = "mpirun"
    wsl_workspace_root: str = "/root/FracRTMWorkspace"

    def __post_init__(self) -> None:
        for item in fields(self):
            setattr(self, item.name, _clean_runtime_value(getattr(self, item.name), item.name))

    @classmethod
    def load(cls):
        store = settings_store()
        old_python = store.value("wsl_python", "/root/venvs/dfnworks-2.8/bin/python", type=str)
        return cls(
            distribution=store.value("distribution", "Ubuntu-22.04", type=str),
            wsl_user=store.value("wsl_user", "root", type=str),
            dfn_python=store.value("dfn_python", old_python, type=str),
            dfnworks_home=store.value("dfnworks_home", "/root/.fracrtm-dfnworks-2.8", type=str),
            pflotran_executable=store.value(
                "pflotran_executable", "/root/pflotran/src/pflotran/pflotran", type=str
            ),
            mpi_launcher=store.value("mpi_launcher", "mpirun", type=str),
            wsl_workspace_root=store.value(
                "wsl_workspace_root", "/root/FracRTMWorkspace", type=str
            ),
        )

    def save(self) -> None:
        store = settings_store()
        for item in fields(self):
            store.setValue(item.name, getattr(self, item.name))
        store.remove("wsl_python")
