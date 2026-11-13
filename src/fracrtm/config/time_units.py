TIME_CONVENTION = "pflotran_4_365_day_year"
YEAR_SECONDS = 31536000.0
TIME_FACTORS = {
    "s": 1.0,
    "sec": 1.0,
    "secs": 1.0,
    "second": 1.0,
    "seconds": 1.0,
    "min": 60.0,
    "minute": 60.0,
    "minutes": 60.0,
    "h": 3600.0,
    "hr": 3600.0,
    "hour": 3600.0,
    "hours": 3600.0,
    "d": 86400.0,
    "day": 86400.0,
    "days": 86400.0,
    "w": 604800.0,
    "week": 604800.0,
    "weeks": 604800.0,
    "month": YEAR_SECONDS / 12,
    "months": YEAR_SECONDS / 12,
    "mo": YEAR_SECONDS / 12,
    "y": YEAR_SECONDS,
    "yr": YEAR_SECONDS,
    "year": YEAR_SECONDS,
    "years": YEAR_SECONDS,
}
TIME_UNITS_SECONDS = {unit: TIME_FACTORS[unit] for unit in ("s", "min", "h", "d", "month", "y")}
CALENDAR_UNITS = frozenset(("y", "yr", "year", "years", "month", "months", "mo"))


# Apply calendar conversions only to these executable hashes.
VERIFIED_CALENDARS = {
    "b7874ba9c80090a8fe92f6b0c2f62fb857723c2c4235a3e1fd66421f81de401a": YEAR_SECONDS,
}


def run_calendar_year_seconds(task):
    import json

    try:
        record = json.loads(task.record_path.read_text(encoding="utf-8"))
        digest = record["execution_environment"]["tools"]["PFLOTRAN"]["sha256"]
        return VERIFIED_CALENDARS.get(digest)
    except (AttributeError, OSError, ValueError, KeyError, TypeError):
        return None
