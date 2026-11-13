from __future__ import annotations

import os
from pathlib import Path

from fracrtm.messages import message as msg


class BoundedLog(list):
    def __init__(self, values=(), *, maximum=2000):
        self.maximum = maximum
        super().__init__(values[-maximum:] if hasattr(values, "__getitem__") else values)

    def append(self, value):
        if isinstance(value, str) and len(value) > 16000:
            value = value[:16000] + "…"
        super().append(value)
        if len(self) > self.maximum:
            del self[: len(self) - self.maximum]


def log_tail(path, maximum=2 * 1024 * 1024):
    path = Path(path)
    with path.open("rb") as stream:
        size = path.stat().st_size
        stream.seek(max(0, size - maximum))
        text = stream.read(maximum).decode("utf-8", errors="replace")
    if size > maximum:
        return str(msg("repair.log_tail", path=path)) + "\n" + text
    return text


def conversion_budget(paths, geometry=None):
    """Reject eager loads that exceed the estimated allocation budget."""
    budget = int(os.environ.get("FRACRTM_CONVERSION_MEMORY_MB", "1024")) * 1024 * 1024
    if budget < 16 * 1024 * 1024:
        raise ValueError(msg("repair.memory_budget"))
    estimate = 0
    for source in paths:
        source = Path(source)
        if source.suffix.lower() in {".h5", ".hdf5"}:
            import h5py

            logical_bytes = 0
            count = 0

            def measure(name, item):
                nonlocal logical_bytes, count
                count += 1
                if count > 100000:
                    raise MemoryError(msg("repair.memory_budget"))
                if isinstance(item, h5py.Dataset):
                    logical_bytes += item.size * item.dtype.itemsize
                    if logical_bytes * 4 > budget:
                        raise MemoryError(
                            msg(
                                "repair.conversion_budget",
                                estimate=logical_bytes * 4,
                                budget=budget,
                            )
                        )

            with h5py.File(source, "r") as stream:
                stream.visititems(measure)
            estimate += logical_bytes * 4
        else:
            estimate += source.stat().st_size * 8
    if geometry is not None:
        estimate += Path(geometry).stat().st_size * 8
    if estimate > budget:
        raise MemoryError(msg("repair.conversion_budget", estimate=estimate, budget=budget))
    return estimate
