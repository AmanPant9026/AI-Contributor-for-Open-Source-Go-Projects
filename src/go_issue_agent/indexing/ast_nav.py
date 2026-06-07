"""ast_nav -- tree-sitter-backed navigation of Go source.

This is the agent's structural "eyes": given a Go file it returns the symbols
defined in it (functions, methods, types) with their line ranges and one-line
signatures. `repo_map` builds on top of this.

We deliberately keep signatures (no bodies): a signature is high-signal and
cheap; a body is the opposite. Everything here is pure and deterministic, so it
is unit-tested on a fixture with no sandbox.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import tree_sitter_go
from tree_sitter import Language, Parser, Node

_GO_LANGUAGE = Language(tree_sitter_go.language())

# Node types that introduce a top-level definition we care about.
_DEF_TYPES = {"function_declaration", "method_declaration", "type_declaration"}


@dataclass(frozen=True)
class Symbol:
    """One defined thing in a Go file."""
    kind: str          # "func" | "method" | "type"
    name: str          # e.g. "Hello", "Start", "Config"
    start_line: int    # 1-indexed, inclusive
    end_line: int      # 1-indexed, inclusive
    signature: str     # one-line declaration (no body)


@lru_cache(maxsize=1)
def _parser() -> Parser:
    return Parser(_GO_LANGUAGE)


def _text(src: bytes, node: Node) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _signature(src: bytes, node: Node) -> str:
    """The declaration text up to the body block (so 'func F(a int) error',
    'type Config struct', etc.) collapsed to a single line."""
    body = node.child_by_field_name("body")
    end = body.start_byte if body is not None else node.end_byte
    raw = src[node.start_byte:end].decode("utf-8", "replace")
    # For type declarations there's no 'body' field; cut at the first '{' so a
    # struct/interface shows 'type Config struct' rather than its whole field list.
    if node.type == "type_declaration" and "{" in raw:
        raw = raw[: raw.index("{")]  # keeps the 'struct'/'interface' keyword before the brace
    one_line = " ".join(raw.split())
    return one_line.rstrip("{ ").strip()


def parse_source(src: bytes) -> list[Symbol]:
    """Extract top-level symbols from Go source bytes."""
    root = _parser().parse(src).root_node
    out: list[Symbol] = []
    for n in root.children:
        if n.type not in _DEF_TYPES:
            continue
        if n.type == "type_declaration":
            # a type_declaration may group several specs: type ( A ...; B ... )
            for spec in n.children:
                if spec.type == "type_spec":
                    name = spec.child_by_field_name("name")
                    if name is not None:
                        out.append(Symbol(
                            "type", _text(src, name),
                            n.start_point[0] + 1, n.end_point[0] + 1,
                            _signature(src, n),
                        ))
            continue
        name_node = n.child_by_field_name("name")
        if name_node is None:
            continue
        kind = "method" if n.type == "method_declaration" else "func"
        out.append(Symbol(
            kind, _text(src, name_node),
            n.start_point[0] + 1, n.end_point[0] + 1,
            _signature(src, n),
        ))
    return out


def parse_file(path: str | Path) -> list[Symbol]:
    """Extract top-level symbols from a Go file on disk."""
    return parse_source(Path(path).read_bytes())


def identifiers(src: bytes) -> list[str]:
    """Every identifier / type-identifier token used in the source.

    Used by repo_map to discover cross-file references. Returns a flat list
    (with duplicates) so callers can count reference frequency.
    """
    root = _parser().parse(src).root_node
    names: list[str] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in ("identifier", "type_identifier", "field_identifier"):
            names.append(src[node.start_byte:node.end_byte].decode("utf-8", "replace"))
        stack.extend(node.children)
    return names


def find_definitions(root: str | Path, name: str) -> list[tuple[str, Symbol]]:
    """Find where `name` is defined across all .go files under `root`.

    Returns (relative_path, Symbol) pairs. Skips _test.go and Go-ignored dirs
    (_examples, testdata, dotfiles). The agent uses this to jump from a symbol it
    saw to the file/line where it lives.
    """
    from pathlib import PurePosixPath
    root = Path(root)
    hits: list[tuple[str, Symbol]] = []
    for go_file in sorted(root.rglob("*.go")):
        if go_file.name.endswith("_test.go"):
            continue
        rel = str(go_file.relative_to(root))
        parts = PurePosixPath(rel).parts
        if any(p.startswith("_") or p.startswith(".") or p == "testdata" for p in parts):
            continue
        for sym in parse_file(go_file):
            if sym.name == name:
                hits.append((rel, sym))
    return hits
