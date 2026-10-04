"""A time-scaled evolutionary tree of the species in the collection.

The backbone is a dated mega-tree of mushroom-forming fungi (Agaricomycetes) whose branch lengths are in
MILLIONS OF YEARS, converted once by scripts/build_tree.py into data/fungi_tree.json.gz and loaded
lazily here (see README.md for which published trees go into it). For a set of species we take
the part of it that connects just them, which gives each branching point an age (in Mya) - that
is what makes the drawn branch lengths mean something.

Species the mega-tree does not contain are not dropped; they are attached the way
V.PhyloMaker does it, and flagged so the interface can say so:
  - genus level: a congener is in the tree, so the species goes at that genus's
    root node (we know it belongs to the genus, not where inside it);
  - family level: no congener, but the family is present, so it goes at the
    family's root node.
Anything else (groups the mega-tree does not cover, odd names) is reported as
"undated" and left out of this tree.

Output nodes match tree.py: internal {"name": str|None, "rank": None, "age": Mya,
"children": [...]}, tips {"name", "species": [name], "age": 0.0, "approx": None |
"genus" | "family"}.
"""
import gzip
import json
import os
import threading
from array import array
from collections import defaultdict

DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "fungi_tree.json.gz")


class Megatree:
    """Compact in-memory form of the mega-tree: parent/length arrays plus lookups."""

    def __init__(self, raw: dict):
        self.tips: list[str] = raw["tips"]
        self.n_tips: int = raw["n_tips"]
        self.parent = array("i", raw["parent"])
        self.length = array("d", raw["length"])
        self.n_nodes = len(self.parent)
        # Unnamed clades come through as 'mrcaott123ott456' - not a real name - and
        # many real ones carry a build-artifact suffix ('Rosales.rn.d8s.tre'), so keep
        # only what's before the first dot.
        self.labels: dict[int, str] = {}
        for k, v in raw["labels"].items():
            v = (v or "").split(".", 1)[0].strip()
            if v and not v.startswith("mrcaott"):
                self.labels[int(k)] = v
        self.genus_family: dict[str, str] = raw["genus_family"]
        self.root = self.parent.index(-1)

        self.tip_index = {name: i for i, name in enumerate(self.tips)}
        self.genus_tips: dict[str, list[int]] = defaultdict(list)
        self.family_tips: dict[str, list[int]] = defaultdict(list)
        for i, name in enumerate(self.tips):
            genus = name.split("_", 1)[0]
            self.genus_tips[genus].append(i)
            family = self.genus_family.get(genus)
            if family:
                self.family_tips[family].append(i)

        self.depth = self._depths()
        # The tree is time-calibrated, so every tip should sit at the same distance
        # from the root - that distance is the root's age.
        self.height = max(self.depth[i] for i in range(self.n_tips))

    def _depths(self) -> array:
        """Distance (Myr) from the root to every node, in one linear pass."""
        depth = array("d", [-1.0]) * self.n_nodes
        depth[self.root] = 0.0
        for start in range(self.n_nodes):
            if depth[start] >= 0:
                continue
            path = []
            node = start
            while depth[node] < 0:
                path.append(node)
                node = self.parent[node]
            d = depth[node]
            for n in reversed(path):
                d += self.length[n]
                depth[n] = d
        return depth

    def age(self, node: int) -> float:
        return max(0.0, self.height - self.depth[node])

    def ancestors(self, node: int) -> list[int]:
        """node, its parent, ... up to and including the root."""
        out = []
        while node != -1:
            out.append(node)
            node = self.parent[node]
        return out

    def lca(self, nodes: list[int]) -> int:
        """Most recent common ancestor of the given nodes."""
        chain = self.ancestors(nodes[0])
        for n in nodes[1:]:
            have = set(chain)
            cur = n
            while cur not in have:
                cur = self.parent[cur]
            chain = chain[chain.index(cur):]
        return chain[0]

    def attach_point(self, tips: list[int]) -> int:
        """Where to hang a species that belongs with these tips: their common root
        node (or, if there is only one tip, its parent - a tip can't have children)."""
        node = self.lca(tips)
        return self.parent[node] if node < self.n_tips else node


_megatree: Megatree | None = None
_lock = threading.Lock()


def megatree() -> Megatree | None:
    """The shared mega-tree, loaded on first use (a second or so, ~30 MB); None if the
    data file is missing, so the caller can fall back to an undated tree."""
    global _megatree
    if _megatree is None:
        with _lock:
            if _megatree is None:
                if not os.path.exists(DATA_PATH):
                    return None
                with gzip.open(DATA_PATH, "rt") as f:
                    _megatree = Megatree(json.load(f))
    return _megatree


def build(taxa: dict[str, dict], mt: Megatree | None = None) -> dict | None:
    """taxa: accepted species name -> its stored taxonomy dict (for the family).

    Returns {"tree", "root_age", "approx": {name: level}, "undated": [names]},
    or None if fewer than two species could be placed.
    """
    mt = mt or megatree()
    if mt is None:
        return None

    # Every species becomes a node id: its real tip, or a virtual tip (ids >= n_nodes)
    # hung off the genus/family node it belongs with.
    placements: list[tuple[str, int, str | None]] = []  # (name, node id, approx level)
    virtual_parent: dict[int, int] = {}
    undated: list[str] = []
    next_virtual = mt.n_nodes

    for name in sorted(taxa):
        key = name.replace(" ", "_")
        if key in mt.tip_index:
            placements.append((name, mt.tip_index[key], None))
            continue
        genus = name.split(" ", 1)[0]
        family = (taxa[name] or {}).get("family") or mt.genus_family.get(genus)
        level, anchor_tips = None, None
        if mt.genus_tips.get(genus):
            level, anchor_tips = "genus", mt.genus_tips[genus]
        elif family and mt.family_tips.get(family):
            level, anchor_tips = "family", mt.family_tips[family]
        if anchor_tips is None:
            undated.append(name)
            continue
        virtual_parent[next_virtual] = mt.attach_point(anchor_tips)
        placements.append((name, next_virtual, level))
        next_virtual += 1

    if len(placements) < 2:
        return None

    def parent_of(node: int) -> int:
        return virtual_parent[node] if node >= mt.n_nodes else mt.parent[node]

    # The union of every placed species' path up to the root, as parent -> children.
    kids: dict[int, list[int]] = defaultdict(list)
    seen: set[int] = set()
    for _, node, _ in placements:
        while node != -1 and node not in seen:
            seen.add(node)
            p = parent_of(node)
            if p != -1:
                kids[p].append(node)
            node = p

    info = {node: (name, level) for name, node, level in placements}

    def convert(node: int) -> dict:
        while len(kids.get(node, ())) == 1:  # collapse chains of single-child nodes
            node = kids[node][0]
        if node not in kids:
            name, level = info[node]
            return {"name": name, "species": [name], "age": 0.0, "approx": level}
        return {
            "name": mt.labels.get(node) if node < mt.n_nodes else None,
            "rank": None,
            "age": round(mt.age(node), 1),
            "children": [convert(c) for c in kids[node]],
        }

    top = mt.lca([node if node < mt.n_nodes else virtual_parent[node] for _, node, _ in placements])
    tree = convert(top)
    if "children" not in tree:
        return None
    return {
        "tree": tree,
        "root_age": tree["age"],
        "approx": {name: level for name, _, level in placements if level},
        "undated": undated,
    }
