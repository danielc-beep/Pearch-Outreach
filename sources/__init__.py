"""Registry of prospecting sources."""
from __future__ import annotations

from typing import Any

from sources import csv_import, explorium, google_places, seed
from sources.base import Field, SourceInfo

_MODULES = [explorium, google_places, csv_import, seed]


def all_sources() -> list[SourceInfo]:
    infos: list[SourceInfo] = []
    for module in _MODULES:
        ok, reason = module.available()
        infos.append(SourceInfo(
            key=module.KEY,
            label=module.LABEL,
            description=module.DESCRIPTION,
            fields=module.FIELDS,
            available=ok,
            unavailable_reason=reason,
            search=module.search,
        ))
    return infos


def get_source(key: str) -> SourceInfo:
    for info in all_sources():
        if info.key == key:
            return info
    raise KeyError(f"unknown prospecting source: {key}")


def search(key: str, query: dict[str, Any]) -> list[dict[str, Any]]:
    return get_source(key).search(query)


__all__ = ["all_sources", "get_source", "search", "Field", "SourceInfo"]
