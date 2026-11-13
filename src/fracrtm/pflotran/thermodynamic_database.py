from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path

from fracrtm.messages import message as msg


@dataclass(frozen=True)
class ParsedThermodynamicDatabase:
    primary_species: list[str] = field(default_factory=list)
    secondary_species: list[str] = field(default_factory=list)
    gas_species: list[str] = field(default_factory=list)
    minerals: list[str] = field(default_factory=list)
    secondary_dependencies: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def aqueous_species(self) -> set[str]:
        return set(self.primary_species) | set(self.secondary_species)

    def secondary_is_compatible(self, species: str, primary_species: list[str]) -> bool:
        dependencies = set(self.secondary_dependencies.get(species, ()))
        return not dependencies or dependencies.issubset(set(primary_species))


def parse_thermodynamic_database(path: str | Path) -> ParsedThermodynamicDatabase:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(msg("database.missing", path=source))
    try:
        lines = source.read_text(encoding="utf-8", errors="strict").splitlines()
    except UnicodeDecodeError:
        lines = source.read_text(encoding="latin-1", errors="strict").splitlines()
    sections: list[list[list[str]]] = [[] for _ in range(5)]
    section = 0
    saw_temperature = False
    for line_number, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith(("#", "!")):
            continue
        try:
            fields = shlex.split(stripped, comments=True, posix=True)
        except ValueError as exc:
            raise ValueError(msg("database.line", index=line_number, reason=exc)) from exc
        if not fields:
            continue
        if not saw_temperature:
            if fields[0].casefold() != "temperature points":
                raise ValueError(
                    msg("validation.the.first.valid.database.record.must.be.temperature.points")
                )
            saw_temperature = True
            continue
        if fields[0].casefold() == "null":
            section += 1
            if section >= 5:
                break
            continue
        if section < 5:
            sections[section].append(fields)
    if not saw_temperature or section < 4:
        raise ValueError(
            msg("text.the.database.is.missing.complete.primary.secondary.gas.or.mineral.sections")
        )
    primary = _unique((record[0] for record in sections[0]))
    secondary = _unique((record[0] for record in sections[1]))
    gases = _unique((record[0] for record in sections[2]))
    minerals = _unique((record[0] for record in sections[3]))
    dependencies = {}
    for record in sections[1]:
        name = record[0]
        try:
            count = int(record[1])
            dependencies[name] = tuple((record[3 + 2 * index] for index in range(count)))
        except (ValueError, IndexError) as exc:
            raise ValueError(msg("database.secondary", name=name)) from exc
    return ParsedThermodynamicDatabase(primary, secondary, gases, minerals, dependencies)


def _unique(values):
    result = []
    seen = set()
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result
