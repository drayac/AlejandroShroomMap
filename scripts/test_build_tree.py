"""Offline test of scripts/build_tree.py + backend/dated_tree.py on tiny made-up trees.

Run from the repo root:   python scripts/test_build_tree.py
"""
import gzip, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
import build_tree  # noqa: E402
import dated_tree  # noqa: E402


def build(files, backbone=None):
    with tempfile.TemporaryDirectory() as d:
        argv, out = [], os.path.join(d, "t.json.gz")
        for name, text in files.items():
            p = os.path.join(d, name + ".tre"); open(p, "w").write(text); argv += ["--tree", f"{name}={p}"]
        if backbone:
            p = os.path.join(d, "bb.json"); json.dump(backbone, open(p, "w")); argv += ["--backbone", p]
        build_tree.main(argv + ["--out", out])
        return dated_tree.Megatree(json.load(gzip.open(out, "rt")))


BACKBONE = {"age": 300, "name": "Root", "children": [{"group": "a"}, {"age": 200, "children": [{"group": "b"}, {"group": "c"}]}]}
A = "((Panthera_leo_FELIDAE_CARNIVORA:10,Panthera_tigris_FELIDAE_CARNIVORA:10):50,Canis_lupus_CANIDAE_CARNIVORA:60);"
B = "#NEXUS\nbegin trees;\ntranslate 1 Aquila_chrysaetos, 2 Falco_peregrinus, 3 Passer_domesticus;\ntree t1 = [&R] ((1:20,2:20):30,3:50);\nend;\n"
C = "(Rana_temporaria:40,(Bufo_bufo:30,Bufo_viridis:30):10);"


def test_groups_joined_by_backbone():
    mt = build({"a": A, "b": B, "c": C}, BACKBONE)
    assert mt.height == 300 and mt.n_tips == 9
    age = lambda x, y: dated_tree.build({x: {}, y: {}}, mt)["root_age"]
    assert age("Panthera leo", "Panthera tigris") == 10
    assert age("Panthera leo", "Canis lupus") == 60
    assert age("Aquila chrysaetos", "Falco peregrinus") == 20
    assert age("Aquila chrysaetos", "Rana temporaria") == 200
    assert age("Panthera leo", "Rana temporaria") == 300


def test_family_from_tip_label_and_graft():
    mt = build({"a": A, "b": B, "c": C}, BACKBONE)
    assert mt.genus_family["Panthera"] == "Felidae"
    r = dated_tree.build({"Felis catus": {"family": "Felidae"}, "Panthera leo": {}}, mt)  # family-level graft
    assert r["approx"] == {"Felis catus": "family"}
    r = dated_tree.build({"Bufo calamita": {}, "Bufo bufo": {}}, mt)                     # genus-level graft
    assert r["approx"] == {"Bufo calamita": "genus"} and r["root_age"] == 30
    r = dated_tree.build({"Homo sapiens": {}, "Bufo bufo": {}}, mt)                      # nothing to anchor to
    assert r is None


def test_single_tree_and_quoted_labels():
    mt = build({"x": "(('Amanita_muscaria':5,Amanita_phalloides:5):15,Boletus_edulis:20)Agaricomycetes;"})
    assert mt.height == 20 and mt.labels  # root keeps its name
    assert dated_tree.build({"Amanita muscaria": {}, "Boletus edulis": {}}, mt)["root_age"] == 20


def test_open_names_are_not_species():
    sp = lambda l: build_tree.tip_species(l)[0]
    assert sp("Amanita_muscaria_G0123_NL5003") == "Amanita_muscaria"
    assert sp("AGAricus_augustus_AF291286") == "Agaricus_augustus"
    for open_name in ("Sebacina_sp", "Hemimycena_sp._2._G0059", "Amanita_spThai03_KF877291",
                      "Leucoagaricus_aff_marriageae", "9_KF000456_Sebacina_sp", "Inocybe_cf_geophylla"):
        assert sp(open_name) is None, open_name

def build_with(files, backbone, extra):
    with tempfile.TemporaryDirectory() as d:
        argv, out = [], os.path.join(d, "t.json.gz")
        for name, text in files.items():
            p = os.path.join(d, name + ".tre"); open(p, "w").write(text); argv += ["--tree", f"{name}={p}"]
        p = os.path.join(d, "bb.json"); json.dump(backbone, open(p, "w")); argv += ["--backbone", p]
        build_tree.main(argv + extra + ["--out", out])
        return dated_tree.Megatree(json.load(gzip.open(out, "rt")))

def test_outgroup_does_not_inflate_crown_age():
    # Homo sapiens as outgroup of an amphibian tree (as in Jetz & Pyron 2018): crown is 40, not 350.
    amph = "(Homo_sapiens:350,(Rana_temporaria:40,(Bufo_bufo:30,Bufo_viridis:30):10):310);"
    bb = {"age": 400, "children": [{"group": "a"}, {"group": "b"}]}
    with_drop = build_with({"a": A, "b": amph}, bb, ["--drop", "Homo_sapiens"])
    assert with_drop.n_tips == 6
    assert dated_tree.build({"Rana temporaria": {}, "Bufo bufo": {}}, with_drop)["root_age"] == 40
    assert dated_tree.build({"Rana temporaria": {}, "Panthera leo": {}}, with_drop)["root_age"] == 400
    # an unparseable outgroup label (Upham's "_Anolis_carolinensis") is re-rooted away too
    mam = "(_Anolis_carolinensis:320,(Panthera_leo:60,Canis_lupus:60):260);"
    mt = build({"x": mam})
    assert mt.height == 60

if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f(); print("ok", n)
