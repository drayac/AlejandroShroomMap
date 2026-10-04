# Tree data for Alejandro Shroom Map

The dated tree is a build artifact: `backend/data/fungi_tree.json.gz`. Without it the app still works and uses the
Open Tree of Life (undated) instead. Build it once, on a machine with internet access:

```bash
python scripts/build_tree.py \
    --tree agaricomycetes=PATH/TO/TREE.nwk \
    --resolve-families \
    --out backend/data/fungi_tree.json.gz
```

Requirements for every input tree: ultrametric, branch lengths in **millions of years**, tips like
`Genus_species` (extra suffixes are dropped; Upham's `Genus_species_FAMILY_ORDER` also gives the family).
Give it ONE tree per group (a consensus/MCC tree, not a posterior sample; a Nexus file's first tree is used).
`--resolve-families` asks GBIF for the family of each genus (a few minutes, needed for the family-level
fallback placement); `--taxonomy file.tsv` ("genus<TAB>family") does the same offline.

## Mushrooms (built 2026-10-04)

Varga et al. (2019), "Megaphylogeny resolves global patterns of mushroom evolution", *Nature Ecology &
Evolution*. Data: https://zenodo.org/records/5003983 (licence **CC0**). Exact steps used:

```bash
curl -LO "https://zenodo.org/records/5003983/files/Varga_2019_NEE_PhyloBayes_FastDate.zip?download=1"
curl -LO "https://zenodo.org/records/5003983/files/Species_name_match.csv?download=1"
unzip Varga_2019_NEE_PhyloBayes_FastDate.zip
# 10 replicate dated trees of all 5,284 taxa; *.tree2 are the authors' ultrametric versions.
# Crown ages 394-472 Myr; replicate 222 (437.8) is the median one.
python scripts/relabel_varga.py PhyloBayes_FastDate/FastDate_analysis/fastdate_kronogram_222.tree2 \
    Species_name_match.csv > varga_222.nwk
python scripts/build_tree.py --tree agaricomycetes=varga_222.nwk --resolve-families \
    --out backend/data/fungi_tree.json.gz
```

Result: 5,284 tips -> 4,784 species (open names such as `Sebacina_sp`, `aff.`/`cf.` and duplicate specimens
dropped), 679/820 genera with a GBIF family. The raw labels carry accession numbers and typos
(`10_KF000459_Helvellosebacina_helvelloide`), which is why the relabelling step matters.
