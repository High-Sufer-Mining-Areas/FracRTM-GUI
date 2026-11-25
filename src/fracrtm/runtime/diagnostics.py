from __future__ import annotations

import traceback
from dataclasses import dataclass

from fracrtm.messages import diagnostic_message, render_record


@dataclass(frozen=True)
class FailureDiagnostic:
    category: str
    exception_type: str
    message: object
    traceback: str

    @classmethod
    def capture(cls, exception, category="application"):
        if isinstance(exception, OSError):
            category = "storage_or_environment"
        elif isinstance(exception, (ValueError, TypeError)):
            category = "validation"
        return cls(
            category,
            type(exception).__name__,
            diagnostic_message(exception),
            "".join(traceback.format_exception(exception)),
        )

    def to_record(self):
        return render_record(
            {
                "category": self.category,
                "exception_type": self.exception_type,
                "message": self.message,
                "traceback": self.traceback,
            }
        )


def persist_diagnostic(diagnostic, directory):
    import uuid
    from datetime import datetime, timezone
    from pathlib import Path

    from fracrtm.project.storage import atomic_json

    path = Path(directory) / (uuid.uuid4().hex + ".json")
    record = diagnostic.to_record()
    record["created_at"] = datetime.now(timezone.utc).isoformat()
    atomic_json(path, record)
    return path
