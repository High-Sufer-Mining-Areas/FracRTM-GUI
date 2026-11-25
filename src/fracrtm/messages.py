from __future__ import annotations

import json
import os
from numbers import Integral, Real
from pathlib import Path
from types import MappingProxyType
from typing import Any

SUPPORTED_LOCALES = ("en_US", "zh_CN")
DEFAULT_LOCALE = "en_US"
_locale = os.environ.get("FRACRTM_LANGUAGE", DEFAULT_LOCALE)
if _locale not in SUPPORTED_LOCALES:
    _locale = DEFAULT_LOCALE
_CATALOGS = {
    locale: json.loads(
        (Path(__file__).parent / "locales" / f"{locale}.json").read_text(encoding="utf-8")
    )
    for locale in SUPPORTED_LOCALES
}


def current_locale() -> str:
    return _locale


def set_locale(locale: str) -> str:
    global _locale
    if locale not in SUPPORTED_LOCALES:
        raise ValueError(f"Unsupported display language: {locale}")
    _locale = locale
    return locale


def _value(value: Any, locale: str):
    if isinstance(value, Message):
        return value.render(locale)
    if isinstance(value, BaseException):
        return _value(diagnostic_message(value), locale)
    if isinstance(value, (Path,)):
        return str(value)
    return value


def _parameter_value(value):
    if isinstance(value, Message):
        return value
    if isinstance(value, BaseException):
        return diagnostic_message(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        return float(value)
    if isinstance(value, tuple):
        return tuple(_parameter_value(item) for item in value)
    if isinstance(value, list):
        return [_parameter_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _parameter_value(item) for key, item in value.items()}
    return str(value)


class Message:
    __slots__ = ("_key", "_values", "_parts")

    @property
    def key(self):
        return self._key

    @property
    def values(self):
        return self._values

    @property
    def parts(self):
        return self._parts

    def __init__(self, key: str, values: dict[str, Any] | None = None, *, parts=None):
        if parts is None and key not in _CATALOGS[DEFAULT_LOCALE]:
            raise KeyError(key)
        self._key = key
        self._values = MappingProxyType(
            {
                name: diagnostic_message(value)
                if name in {"reason", "detail", "error", "result_message"}
                and value is not None
                and not isinstance(value, Message)
                else _parameter_value(value)
                for name, value in (values or {}).items()
            }
        )
        self._parts = tuple(parts) if parts is not None else None

    def render(self, locale=None) -> str:
        selected = locale or current_locale()
        if self.parts is not None:
            return "".join(render_text(part, selected) for part in self.parts)
        return _CATALOGS[selected][self.key].format(
            **{
                name: _value(
                    state_text(value) if name in {"state", "stage", "error_stage"} else value,
                    selected,
                )
                for name, value in self.values.items()
            }
        )

    def __str__(self):
        return self.render()

    def __format__(self, spec):
        return format(self.render(), spec)

    def __repr__(self):
        return (
            f"Message({self.key!r}, {self.values!r})"
            if self.parts is None
            else f"Message(parts={self.parts!r})"
        )

    def __eq__(self, other):
        return (
            self.to_record() == other.to_record() if isinstance(other, Message) else NotImplemented
        )

    def __hash__(self):
        if self.values or self.parts is not None:
            raise TypeError("Parameterized messages cannot be mapping keys")
        return hash(self.key)

    def __add__(self, other):
        if not isinstance(other, (str, Message)):
            return NotImplemented
        return Message("", parts=(self, other))

    def __radd__(self, other):
        if not isinstance(other, (str, Message)):
            return NotImplemented
        return Message("", parts=(other, self))

    def __reduce__(self):
        return (restore_text, (self.to_record(),))

    def to_record(self):
        if self.parts is not None:
            return {"parts": [_encode(part) for part in self.parts]}
        return {
            "key": self.key,
            "values": {name: _encode(value) for name, value in self.values.items()},
        }


def _encode(value):
    if isinstance(value, Message):
        return {"$message": value.to_record()}
    if isinstance(value, BaseException):
        return {"$message": diagnostic_message(value).to_record()}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return {"$tuple": [_encode(item) for item in value]}
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _encode(item) for key, item in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return {"$text": str(value)}


def _decode(value):
    if isinstance(value, dict):
        if "$message" in value:
            return restore_text(value["$message"])
        if "$tuple" in value:
            return tuple(_decode(item) for item in value["$tuple"])
        if "$text" in value:
            return value["$text"]
        return {key: _decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


def restore_text(record):
    if "parts" in record:
        return Message("", parts=tuple(_decode(value) for value in record["parts"]))
    return Message(
        record["key"], {name: _decode(value) for name, value in record.get("values", {}).items()}
    )


def message(key: str, /, **values) -> Message:
    return Message(key, values)


def format_message(key: str, /, locale=None, **values):
    value = message(key, **values)
    return value if locale is None else value.render(locale)


def tr(key: str, locale=None):
    value = message(key)
    return value if locale is None else value.render(locale)


def render_text(value, locale=None) -> str:
    if isinstance(value, Message):
        return value.render(locale)
    return "" if value is None else str(value)


def canonical_text(value):
    return value.render(DEFAULT_LOCALE) if isinstance(value, Message) else value


def join_text(separator, values):
    values = tuple(
        diagnostic_message(value) if isinstance(value, BaseException) else value for value in values
    )
    if not any(isinstance(value, Message) for value in values):
        return separator.join(str(value) for value in values)
    parts = []
    for value in values:
        if parts:
            parts.append(separator)
        parts.append(value)
    return Message("", parts=parts)


def diagnostic_message(value):
    if isinstance(value, Message):
        return value
    if isinstance(value, BaseException) and value.args and isinstance(value.args[0], Message):
        return value.args[0]
    return message("diagnostic.external", diagnostic=str(value))


def render_record(value, locale=None):
    if isinstance(value, Message):
        return value.render(locale)
    if isinstance(value, dict):
        result = {key: render_record(item, locale) for key, item in value.items()}
        for key, item in value.items():
            if isinstance(item, Message):
                result[key + "_data"] = item.to_record()
        return result
    if isinstance(value, (list, tuple)):
        return [render_record(item, locale) for item in value]
    return value


def message_record(value):
    if isinstance(value, Message):
        return {"message": value.render(), "message_data": value.to_record()}
    return {"message": str(value)}


def record_message(record, field="message"):
    payload = record.get(field + "_data")
    if payload:
        try:
            return restore_text(payload)
        except (KeyError, ValueError, TypeError):
            pass
    return record.get(field, "")


def restore_record(value):
    if isinstance(value, dict):
        result = {key: restore_record(item) for key, item in value.items()}
        for key in tuple(result):
            if key.endswith("_data") and key[:-5] in result:
                restored = record_message(result, key[:-5])
                if isinstance(result[key], dict) and (
                    "key" in result[key] or "parts" in result[key]
                ):
                    result[key[:-5]] = restored
                    result.pop(key)
        return result
    if isinstance(value, list):
        return [restore_record(item) for item in value]
    return value


def exception_text(value):
    if isinstance(value, BaseException) and value.args and isinstance(value.args[0], Message):
        return value.args[0]
    return str(value)


def raw_diagnostics(value):
    if isinstance(value, BaseException):
        value = diagnostic_message(value)
    if not isinstance(value, Message):
        return []
    if value.key == "diagnostic.external":
        return [str(value.values["diagnostic"])]
    children = value.parts if value.parts is not None else value.values.values()
    return [detail for child in children for detail in raw_diagnostics(child)]


def state_text(value):
    if isinstance(value, Message):
        return value
    key = "state." + str(value)
    return (
        message(key)
        if key in _CATALOGS[DEFAULT_LOCALE] and "{" not in _CATALOGS[DEFAULT_LOCALE][key]
        else value
    )


_DATASET_NAME_ALIASES = {
    "porosity_names": ("porosity", "\u5b54\u9699"),
    "permeability_names": ("permeability", "\u6e17\u900f"),
}


def legacy_terms(key):
    return _DATASET_NAME_ALIASES[key]


def canonical_record(value):
    if isinstance(value, Message):
        return value.render(DEFAULT_LOCALE)
    if isinstance(value, dict):
        result = {key: canonical_record(item) for key, item in value.items()}
        for key, item in value.items():
            if isinstance(item, Message):
                result[key + "_data"] = item.to_record()
        return result
    if isinstance(value, (list, tuple)):
        return [canonical_record(item) for item in value]
    return value


def configure_cli_language(argv=None):
    import sys

    arguments = list(sys.argv[1:] if argv is None else argv)
    requested = os.environ.get("FRACRTM_LANGUAGE", current_locale())
    for index, argument in enumerate(arguments):
        if argument.startswith("--language="):
            requested = argument.split("=", 1)[1]
        elif argument == "--language" and index + 1 < len(arguments):
            requested = arguments[index + 1]
    set_locale(requested if requested in SUPPORTED_LOCALES else DEFAULT_LOCALE)
    return arguments


def argument_parser(description):
    import argparse

    class Formatter(argparse.HelpFormatter):
        def add_usage(self, usage, actions, groups, prefix=None):
            super().add_usage(usage, actions, groups, prefix or message("cli.usage").render())

    class Parser(argparse.ArgumentParser):
        def error(self, diagnostic):
            import sys

            self.print_usage(sys.stderr)
            record = {
                "event": "error",
                "code": "cli.arguments",
                **message_record(message("cli.invalid_arguments")),
                "diagnostic": diagnostic,
            }
            self.exit(2, json.dumps(record, ensure_ascii=False) + "\n")

    parser = Parser(description=render_text(description), add_help=False, formatter_class=Formatter)
    parser._positionals.title = message("cli.positional").render()
    parser._optionals.title = message("cli.options").render()
    parser.add_argument("-h", "--help", action="help", help=message("cli.help").render())
    parser.add_argument(
        "--language",
        choices=SUPPORTED_LOCALES,
        default=current_locale(),
        help=message("cli.language").render(),
    )
    return parser


def prepare_json(value):
    if isinstance(value, Message):
        return value.render(DEFAULT_LOCALE)
    if isinstance(value, dict):
        return {canonical_text(key): prepare_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [prepare_json(item) for item in value]
    return value
