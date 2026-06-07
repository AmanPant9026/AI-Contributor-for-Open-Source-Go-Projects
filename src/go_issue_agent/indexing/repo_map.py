"""repo_map -- a compact, ranked "table of contents" of a Go repository.

Four steps (see docs/stage-3.md for the long-form explanation):

  1. PARSE   every non-test .go file with tree-sitter -> symbols (via ast_nav).
  2. GRAPH   build a file->file reference graph: an edge referrer -> definer for
             each use of a symbol defined in another file, weighted by frequency.
  3. RANK    run PageRank over that graph. A file is "important" if many
             (important) files reference the symbols it defines.
  4. SKELETON walk files in rank order and emit their signatures (no bodies)
             until a token budget is hit -> a small, high-signal overview.

Pure and deterministic: no sandbox, no network. PageRank is implemented here
(simple power iteration) to avoid pulling in networkx.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import ast_nav
from .ast_nav import Symbol


@dataclass
class FileInfo:
    path: str                       # repo-relative
    symbols: list[Symbol] = field(default_factory=list)
    rank: float = 0.0


def _build_ignored(rel: str) -> bool:
    """True if any path component starts with '_' or '.' (e.g. _examples, .git)
    or is 'testdata'. The Go toolchain ignores exactly these for builds, so they
    are noise in a repo map of the library."""
    from pathlib import PurePosixPath
    parts = PurePosixPath(rel).parts
    return any(p.startswith("_") or p.startswith(".") or p == "testdata" for p in parts)


def _collect(root: Path) -> tuple[dict[str, FileInfo], dict[str, str]]:
    """Parse every non-test .go file. Returns (files_by_path, name->defining_path).

    If a name is defined in multiple files we keep the first (rare for Go
    top-level identifiers within one module); references still resolve sensibly.
    Files under Go-ignored dirs (_examples, testdata, dotfiles) are skipped.
    """
    files: dict[str, FileInfo] = {}
    defines: dict[str, str] = {}
    for go_file in sorted(root.rglob("*.go")):
        if go_file.name.endswith("_test.go"):
            continue
        rel = str(go_file.relative_to(root))
        if _build_ignored(rel):
            continue
        syms = ast_nav.parse_file(go_file)
        files[rel] = FileInfo(path=rel, symbols=syms)
        for s in syms:
            defines.setdefault(s.name, rel)
    return files, defines


def _build_graph(root: Path, files: dict[str, FileInfo],
                 defines: dict[str, str]) -> dict[str, dict[str, float]]:
    """Edges referrer -> definer, weighted by how many times the referrer uses
    a symbol the definer defines (self-references excluded)."""
    graph: dict[str, dict[str, float]] = {p: {} for p in files}
    for rel, info in files.items():
        src = (root / rel).read_bytes()
        for ident in ast_nav.identifiers(src):
            definer = defines.get(ident)
            if definer is not None and definer != rel:
                graph[rel][definer] = graph[rel].get(definer, 0.0) + 1.0
    return graph


def pagerank(graph: dict[str, dict[str, float]], *, damping: float = 0.85,
             iterations: int = 50, tol: float = 1e-8,
             personalization: dict[str, float] | None = None) -> dict[str, float]:
    """Weighted PageRank via power iteration. `graph[a][b]=w` is an edge a->b.

    Dangling nodes (no out-edges) redistribute their mass uniformly, so the
    scores always sum to 1. `personalization` (optional) biases the random-jump
    target distribution -- a Stage-4 hook for focusing on issue-relevant files.
    """
    nodes = list(graph)
    n = len(nodes)
    if n == 0:
        return {}
    if personalization:
        total = sum(personalization.get(x, 0.0) for x in nodes) or 1.0
        teleport = {x: personalization.get(x, 0.0) / total for x in nodes}
    else:
        teleport = {x: 1.0 / n for x in nodes}

    rank = {x: 1.0 / n for x in nodes}
    out_w = {x: sum(graph[x].values()) for x in nodes}

    for _ in range(iterations):
        new = {x: (1.0 - damping) * teleport[x] for x in nodes}
        dangling = damping * sum(rank[x] for x in nodes if out_w[x] == 0.0)
        for x in nodes:
            new[x] += dangling * teleport[x]
        for a in nodes:
            if out_w[a] == 0.0:
                continue
            share = damping * rank[a] / out_w[a]
            for b, w in graph[a].items():
                new[b] += share * w
        delta = sum(abs(new[x] - rank[x]) for x in nodes)
        rank = new
        if delta < tol:
            break
    return rank


def _est_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token) -- good enough for budgeting."""
    return max(1, len(text) // 4)


@dataclass
class RepoMap:
    ranked_files: list[FileInfo]    # all files, most-important first
    skeleton: str                   # the budgeted text overview
    truncated: bool                 # True if the budget cut some files


def build_repo_map(root: str | Path, *, budget_tokens: int = 3000,
                   personalization: dict[str, float] | None = None) -> RepoMap:
    """Build the ranked, budgeted repo map for a Go project rooted at `root`."""
    root = Path(root)
    files, defines = _collect(root)
    graph = _build_graph(root, files, defines)
    ranks = pagerank(graph, personalization=personalization)
    for p, info in files.items():
        info.rank = ranks.get(p, 0.0)
    # Most important first; tie-break by path for determinism.
    ordered = sorted(files.values(), key=lambda f: (-f.rank, f.path))

    lines: list[str] = []
    used = 0
    truncated = False
    for info in ordered:
        block = [info.path]
        for s in info.symbols:
            block.append(f"  {s.signature}  [L{s.start_line}]")
        chunk = "\n".join(block) + "\n"
        cost = _est_tokens(chunk)
        if used + cost > budget_tokens and lines:
            truncated = True
            break
        lines.append(chunk)
        used += cost
    return RepoMap(ranked_files=ordered, skeleton="".join(lines).rstrip("\n"),
                   truncated=truncated)
