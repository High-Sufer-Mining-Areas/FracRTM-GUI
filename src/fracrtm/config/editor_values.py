from __future__ import annotations

from copy import deepcopy
from numbers import Real


def preserve_unedited_numbers(original, displayed, current):
    """Preserve saved values that exceed an unedited widget's precision."""
    if isinstance(original, Real) and not isinstance(original, bool):
        return original if current == displayed else current
    if isinstance(original, dict) and isinstance(displayed, dict) and isinstance(current, dict):
        if any(current.get(key) != displayed.get(key) for key in ("id",)):
            return current
        return {
            key: preserve_unedited_numbers(original[key], displayed[key], value)
            if key in original and key in displayed
            else value
            for key, value in current.items()
        }
    if (
        isinstance(original, list)
        and isinstance(displayed, list)
        and isinstance(current, list)
        and len(original) == len(displayed) == len(current)
    ):
        return [
            preserve_unedited_numbers(old, initial, value)
            for old, initial, value in zip(original, displayed, current)
        ]
    return deepcopy(current)
