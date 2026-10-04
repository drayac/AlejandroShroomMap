"""Offline tests for the tree code (no network, no database).

Run from backend/:   python test_trees.py      (or: pytest test_trees.py)
"""
import dated_tree
import tree

# ----------------------------------------------------------------------------
# A tiny hand-made dated tree whose correct answers are obvious.
#
#   R (100 Mya) +-- X (60) +-- Y (20) +-- A_a
#               |           |         +-- A_b        genus A, family FamA
#               |           +-- B_c                  genus B, family FamB
#               +-- C_d                              genus C, family FamC
#
# Tips are 0..3 (A_a, A_b, B_c, C_d), then internal nodes Y=4, X=5, R=6.
# ----------------------------------------------------------------------------
TOY = {
    "n_tips": 4,
    "tips": ["A_a", "A_b", "B_c", "C_d"],
    "parent": [4, 4, 5, 6, 5, 6, -1],
    "length": [20, 20, 60, 100, 40, 40, 0],
    "labels": {"5": "CladeX.rn.d8s.tre", "6": "mrcaott1ott2"},  # 5 has a junk suffix; 6 is unnamed
    "genus_family": {"A": "FamA", "B": "FamB", "C": "FamC"},
}


def toy():
    return dated_tree.Megatree(TOY)


def tips_of(node):
    return [node] if "children" not in node else [t for c in node["children"] for t in tips_of(c)]


def test_ages_come_from_branch_lengths():
    mt = toy()
    assert mt.height == 100
    assert [mt.age(n) for n in (4, 5, 6)] == [20, 60, 100]


def test_exact_species_get_real_split_ages():
    r = dated_tree.build({"A a": {}, "B c": {}, "C d": {}}, toy())
    assert r["root_age"] == 100
    root = r["tree"]
    assert root["name"] is None  # 'mrcaott...' is not a real name
    by_name = {c.get("name"): c for c in root["children"]}
    assert "C d" in by_name and "CladeX" in by_name
    assert by_name["CladeX"]["age"] == 60  # A a / B c split
    assert r["approx"] == {} and r["undated"] == []


def test_node_names_are_cleaned_of_build_artifacts():
    mt = toy()
    assert mt.labels == {5: "CladeX"}  # suffix stripped, 'mrcaott...' dropped


def test_single_child_chains_collapse():
    r = dated_tree.build({"A a": {}, "B c": {}}, toy())
    assert r["root_age"] == 60  # Y has only one kept child, so it is not a split
    assert sorted(t["name"] for t in tips_of(r["tree"])) == ["A a", "B c"]


def test_missing_species_is_grafted_at_its_genus():
    r = dated_tree.build({"A zzz": {}, "B c": {}}, toy())  # genus A exists, A zzz doesn't
    assert r["approx"] == {"A zzz": "genus"}
    assert r["root_age"] == 60  # splits from B c at X, not somewhere inside genus A
    tip = next(t for t in tips_of(r["tree"]) if t["name"] == "A zzz")
    assert tip["approx"] == "genus" and tip["age"] == 0.0


def test_missing_genus_falls_back_to_family():
    r = dated_tree.build({"Qqq www": {"family": "FamB"}, "C d": {}}, toy())
    assert r["approx"] == {"Qqq www": "family"}
    assert r["root_age"] == 100


def test_unplaceable_species_are_reported_not_dropped_silently():
    r = dated_tree.build({"A a": {}, "B c": {}, "Zz yy": {"family": "Nowhere"}}, toy())
    assert r["undated"] == ["Zz yy"]
    assert dated_tree.build({"A a": {}, "Zz yy": {}}, toy()) is None  # <2 placed


# ----------------------------------------------------------------------------
# tree.py: Open Tree of Life label parsing (regression: the quoted, disambiguated
# labels used for names that also exist in another kingdom were being dropped)
# ----------------------------------------------------------------------------
def test_open_tree_label_spellings():
    cases = {
        "Fagus_sylvatica_ott774712": ("Fagus sylvatica", 774712),
        "Salix alba (species in kingdom Archaeplastida) ott164209": ("Salix alba", 164209),
        "Salix (genus in kingdom Archaeplastida) ott458856": ("Salix", 458856),
        "Saliceae_ott509390": ("Saliceae", 509390),
        "mrcaott8858ott737360": (None, None),
        "": (None, None),
    }
    for label, want in cases.items():
        assert tree._split_label(label) == want, label


def test_newick_with_quoted_labels_and_deep_unary_chains():
    nwk = "(((((('Salix alba (species in kingdom Archaeplastida) ott1')mrcaott1ott2)mrcaott1ott3)x)y)(B_ott2,C_ott3)Clade_ott9)root_ott0;"
    root = tree.parse_newick(nwk)
    simple = tree._simplify(root, {1: ["Salix alba"], 2: ["B"], 3: ["C"]})
    assert sorted(t["name"] for t in tree._tips(simple)) == ["B", "C", "Salix alba"]


def test_linnaean_fallback_groups_by_rank():
    taxa = {
        "Fagus sylvatica": {"phylum": "T", "class": "M", "order": "Fagales", "family": "Fagaceae", "genus": "Fagus"},
        "Quercus robur": {"phylum": "T", "class": "M", "order": "Fagales", "family": "Fagaceae", "genus": "Quercus"},
        "Pinus sylvestris": {"phylum": "T", "class": "P", "order": "Pinales", "family": "Pinaceae", "genus": "Pinus"},
    }
    t = tree._taxonomic_tree(taxa)
    fagaceae = next(c for c in t["children"] if c.get("name") == "Fagaceae" or any(
        x.get("name") == "Fagaceae" for x in c.get("children", [])))
    assert sorted(x["name"] for x in tips_of(fagaceae)) == ["Fagus sylvatica", "Quercus robur"]


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("PASS", name)
    print(f"\n{len(tests)} tests passed")
