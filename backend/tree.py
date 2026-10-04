"""The evolutionary tree of the species in the collection.

Three sources, best first; the response says which one it used ("source"):

1. "gbotb" - a time-scaled tree (dated_tree.py): branch lengths are real divergence
   times in millions of years. Covers mushroom-forming fungi (Agaricomycetes); see that module for how
   species it lacks are placed, and its accuracy caveats.
2. "otl" - the Open Tree of Life (opentreeoflife.org, CC0) synthetic tree, a real
   phylogeny from published studies but with topology only (no usable branch
   lengths), drawn as a plain cladogram. Used when the dated tree can't place at
   least two of the species (e.g. mosses). We match names to its taxon ids (TNRS),
   ask for the subtree connecting just our species, and parse the Newick it returns.
3. "taxonomy" - if Open Tree is unreachable too, the Linnaean ranks stored for each
   leaf (kingdom > phylum > class > order > family > genus > species) as a tree.
   Still a tree, but a classification rather than a phylogeny.

Output nodes: internal {"name": str|None, "rank": str|None, "children": [...]}, tips
{"name": species, "species": [names it stands for]}; the dated tree also carries
"age" (Mya) on every node and "approx" on tips.
"""
import re

import httpx

import dated_tree

OTL = "https://api.opentreeoflife.org/v3"
HEADERS = {"User-Agent": "AlejandroShroomMap/1.0 (+https://github.com/)"}
RANK_ORDER = ("kingdom", "phylum", "class", "order", "family", "genus")

_tree_cache: dict[tuple, dict] = {}


# ---------------------------------------------------------------- Newick ----

def _read_label(s: str, i: int) -> tuple[str, int]:
    if s[i] == "'":
        i += 1
        out = []
        while i < len(s):
            if s[i] == "'":
                if i + 1 < len(s) and s[i + 1] == "'":  # escaped quote
                    out.append("'")
                    i += 2
                    continue
                i += 1
                break
            out.append(s[i])
            i += 1
        return "".join(out), i
    start = i
    while i < len(s) and s[i] not in "(),:;":
        i += 1
    return s[start:i].strip(), i


def parse_newick(s: str) -> dict:
    """Iterative (the unary chains Open Tree returns can be hundreds deep)."""
    s = s.strip()
    stack: list[dict] = []
    last: dict | None = None  # the internal node most recently closed by ')'
    i = 0
    while i < len(s):
        c = s[i]
        if c == "(":
            node = {"label": "", "children": []}
            if stack:
                stack[-1]["children"].append(node)
            stack.append(node)
            last = None
            i += 1
        elif c == ",":
            last = None
            i += 1
        elif c == ")":
            last = stack.pop()
            i += 1
        elif c == ":":
            i += 1  # skip a branch length, if any
            while i < len(s) and s[i] not in "(),;":
                i += 1
        elif c == ";":
            break
        elif c.isspace():
            i += 1
        else:
            label, i = _read_label(s, i)
            if last is not None:
                last["label"] = label  # name of the clade that just closed
            elif stack:
                stack[-1]["children"].append({"label": label, "children": []})  # a tip
            else:
                return {"label": label, "children": []}  # a one-node tree
    if last is None:
        raise ValueError("unparseable newick")
    return last


def _split_label(label: str) -> tuple[str | None, int | None]:
    """Parse an Open Tree label into (name, ott id); unnamed 'mrcaott..' -> (None, None).

    Two spellings occur: plain  'Fagus_sylvatica_ott774712'  and, for a name that
    also exists in another kingdom, a quoted disambiguated one:
    'Salix alba (species in kingdom Archaeplastida) ott164209'. The second kind
    is common and must not be dropped.
    """
    if not label or re.fullmatch(r"mrcaott\d+ott\d+", label):
        return None, None
    m = re.match(r"^(.*?)[_ ]ott(\d+)$", label)
    if not m:
        return re.sub(r"\s*\([^)]*\)\s*$", "", label).replace("_", " "), None
    base = re.sub(r"\s*\([^)]*\)\s*$", "", m.group(1))  # drop '(species in kingdom ...)'
    return base.replace("_", " "), int(m.group(2))


def _simplify(node: dict, names_by_ott: dict[int, list[str]]) -> dict:
    """Collapse single-child chains and convert to the output node shape."""
    while len(node["children"]) == 1:
        node = node["children"][0]
    name, ott = _split_label(node["label"])
    if not node["children"]:
        species = names_by_ott.get(ott) or ([name] if name else [])
        return {"name": species[0] if species else (name or "?"), "species": species}
    return {
        "name": name,
        "rank": None,
        "children": [_simplify(c, names_by_ott) for c in node["children"]],
    }


def _tips(node: dict):
    if "children" not in node:
        yield node
    else:
        for c in node["children"]:
            yield from _tips(c)


# ------------------------------------------------------------- Open Tree ----

def _otl_tree(names: list[str]) -> tuple[dict | None, list[str]]:
    """(tree, unplaced names) from the Open Tree of Life, or (None, all names) on failure."""
    try:
        resp = httpx.post(
            f"{OTL}/tnrs/match_names",
            json={"names": names, "context_name": "Fungi", "do_approximate_matching": False},
            headers=HEADERS,
            timeout=20.0,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception:
        return None, list(names)

    names_by_ott: dict[int, list[str]] = {}
    for r in results:
        matches = r.get("matches") or []
        if not matches:
            continue
        taxon = matches[0].get("taxon") or {}
        if taxon.get("ott_id") is not None:
            names_by_ott.setdefault(int(taxon["ott_id"]), []).append(r["name"])
    if len(names_by_ott) < 2:
        return None, list(names)

    newick = None
    ids = set(names_by_ott)
    for _ in range(2):  # one retry, dropping ids the synthetic tree rejects
        try:
            resp = httpx.post(
                f"{OTL}/tree_of_life/induced_subtree",
                json={"ott_ids": sorted(ids), "label_format": "name_and_id"},
                headers=HEADERS,
                timeout=30.0,
            )
            if resp.status_code == 400:
                body = resp.json()
                bad = {int(k) for k in (body.get("unknown") or {})} | {int(k) for k in (body.get("broken") or {})}
                if not bad & ids or len(ids - bad) < 2:
                    return None, list(names)
                ids -= bad
                continue
            resp.raise_for_status()
            newick = resp.json().get("newick")
            break
        except Exception:
            return None, list(names)
    if not newick:
        return None, list(names)

    try:
        tree = _simplify(parse_newick(newick), names_by_ott)
    except Exception:
        return None, list(names)
    if "children" not in tree:
        return None, list(names)
    placed = {n for t in _tips(tree) for n in t["species"]}
    return tree, [n for n in names if n not in placed]


# ------------------------------------------------------ Linnaean fallback ----

def _taxonomic_tree(taxa: dict[str, dict]) -> dict:
    """taxa: species name -> taxonomy dict. Nested by rank, single-child chains collapsed."""
    root: dict = {"children": {}}
    for species, tax in taxa.items():
        node = root
        for rank in RANK_ORDER:
            value = tax.get(rank)
            if not value:
                continue
            child = node["children"].setdefault(value, {"rank": rank, "children": {}})
            node = child
        node["children"].setdefault(species, {"rank": "species", "children": {}, "tip": True})

    def convert(name: str | None, rank: str | None, node: dict) -> dict:
        while len(node["children"]) == 1 and not node.get("tip"):
            (name, child), = node["children"].items()
            rank, node = child["rank"], child
        if node.get("tip") or not node["children"]:
            return {"name": name, "species": [name]}
        return {
            "name": name,
            "rank": rank,
            "children": [convert(n, c["rank"], c) for n, c in sorted(node["children"].items())],
        }

    return convert(None, None, root)


# ------------------------------------------------------------------ entry ----

def tree_for(taxa: dict[str, dict]) -> dict:
    """taxa: accepted species name -> its stored taxonomy dict.

    Returns {"source": "gbotb" | "otl" | "taxonomy" | None, "tree": node | None,
             "unplaced": [names], "species_count": n}, plus "dated", "root_age" and
    "approx" when the tree is time-scaled.
    """
    names = sorted(taxa)
    if len(names) < 2:
        return {"source": None, "tree": None, "unplaced": [], "species_count": len(names)}

    key = tuple(names)
    if key in _tree_cache:
        return _tree_cache[key]

    try:
        dated = dated_tree.build(taxa)
    except Exception:  # a broken/missing data file must never take the tree down
        dated = None
    if dated is not None:
        result = {
            "source": "gbotb",
            "dated": True,
            "root_age": dated["root_age"],
            "tree": dated["tree"],
            "approx": dated["approx"],  # species placed only at genus/family level
            "unplaced": dated["undated"],
            "species_count": len(names),
        }
        _tree_cache[key] = result
        return result

    tree, unplaced = _otl_tree(names)
    if tree is not None:
        result = {"source": "otl", "tree": tree, "unplaced": unplaced, "species_count": len(names)}
        _tree_cache[key] = result  # only the real phylogeny is worth remembering
        return result

    return {
        "source": "taxonomy",
        "tree": _taxonomic_tree(taxa),
        "unplaced": [],
        "species_count": len(names),
    }
