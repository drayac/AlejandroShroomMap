#!/usr/bin/env python3
"""One-off build step: turn published dated trees (Newick/Nexus) into the compact JSON the
backend ships with (backend/data/<name>.json.gz). Standard library only.

Usage
  # one tree (mushrooms)
  python scripts/build_tree.py --tree agaricomycetes=path/to/tree.nwk --out backend/data/fungi_tree.json.gz

  # several group trees joined by a backbone (vertebrates)
  python scripts/build_tree.py --backbone scripts/backbone.json \\
      --tree mammals=mammals.tre --tree birds=birds.tre --tree amphibians=amphibians.tre \\
      --tree squamates=squamates.tre --tree fishes=fishes.tre \\
      --out backend/data/vertebrate_tree.json.gz

Options
  --taxonomy FILE        TSV "genus<TAB>family" to enable the family-level fallback placement
  --resolve-families     look the family of every genus up in GBIF (needs network; slow, once)
  --tip-underscore       (default) tip labels are Genus_species[...]; extra parts are dropped

Input expectations: ultrametric trees with branch lengths in MILLIONS OF YEARS. A Nexus file
(with a TREES block, optionally a TRANSLATE table) is accepted; if it holds several trees the
FIRST is used - give it a consensus/MCC tree, not a posterior sample. Tip labels such as
"Panthera_leo" or Upham's "Panthera_leo_FELIDAE_CARNIVORA" are understood (the latter also
yields the family).

Output (same format the backend's dated_tree.py reads): tips 0..n_tips-1, then internal nodes;
parent[i] (-1 for the root), length[i] (Myr from i up to its parent), labels, genus_family.

Backbone JSON: a nested tree of {"age": Mya, "name": optional, "children": [...]} whose leaves are
{"group": "<name given to --tree>"}. Each group's crown is hung under its backbone parent with a
branch of (parent age - group crown age). The ages in scripts/backbone.json are rough, hand-set
values: check them against the literature before relying on deep splits.
"""
import argparse, concurrent.futures, gzip, json, re, sys, urllib.parse, urllib.request
from collections import Counter


# ------------------------------------------------------------------ Newick ----
def _extract_newick(text):
    """Newick string from a plain Newick or Nexus file (first tree; TRANSLATE applied)."""
    if re.search(r"^\s*#nexus", text, re.I):
        trans = {}
        m = re.search(r"\btranslate\b(.*?);", text, re.I | re.S)
        if m:
            for k, v in re.findall(r"(\S+)\s+([^\s,;]+)\s*[,;]?", m.group(1)):
                trans[k] = v.strip("'")
        m = re.search(r"\btree\s+[^=]*=\s*(?:\[[^\]]*\]\s*)*(\(.*?;)", text, re.I | re.S)
        if not m:
            raise ValueError("no tree found in Nexus file")
        return m.group(1), trans
    m = re.search(r"\(.*;", text, re.S)
    if not m:
        raise ValueError("no Newick tree found")
    return m.group(0), {}


def parse_newick(s):
    """Iterative parser. Returns (labels, parent, length, is_leaf) as parallel lists; node 0 is
    the root. Comments in [] are skipped; quoted labels are supported."""
    labels, parent, length, leaf = [], [], [], []
    stack, cur, i, n = [], None, 0, len(s)

    def new(par):
        labels.append(""); parent.append(par); length.append(0.0); leaf.append(True)
        return len(labels) - 1

    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
        elif c == "[":
            i = s.index("]", i) + 1
        elif c == "(":
            node = new(stack[-1] if stack else -1)
            leaf[node] = False
            stack.append(node); cur = None; i += 1
        elif c == ",":
            cur = None; i += 1
        elif c == ")":
            cur = stack.pop(); i += 1
        elif c == ":":
            j = i + 1
            while j < n and s[j] not in "(),;[":
                j += 1
            length[cur] = float(s[i + 1:j] or 0)
            i = j
        elif c == ";":
            break
        else:  # a label
            if c == "'":
                j, out = i + 1, []
                while j < n:
                    if s[j] == "'":
                        if j + 1 < n and s[j + 1] == "'":
                            out.append("'"); j += 2; continue
                        j += 1; break
                    out.append(s[j]); j += 1
                label = "".join(out)
            else:
                j = i
                while j < n and s[j] not in "(),:;[":
                    j += 1
                label = s[i:j].strip()
            i = j
            if cur is None:               # a tip
                cur = new(stack[-1])
            labels[cur] = label           # else: the name of the clade just closed
    return labels, parent, length, leaf


UPHAM = re.compile(r"^([A-Z][A-Za-z-]+)_([a-z][a-z-]+)_([A-Z]+)_([A-Z]+)$")
GENUS = re.compile(r"^[A-Z][A-Za-z-]+$")
EPITHET = re.compile(r"^[a-z][a-z-]+$")            # rejects "sp.", "spThai03", "2."
NOT_EPITHET = {"sp", "spp", "cf", "aff", "nov", "indet", "unknown", "x"}  # open names, not species


def tip_species(label):
    """-> (species 'Genus_species' or None, family or None)."""
    label = label.strip().strip("'")
    m = UPHAM.match(label)
    if m:
        return f"{m.group(1)}_{m.group(2)}", m.group(3).title()
    parts = label.replace(" ", "_").split("_")
    if len(parts) >= 2 and GENUS.match(parts[0]) and EPITHET.match(parts[1]) and parts[1] not in NOT_EPITHET:
        return f"{parts[0][0].upper()}{parts[0][1:].lower()}_{parts[1]}", None
    return None, None


def load_group(path):
    text = open(path, encoding="utf-8", errors="replace").read()
    nwk, trans = _extract_newick(text)
    labels, parent, length, leaf = parse_newick(nwk)
    if trans:
        labels = [trans.get(l, l) if leaf[k] else l for k, l in enumerate(labels)]
    return labels, parent, length, leaf


# ------------------------------------------------------------ assembling ----
def depths(parent, length):
    d = [None] * len(parent)
    for start in range(len(parent)):
        path, node = [], start
        while node != -1 and d[node] is None:
            path.append(node); node = parent[node]
        base = 0.0 if node == -1 else d[node]
        for k in reversed(path):
            base += length[k] if parent[k] != -1 else 0.0
            d[k] = base
    return d


def group_info(name, path):
    labels, parent, length, leaf = load_group(path)
    d = depths(parent, length)
    tips = [k for k in range(len(labels)) if leaf[k]]
    height = max(d[k] for k in tips)
    lo = min(d[k] for k in tips)
    if height - lo > 0.02 * height:
        print(f"  warning: {name}: tips are not equidistant from the root ({lo:.1f}..{height:.1f} Myr); "
              f"is this an ultrametric time tree?", file=sys.stderr)
    return dict(name=name, labels=labels, parent=parent, length=length, leaf=leaf, tips=tips, height=height)


def assemble(groups, backbone, drop=()):
    """Merge group trees (+ backbone) into the global arrays. `drop`: species (Genus_species)
    to leave out, e.g. an outgroup such as Homo_sapiens in the amphibian tree."""
    tips, tip_family = [], {}
    # first pass: global tip ids
    gmap = []
    seen = set(drop)
    for g in groups:
        m, kept = {}, 0
        for k in g["tips"]:
            sp, fam = tip_species(g["labels"][k])
            if not sp or sp in seen:
                continue
            seen.add(sp); m[k] = len(tips); tips.append(sp); kept += 1
            if fam:
                tip_family[sp.split("_")[0]] = fam
        print(f"  {g['name']}: {len(g['tips'])} tips, {kept} usable, crown {g['height']:.1f} Myr")
        gmap.append(m)
    n_tips = len(tips)

    parent, length, labels = [-1] * n_tips, [0.0] * n_tips, {}
    group_root = {}

    def add_node(par, ln, label=None):
        parent.append(par); length.append(ln)
        if label:
            labels[len(parent) - 1] = label
        return len(parent) - 1

    for g, m in zip(groups, gmap):
        # which nodes survive: a tip we kept, or an internal node with a kept descendant
        keep = set(m)
        for k in list(m):
            a = g["parent"][k]
            while a != -1 and a not in keep:
                keep.add(a); a = g["parent"][a]
        # Re-root at the crown (most recent common ancestor of the kept tips): an outgroup or
        # a dropped tip must not make the group look older than it is.
        kids = {}
        for k in keep:
            kids.setdefault(g["parent"][k], []).append(k)
        crown = kids[-1][0]
        while crown not in m and len(kids.get(crown, [])) == 1:
            crown = kids[crown][0]
        a = g["parent"][crown]
        while a != -1:
            keep.discard(a); a = g["parent"][a]
        d = depths(g["parent"], g["length"])
        crown_age = g["height"] - d[crown]
        if crown_age < g["height"] - 1e-6:
            print(f"  {g['name']}: crown re-rooted at {crown_age:.1f} Myr (tree root {g['height']:.1f})")
        newid = dict(m)
        for k in range(len(g["labels"])):  # parents before children is not guaranteed -> two passes
            if k in keep and k not in newid:
                newid[k] = add_node(-1, 0.0, g["labels"][k] if not g["leaf"][k] else None)
        root = None
        for k in keep:
            p = g["parent"][k]
            if k == crown:
                root = newid[k]
            else:
                parent[newid[k]] = newid[p]
                length[newid[k]] = g["length"][k]
        group_root[g["name"]] = (root, crown_age)

    if backbone is None:
        assert len(groups) == 1, "several trees need --backbone"
        top = group_root[groups[0]["name"]][0]
        return tips, parent, length, labels, top, tip_family

    def build(node):
        if "group" in node:
            root, crown = group_root[node["group"]]
            if not labels.get(root):
                labels[root] = node["group"]
            return root, crown
        me = add_node(-1, 0.0, node.get("name"))
        for ch in node["children"]:
            cid, cage = build(ch)
            ln = node["age"] - cage
            if ln < 0:
                print(f"  warning: backbone node {node.get('name') or node['age']} ({node['age']} Mya) is younger "
                      f"than child crown ({cage:.1f}); using 0.1", file=sys.stderr)
                ln = 0.1
            parent[cid] = me; length[cid] = ln
        return me, node["age"]

    top, _ = build(backbone)
    return tips, parent, length, labels, top, tip_family


def resolve_families(genera):
    def one(g):
        try:
            url = "https://api.gbif.org/v1/species/match?" + urllib.parse.urlencode({"name": g, "rank": "genus"})
            with urllib.request.urlopen(url, timeout=10) as r:
                return g, json.load(r).get("family")
        except Exception:
            return g, None
    out = {}
    with concurrent.futures.ThreadPoolExecutor(8) as ex:
        for k, (g, f) in enumerate(ex.map(one, sorted(genera))):
            if f:
                out[g] = f
            if k % 500 == 499:
                print(f"  ...{k + 1}/{len(genera)} genera", file=sys.stderr)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tree", action="append", required=True, metavar="NAME=FILE")
    ap.add_argument("--backbone")
    ap.add_argument("--drop", action="append", default=[], metavar="GENUS_SPECIES",
                    help="leave this species out (an outgroup); repeatable")
    ap.add_argument("--taxonomy")
    ap.add_argument("--resolve-families", action="store_true")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    groups = []
    for spec in a.tree:
        name, _, path = spec.partition("=")
        if not path:
            ap.error("--tree wants NAME=FILE")
        groups.append(group_info(name, path))
    backbone = json.load(open(a.backbone)) if a.backbone else None
    tips, parent, length, labels, top, fam = assemble(groups, backbone, a.drop)

    # Keep only what hangs under `top`, with tips first (they already are) and a clean root.
    parent[top] = -1; length[top] = 0.0
    genus_family = dict(fam)
    if a.taxonomy:
        for line in open(a.taxonomy, encoding="utf-8"):
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2 and parts[0] and parts[1]:
                genus_family.setdefault(parts[0], parts[1])
    genera = {t.split("_")[0] for t in tips}
    if a.resolve_families:
        todo = genera - set(genus_family)
        print(f"resolving {len(todo)} genera in GBIF...")
        genus_family.update(resolve_families(todo))
    genus_family = {g: f for g, f in genus_family.items() if g in genera}

    out = {"n_tips": len(tips), "tips": tips, "parent": parent, "length": [round(x, 4) for x in length],
           "labels": {str(k): v for k, v in labels.items() if v}, "genus_family": genus_family}
    with gzip.open(a.out, "wt", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"wrote {a.out}: {len(tips)} tips, {len(parent) - len(tips)} internal nodes, "
          f"{len(out['labels'])} named, {len(genus_family)}/{len(genera)} genera with a family")


if __name__ == "__main__":
    main()
