#!/usr/bin/env python3
"""Rename the tips of a Varga et al. 2019 FastDate tree with the paper's corrected names.

The trees in Varga_2019_NEE_PhyloBayes_FastDate.zip carry the raw alignment labels (typos, accession
numbers, "10_KF000459_Helvellosebacina_helvelloide"); Species_name_match.csv in the same Zenodo record
maps each one to its final name. Standard library only.

  python scripts/relabel_varga.py fastdate_kronogram_222.tree2 Species_name_match.csv > varga_222.nwk
"""
import csv, re, sys


def main(tree_path, csv_path):
    names = {}
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            names[row["Alignment_names"].strip()] = row["Species_names_final"].strip()
    tree = open(tree_path, encoding="utf-8").read()
    hit, miss = 0, []

    def rename(m):
        nonlocal hit
        label = m.group(2)
        if label in names:
            hit += 1
            return m.group(1) + names[label]
        miss.append(label)
        return m.group(0)

    out = re.sub(r"([(,])([^(),:;\[]+)", rename, tree)
    print(f"renamed {hit} tips, {len(miss)} not in the table{': ' + ', '.join(miss[:5]) if miss else ''}",
          file=sys.stderr)
    sys.stdout.write(out)


if __name__ == "__main__":
    main(*sys.argv[1:3])
