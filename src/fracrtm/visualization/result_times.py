from __future__ import annotations

import math
import re

from fracrtm.config.time_units import CALENDAR_UNITS, TIME_FACTORS
from fracrtm.messages import join_text
from fracrtm.messages import message as msg

NUMBER = "[-+]?(?:\\d+(?:\\.\\d*)?|\\.\\d+)(?:[EeDd][-+]?\\d+)?"
TIME_PATTERN = re.compile(
    "\\bTime\\s*[:=]\\s*(" + NUMBER + ")\\s*(?:\\[\\s*)?([A-Za-z]+)(?:\\s*\\])?", re.I
)
INDEXED_TIME_GROUP = re.compile(
    "^\\s*(?:\\d+\\s+)?Time\\s+(" + NUMBER + ")\\s+([A-Za-z]+)\\s*$", re.I
)


class CalendarTimeUnverified(ValueError):
    pass


def physical_time(value, unit, *, calendar_year_seconds=None):
    import numpy as np

    if isinstance(unit, bytes):
        unit = unit.decode("ascii")
    key = str(unit).strip().strip("[]").lower()
    if key not in TIME_FACTORS:
        raise ValueError(
            msg(
                "diagnostic.result_times.unknown.physical.time.unit.unit",
                unit=f"{unit!r}",
            )
        )
    factor = TIME_FACTORS[key]
    if key in CALENDAR_UNITS:
        if calendar_year_seconds is None:
            raise CalendarTimeUnverified(msg("repair.calendar_unverified"))
        factor = calendar_year_seconds / (12 if key in {"month", "months", "mo"} else 1)
    values = np.asarray(value).reshape(-1)
    if len(values) != 1:
        raise ValueError(msg("text.physical.output.time.metadata.must.be.scalar"))
    time = float(str(values[0]).replace("D", "E").replace("d", "e")) * factor
    if not math.isfinite(time) or time < 0:
        raise ValueError(msg("text.physical.output.time.must.be.finite.and.nonnegative"))
    return time


def time_from_text(text):
    match = TIME_PATTERN.search(text)
    try:
        return physical_time(*match.groups()) if match else None
    except CalendarTimeUnverified:
        return None


def time_from_hdf5_group(name, group):
    try:
        return _time_from_hdf5_group(name, group)
    except CalendarTimeUnverified:
        return None


def _time_from_hdf5_group(name, group):
    # Read physical time from metadata, not the group index.
    for key, value in group.attrs.items():
        match = re.fullmatch("Time\\s*[\\[(]\\s*([A-Za-z]+)\\s*[\\])]", str(key), re.I)
        if match:
            return physical_time(value, match.group(1))
    for value_key, unit_key in [
        ("time", "time_unit"),
        ("Time", "Time Units"),
        ("time", "time_units"),
    ]:
        if value_key in group.attrs and unit_key in group.attrs:
            return physical_time(group.attrs[value_key], group.attrs[unit_key])
    value = time_from_text(name)
    if value is not None:
        return value
    match = INDEXED_TIME_GROUP.fullmatch(name)
    if match:
        return physical_time(*match.groups())
    return None


def order_snapshots(snapshots):
    return sorted(
        snapshots,
        key=lambda s: (
            s.time_seconds is None,
            s.time_seconds if s.time_seconds is not None else 0.0,
        ),
    )


def time_labels(times):
    return tuple(
        (
            join_text("", [f"{float(value):g}", " s"])
            if value is not None
            else msg("results.unknown_time", index=index + 1)
            for index, value in enumerate(times)
        )
    )
