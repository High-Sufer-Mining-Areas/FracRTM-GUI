import math

from fracrtm.config.time_units import CALENDAR_UNITS
from fracrtm.messages import format_message, join_text, state_text
from fracrtm.messages import message as msg
from fracrtm.visualization.result_times import TIME_FACTORS

from .localization import tr


def _seconds(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


def format_duration(seconds):
    seconds = _seconds(seconds)
    if seconds is None:
        return "—"
    rounded = int(seconds + 0.5)
    if rounded < 60:
        return format_message("overview.duration.seconds", seconds=rounded)
    if rounded < 3600:
        minutes, remainder = divmod(rounded, 60)
        return format_message("overview.duration.minutes", minutes=minutes, seconds=remainder)
    hours, minutes = divmod(rounded // 60, 60)
    return format_message("overview.duration.hours", hours=hours, minutes=minutes)


def format_simulation_time(seconds, unit="s"):
    seconds = _seconds(seconds)
    if seconds is None:
        return "—"
    unit = str(unit).lower()
    if unit in CALENDAR_UNITS:
        unit = "s"
    factor = TIME_FACTORS.get(unit, 1.0)
    names = {
        1: "second",
        60: "minute",
        3600: "hour",
        86400: "day",
        604800: "week",
        TIME_FACTORS["month"]: "month",
        TIME_FACTORS["y"]: "year",
    }
    value = seconds / factor
    name = names.get(factor, "second") + ("" if value == 1 else "s")
    return format_message("overview.time." + name, value=f"{value:g}")


def summary_status(status, matches_current):
    if status is None:
        return msg("text.no.simulation.record")
    label = state_text(
        {
            "completed": msg("text.completed"),
            "running": msg("text.running"),
            "failed": msg("text.failed"),
            "cancelled": msg("text.cancelled"),
            "prepared": msg("text.preparing"),
            "synchronizing": msg("text.synchronizing"),
        }.get(status, status)
    )
    relation = tr("overview.current" if matches_current else "overview.changed")
    return join_text("", [label, " · ", relation])
