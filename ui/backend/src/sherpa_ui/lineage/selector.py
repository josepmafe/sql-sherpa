from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from sherpa_ui.lineage.manifest_graph import ManifestGraph

MAX_SELECTOR_LENGTH = 1000
SHOWN_LENGTH = 100  # how much of a rejected selector the message echoes
SUPPORTED_FORMS = (
    "supported selectors: a model, seed, snapshot or exposure name, source:<source>, source:<source>.<table>, "
    "exposure:<name>, "
    "config.meta.domain:<domain>; graph operators +atom, atom+, n+atom, atom+n, n+atom+m; "
    "several atoms separated by spaces"
)
RESERVED_NAME_HINT = "a package, top-level folder or test name is a group in dbt; use the layer filter for a folder"
# `[n]+` before and `+[m]` after one atom; the digits are the depth, none means unlimited.
_ATOM_WITH_OPERATORS = re.compile(r"^(?P<up>(?P<up_depth>\d*)\+)?(?P<atom>[^+]+?)(?P<down>\+(?P<down_depth>\d*))?$")
_NAME = r"[\w-]+"
_SOURCE_ATOM = re.compile(rf"^source:(?P<source>{_NAME})(?:\.(?P<table>{_NAME}))?$")
_EXPOSURE_ATOM = re.compile(rf"^exposure:(?P<name>{_NAME})$")
_DOMAIN_ATOM = re.compile(rf"^config\.meta\.domain:(?P<domain>{_NAME})$")
_NAME_ATOM = re.compile(rf"^{_NAME}$")


class UnsupportedSelector(Exception):
    """The selector uses dbt syntax outside the supported subset (UI spec §6.5)."""

    def __init__(self, selector: str, hint: str | None = None) -> None:
        shown = selector if len(selector) <= SHOWN_LENGTH else f"{selector[:SHOWN_LENGTH]}..."
        reason = f"{hint}; " if hint else ""
        self.message = f"unsupported selector {shown!r}; {reason}{SUPPORTED_FORMS}"
        super().__init__(self.message)


class SelectorEvaluator:
    """Evaluates a subset of dbt's `--select` syntax on the manifest graph, without running dbt."""

    def __init__(self, graph: ManifestGraph) -> None:
        self._graph = graph

    def select(self, selector: str) -> frozenset[str]:
        if len(selector) > MAX_SELECTOR_LENGTH:
            raise UnsupportedSelector(selector)

        tokens = selector.split()
        if not tokens:
            return frozenset(self._graph.nodes)

        selected: set[str] = set()
        for token in tokens:
            selected |= self._select_token(token)
        return frozenset(selected)

    def _select_token(self, token: str) -> set[str]:
        match = _ATOM_WITH_OPERATORS.match(token)
        if match is None:
            raise UnsupportedSelector(token)

        matched = self._match_atom(match["atom"], token)
        result = set(matched)
        if match["up"]:
            result |= self._walk(matched, self._graph.parents, _depth(match["up_depth"]))
        if match["down"]:
            result |= self._walk(matched, self._graph.children, _depth(match["down_depth"]))
        return result

    def _match_atom(self, atom: str, token: str) -> set[str]:
        nodes = self._graph.nodes.values()
        if (source := _SOURCE_ATOM.match(atom)) is not None:
            return {
                node.unique_id
                for node in nodes
                if node.resource_type == "source"
                and node.source_name == source["source"]
                and (source["table"] is None or node.name == source["table"])
            }
        if (exposure := _EXPOSURE_ATOM.match(atom)) is not None:
            return {
                node.unique_id for node in nodes if node.resource_type == "exposure" and node.name == exposure["name"]
            }
        if (domain := _DOMAIN_ATOM.match(atom)) is not None:
            return {node.unique_id for node in nodes if node.domain == domain["domain"]}
        if _NAME_ATOM.match(atom) is not None:
            if atom in self._graph.reserved_names:
                raise UnsupportedSelector(token, RESERVED_NAME_HINT)

            # dbt's bare names reach models, seeds, snapshots and exposures; a source table needs `source:`.
            return {node.unique_id for node in nodes if node.resource_type != "source" and node.name == atom}
        raise UnsupportedSelector(token)

    def _walk(self, start: Iterable[str], step: Callable[[str], tuple[str, ...]], depth: int | None) -> set[str]:
        reached: set[str] = set()
        frontier = set(start)
        level = 0
        while frontier and (depth is None or level < depth):
            frontier = {neighbour for unique_id in frontier for neighbour in step(unique_id)} - reached
            reached |= frontier
            level += 1
        return reached


def _depth(digits: str | None) -> int | None:
    return int(digits) if digits else None
