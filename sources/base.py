"""
The prospecting source contract.

A source turns a query — an industry, a location, a page of a CSV — into a
list of raw business dicts. It does not touch the database, score anything,
or dedupe: prospect.py does all of that, identically for every source. That
keeps adding a new source (a directory, an association member list, a
scraped list) to writing one `search()` function.

Keys a source may return (all optional except `name`):

    name legal_name abn website email phone address suburb state postcode
    industry category size_band rating review_count linkedin facebook
    instagram description source_ref
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


@dataclass
class Field:
    """One input on the prospecting form."""
    name: str
    label: str
    placeholder: str = ""
    required: bool = False
    kind: str = "text"          # text | number | textarea | select
    options: list[str] = field(default_factory=list)
    default: str = ""
    help: str = ""


class Source(Protocol):
    KEY: str
    LABEL: str
    DESCRIPTION: str
    FIELDS: list[Field]

    def available(self) -> tuple[bool, str]: ...
    def search(self, query: dict[str, Any]) -> list[dict[str, Any]]: ...


@dataclass
class SourceInfo:
    key: str
    label: str
    description: str
    fields: list[Field]
    available: bool
    unavailable_reason: str
    search: Callable[[dict[str, Any]], list[dict[str, Any]]]
