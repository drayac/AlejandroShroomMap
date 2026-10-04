const PASSPHRASE_KEY = "shroommap_passphrase";
const TITLE_HIDE_ZOOM = 3;
const MAP_STYLE_KEY = "shroommap_map_style";
// (localStorage is per-origin, so these keys can't clash with the Rock Map
// even if both were ever served from one domain.)

// OpenFreeMap's full set of hosted styles (all free, same vector tiles,
// just different cartography) - see https://openfreemap.org.
const MAP_STYLES = [
  { id: "liberty", label: "Liberty (default)" },
  { id: "bright", label: "Bright" },
  { id: "positron", label: "Positron (light)" },
  { id: "dark", label: "Dark" },
  { id: "fiord", label: "Fiord" },
];
const DEFAULT_MAP_STYLE = "liberty";
function mapStyleUrl(id) {
  return `https://tiles.openfreemap.org/styles/${id}`;
}

const savedMapStyle = localStorage.getItem(MAP_STYLE_KEY);
const initialMapStyle = MAP_STYLES.some((s) => s.id === savedMapStyle) ? savedMapStyle : DEFAULT_MAP_STYLE;

// OpenFreeMap's own terms require the OpenMapTiles + OpenStreetMap credit
// but say the "OpenFreeMap" link is optional - so that one part is dropped
// by overriding the style's source attribution (a style-level attribution
// wins over the one in the tile source's TileJSON). The required credits stay.
const BASEMAP_ATTRIBUTION =
  '<a href="https://www.openmaptiles.org/" target="_blank">&copy; OpenMapTiles</a> Data from ' +
  '<a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>';
function transformStyle(_previous, next) {
  if (next.sources && next.sources.openmaptiles) next.sources.openmaptiles.attribution = BASEMAP_ATTRIBUTION;
  return next;
}

const map = new maplibregl.Map({
  container: "map",
  center: [10, 25],
  zoom: 1.6,
  minZoom: 0,
  maxZoom: 19,
  attributionControl: false, // added by hand below, so it can go in the top-left corner
});
// The constructor can't take a transformStyle, so the first style is set
// right after - same call (and same transform) as every later style switch.
//
// diff:false = always a full style reload on a switch. (The Rock Map this was
// copied from needed that, because plain setStyle() patches the style in place
// and silently drops any layers the app added itself. This app adds none, so it
// isn't required here - it's kept because it is the tested, known-good path for
// the attribution override above, and a style switch is a rare action.)
const STYLE_OPTIONS = { diff: false, transformStyle };
map.setStyle(mapStyleUrl(initialMapStyle), STYLE_OPTIONS);

// The credit control (the small "i") can't be removed - the OSM/OpenMapTiles
// licenses require the credit to stay reachable - so it's kept
// as small and out of the way as possible: top-left corner (nothing else is
// there; zoom buttons are top-right), collapsed to just the "i".
//
// MapLibre's compact control otherwise starts expanded - a ~270px strip of
// text - until the first tap. It only switches into that expanded state once
// it has its first credit text - after the style loads, not at construction -
// so a one-time removal right here would run too early. Instead, watch for
// that first expansion and undo it once; later taps on the "i" work as normal.
// (The full list is also in the Map layers panel.)
map.addControl(new maplibregl.AttributionControl({ compact: true }), "top-left");
const attribControl = document.querySelector(".maplibregl-ctrl-attrib");
if (attribControl) {
  const collapseOnce = new MutationObserver(() => {
    if (!attribControl.classList.contains("maplibregl-compact-show")) return;
    attribControl.removeAttribute("open");
    attribControl.classList.remove("maplibregl-compact-show");
    collapseOnce.disconnect();
  });
  collapseOnce.observe(attribControl, { attributes: true, attributeFilter: ["class"] });
}

map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");

let leavesData = { type: "FeatureCollection", features: [] };
let pendingCoords = null;
let pickingOnMap = false;

// ---------- Tap/click map to close open panels ----------
let lastGestureAt = 0; // guards against the trailing synthetic click after a long-press

function closeAllSheets() {
  document.querySelectorAll(".sheet").forEach((el) => el.classList.add("hidden"));
  document.body.classList.remove("menu-open");
}

// ---------- Collapsible icon menu (locate/layers/gallery/analysis) ----------
document.getElementById("menu-toggle-btn").addEventListener("click", () => {
  document.body.classList.toggle("menu-open");
});

map.on("click", () => {
  // Picking a location for "choose on the map" registers its own click
  // handler for this same click - let that own it exclusively, don't also
  // race it with cancelPicking() here.
  if (pickingOnMap) return;
  if (Date.now() - lastGestureAt < 500) return;
  closeAllSheets();
});

// ---------- Add a leaf via long-press (touch) ----------
// Double-click/double-tap is left as MapLibre's default (zoom in), the
// familiar gesture on basically every map app - it used to be repurposed to
// add a leaf here instead, but that meant double-tapping to zoom (something
// people do on reflex) silently opened the add-leaf flow instead. Long-press
// still adds a leaf at a point on touch devices; on desktop, use "+" ->
// "Choose on the map".
function startAddLeafAt(lngLat) {
  lastGestureAt = Date.now();
  openAddPanel({ lat: lngLat.lat, lon: lngLat.lng });
}

const LONG_PRESS_MS = 550;
const LONG_PRESS_MOVE_TOLERANCE_PX = 10;
let longPressTimer = null;
let longPressOrigin = null;

map.getCanvas().addEventListener("touchstart", (e) => {
  if (e.touches.length !== 1) return;
  const touch = e.touches[0];
  longPressOrigin = { x: touch.clientX, y: touch.clientY };
  longPressTimer = setTimeout(() => {
    longPressTimer = null;
    const rect = map.getCanvas().getBoundingClientRect();
    const lngLat = map.unproject([touch.clientX - rect.left, touch.clientY - rect.top]);
    startAddLeafAt(lngLat);
  }, LONG_PRESS_MS);
});

map.getCanvas().addEventListener("touchmove", (e) => {
  if (!longPressTimer || !longPressOrigin) return;
  const touch = e.touches[0];
  const moved = Math.hypot(touch.clientX - longPressOrigin.x, touch.clientY - longPressOrigin.y);
  if (moved > LONG_PRESS_MOVE_TOLERANCE_PX) {
    clearTimeout(longPressTimer);
    longPressTimer = null;
  }
});

for (const evt of ["touchend", "touchcancel"]) {
  map.getCanvas().addEventListener(evt, () => {
    if (longPressTimer) {
      clearTimeout(longPressTimer);
      longPressTimer = null;
    }
  });
}

// ---------- Title overlay fade ----------
const titleOverlay = document.getElementById("title-overlay");
function updateTitleVisibility() {
  const z = map.getZoom();
  titleOverlay.style.opacity = z > TITLE_HIDE_ZOOM ? "0" : "1";
}
map.on("zoom", updateTitleVisibility);
map.on("load", updateTitleVisibility);

// Fetched straight away rather than on the map's "load" event: "load" waits for the
// basemap's first tiles, and the leaves (plain DOM markers) don't need them - so a
// slow or failing tile server must not be able to hide the whole collection.
fetchLeaves();

// ---------- Map style switcher ----------
const mapStyleSelect = document.getElementById("map-style-select");
mapStyleSelect.innerHTML = MAP_STYLES.map((s) => `<option value="${s.id}">${escapeHtml(s.label)}</option>`).join("");
mapStyleSelect.value = initialMapStyle;
mapStyleSelect.addEventListener("change", () => {
  localStorage.setItem(MAP_STYLE_KEY, mapStyleSelect.value);
  map.setStyle(mapStyleUrl(mapStyleSelect.value), STYLE_OPTIONS);
});

// Note: leaves are rendered as DOM-based maplibregl.Marker elements, not a
// GeoJSON source + circle/symbol layers. A GeoJSON source added at map load
// time (even non-clustered, even renamed, even removed and re-added) had its
// worker-side tile registration silently vanish on this deployment — setData
// never threw but nothing ever rendered or queried back. Markers sidestep
// the tile/worker pipeline entirely (they're just positioned DOM elements),
// so they render reliably regardless of that issue.
let leafMarkers = [];

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// Species names are written in italics by convention; "unknown" means unidentified.
function sciNameHtml(name) {
  return name && name !== "unknown" ? `<em>${escapeHtml(name)}</em>` : "Unidentified";
}

// Bar chart of how sure the AI was, and what else it considered - same visual
// language the Rock Map used for mineral composition.
function identificationChartHtml(properties) {
  const ident = properties.identification;
  if (!ident || typeof ident.confidence !== "number") return "";
  const rows = [
    { name: properties.scientific_name, pct: ident.confidence * 100 },
    ...(ident.alternatives || []).map((a) => ({ name: a.scientific_name, pct: (a.confidence || 0) * 100 })),
  ].filter((r) => r.name && r.name !== "unknown");
  if (!rows.length) return "";
  const bars = rows
    .map(
      (r, i) => `
    <div class="bar-row">
      <span class="bar-label">${sciNameHtml(r.name)}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${Math.max(2, Math.round(r.pct))}%; background:${LEAF_BAR_COLORS[Math.min(i, LEAF_BAR_COLORS.length - 1)]}"></div></div>
      <span class="bar-pct">${Math.round(r.pct)}%</span>
    </div>`
    )
    .join("");
  return `<div class="bar-chart"><p class="hint">AI identification &amp; alternatives</p>${bars}</div>`;
}
const LEAF_BAR_COLORS = ["#8a624b", "#b58b73", "#cfb09e", "#dfcbc0"];

// ---------- Edibility / toxicity ----------
// Same scales as backend/ai.py (EDIBILITY_SCALE, TOXICITY_SCALE, IDENTIFIABILITY_SCALE, SIMILAR_SPECIES_SCALE).
const EDIBILITY_LABELS = ["Not edible or unknown", "Edible but worthless", "Edible, mediocre", "Good edible", "Very good edible", "Choice edible"];
const TOXICITY_LABELS = ["No known toxicity", "Mildly toxic", "Poisonous", "Seriously poisonous", "Potentially deadly", "Deadly"];
const IDENTIFIABILITY_LABELS = ["", "needs microscopy or DNA", "needs tests a photo can't show", "an experienced eye usually suffices", "distinctive", "unmistakable"];
const SIMILAR_LABELS = ["no look-alikes", "one or two easy look-alikes", "a few look-alikes", "several look-alikes", "many look-alikes", "many near-identical species"];
const EAT_WARNING =
  "Never eat a mushroom because of this app. Photo identification by AI is often wrong, and deadly mushrooms can look like edible ones. Bring every mushroom you plan to eat to an expert (an official mushroom inspector, a trained pharmacist or a mycological society) for review before eating it.";

function scoreDots(value, kind) {
  let dots = "";
  for (let i = 1; i <= 5; i++) dots += `<span class="dot${i <= value ? " on" : ""}"></span>`;
  return `<span class="score-dots ${kind}" role="img" aria-label="${value} out of 5">${dots}</span>`;
}

function eatWarningHtml(short) {
  return `<p class="eat-warning">⚠️ ${short ? "Do not eat based on this app. Have any mushroom checked by an expert before eating it." : EAT_WARNING}</p>`;
}

// `safety`: {edibility, toxicity, worst_toxicity, notes, lookalikes}; `rel`: {score, share,
// identifiability, similar_species} (see _safety / _reliability in backend/ai.py). Toxicity is the
// identified species' own; worst_toxicity (over alternatives and look-alikes) greys out edibility.
function safetyHtml(safety, short, rel) {
  if (!safety || typeof safety.worst_toxicity !== "number") return eatWarningHtml(short);
  const risky = safety.worst_toxicity >= 2;
  const lookalikes = (safety.lookalikes || [])
    .map((l) => `<li><em>${escapeHtml(l.scientific_name)}</em>${l.common_name && l.common_name !== "unknown" ? ` (${escapeHtml(l.common_name)})` : ""}: ${escapeHtml(TOXICITY_LABELS[l.toxicity] || "")}</li>`)
    .join("");
  const relRow =
    rel && typeof rel.score === "number"
      ? `<div class="score-row">
        <span class="score-name">Confidence</span>${scoreDots(Math.round(rel.score * 5), "conf")}
        <span class="score-label">${Math.round(rel.score * 100)}%</span>
      </div>
      <span class="score-sub">${Math.round(rel.share * 100)}% of the AI's candidates · ${escapeHtml(IDENTIFIABILITY_LABELS[rel.identifiability] || "")} · ${escapeHtml(SIMILAR_LABELS[rel.similar_species] || "")}</span>`
      : "";
  return `
    <div class="safety">
      <div class="score-row${risky ? " muted" : ""}">
        <span class="score-name">Edibility</span>${scoreDots(safety.edibility, "edi")}
        <span class="score-label">${escapeHtml(EDIBILITY_LABELS[safety.edibility] || "")}</span>
      </div>
      <span class="score-sub">${risky ? "Meaningless here: a poisonous look-alike or alternative is possible." : "Only if the identification is right."}</span>
      <div class="score-row">
        <span class="score-name">Toxicity</span>${scoreDots(safety.toxicity, "tox")}
        <span class="score-label">${escapeHtml(TOXICITY_LABELS[safety.toxicity] || "")}</span>
      </div>
      ${relRow}
      ${safety.notes ? `<p class="score-notes">${escapeHtml(safety.notes)}</p>` : ""}
      ${lookalikes ? `<p class="score-sub">Poisonous look-alikes:</p><ul class="lookalikes">${lookalikes}</ul>` : ""}
      ${eatWarningHtml(short)}
    </div>`;
}

const TAXON_RANKS = [
  ["kingdom", "Kingdom"],
  ["phylum", "Phylum"],
  ["class", "Class"],
  ["order", "Order"],
  ["family", "Family"],
  ["genus", "Genus"],
  ["species", "Species"],
];

// The Linnaean ranks as a small table - kingdom down to species.
function classificationHtml(tax) {
  if (!tax) return "";
  const rows = TAXON_RANKS.filter(([key]) => tax[key])
    .map(([key, label]) => {
      const value = key === "genus" || key === "species" ? `<em>${escapeHtml(tax[key])}</em>` : escapeHtml(tax[key]);
      return `<span class="rank-label">${label}</span><span class="rank-value">${value}</span>`;
    })
    .join("");
  return rows ? `<div class="classification"><p class="hint">Classification</p><div class="rank-grid">${rows}</div></div>` : "";
}

function ecosystemHtml(eco) {
  if (!eco || !eco.ecoregion) return "";
  const detail = [eco.biome, eco.realm].filter(Boolean).join(" · ");
  return `<div class="ecosystem">🌍 <strong>${escapeHtml(eco.ecoregion)}</strong>${detail ? `<span class="meta"> ${escapeHtml(detail)}</span>` : ""}</div>`;
}

function popupHtml(properties, coords) {
  const date = properties.created_at ? new Date(properties.created_at).toLocaleDateString() : "unknown";
  const photo = properties.photo_url ? `<img src="${escapeHtml(properties.photo_url)}" alt="mushroom photo" />` : "";
  return `
    <div class="leaf-popup">
      ${photo}
      <div class="title">${escapeHtml(properties.title || "unknown")}</div>
      <div class="sci">${sciNameHtml(properties.scientific_name)}</div>
      <div class="desc">${escapeHtml(properties.description || "unknown")}</div>
      ${safetyHtml((properties.identification || {}).safety, true, (properties.identification || {}).reliability)}
      ${identificationChartHtml(properties)}
      ${classificationHtml(properties.taxonomy)}
      ${ecosystemHtml(properties.ecosystem)}
      <div class="meta">${coords[1].toFixed(4)}, ${coords[0].toFixed(4)} · ${date}</div>
      <div class="meta">Added by ${escapeHtml(properties.added_by || "unknown")}</div>
    </div>`;
}

function renderLeafMarkers(featureCollection) {
  leafMarkers.forEach((m) => m.remove());
  leafMarkers = featureCollection.features.map((f) => {
    const el = document.createElement("div");
    el.className = "leaf-marker";
    if (f.properties.photo_url) {
      const img = document.createElement("img");
      img.src = f.properties.photo_url;
      img.alt = "";
      el.appendChild(img);
    } else {
      el.textContent = "🍄";
    }
    const popup = new maplibregl.Popup({ closeButton: true, maxWidth: "280px" }).setHTML(
      popupHtml(f.properties, f.geometry.coordinates)
    );
    return new maplibregl.Marker({ element: el })
      .setLngLat(f.geometry.coordinates)
      .setPopup(popup)
      .addTo(map);
  });
}

// Only ever called from the initial fetchLeaves() below, not after adding a
// leaf later — re-zooming out every time someone adds one would be
// disorienting, this is meant purely as "where's my collection" on open.
function zoomToFitLeaves(featureCollection) {
  const coords = featureCollection.features
    .map((f) => f.geometry?.coordinates)
    .filter((c) => Array.isArray(c) && c.length === 2);
  if (!coords.length) return;

  const bounds = coords.reduce((b, c) => b.extend(c), new maplibregl.LngLatBounds(coords[0], coords[0]));

  map.fitBounds(bounds, {
    padding: { top: 70, bottom: 110, left: 40, right: 40 },
    maxZoom: 14, // a single leaf (or a tight cluster) shouldn't zoom in absurdly far
    duration: 3600, // slow/smooth rather than a quick snap (20% slower per feedback)
  });
}

async function fetchLeaves() {
  try {
    const res = await fetch("/api/leaves");
    leavesData = await res.json();
    renderLeafMarkers(leavesData);
    zoomToFitLeaves(leavesData);
  } catch (err) {
    console.error("Failed to load leaves", err);
  }
}

// ---------- Layers panel ----------
const layersPanel = document.getElementById("layers-panel");
document.getElementById("layers-btn").addEventListener("click", () => {
  closeAllSheets();
  layersPanel.classList.remove("hidden");
});

// ---------- Gallery ----------
let galleryCountry = null; // null = country list; a country name = that country's leaves
let galleryLeaf = null; // a leaf feature = its full detail view

// Opens straight into whichever country has the most recent addition,
// instead of the country list — per explicit feedback, that's almost always
// where you want to land anyway. The full list is still one tap away via
// the "‹ Countries" back button.
function mostRecentCountry(featureCollection) {
  const feats = featureCollection.features;
  if (!feats.length) return null;
  const latest = feats.reduce((a, b) =>
    new Date(a.properties.created_at) >= new Date(b.properties.created_at) ? a : b
  );
  return latest.properties.country || "unknown";
}

document.getElementById("gallery-btn").addEventListener("click", () => {
  closeAllSheets();
  galleryCountry = mostRecentCountry(leavesData);
  galleryLeaf = null;
  renderGallery(leavesData);
  document.getElementById("gallery-panel").classList.remove("hidden");
});

function monthYearLabel(d) {
  return d ? d.toLocaleDateString(undefined, { month: "long", year: "numeric" }) : "Unknown date";
}

function galleryRowHtml(f) {
  const p = f.properties;
  const d = p.created_at ? new Date(p.created_at) : null;
  const thumb = p.photo_url ? `<img src="${escapeHtml(p.photo_url)}" alt="" />` : "<div></div>";
  const dayLabel = d ? d.toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "";
  return `
    <div class="gallery-row" data-id="${escapeHtml(p.id)}">
      ${thumb}
      <div class="gallery-row-text">
        <div class="title">${escapeHtml(p.title || "unknown")}</div>
        <div class="sci">${sciNameHtml(p.scientific_name)}</div>
        <div class="meta">${dayLabel} · Added by ${escapeHtml(p.added_by || "unknown")}</div>
      </div>
    </div>`;
}

function galleryMonthGroupsHtml(feats) {
  let html = "";
  let lastMonthKey = null;
  for (const f of feats) {
    const d = f.properties.created_at ? new Date(f.properties.created_at) : null;
    const monthKey = d ? `${d.getFullYear()}-${d.getMonth()}` : "unknown";
    if (monthKey !== lastMonthKey) {
      html += `<div class="gallery-month">${escapeHtml(monthYearLabel(d))}</div>`;
      lastMonthKey = monthKey;
    }
    html += galleryRowHtml(f);
  }
  return html;
}

function groupByCountry(sortedFeats) {
  const byCountry = new Map();
  for (const f of sortedFeats) {
    const country = f.properties.country || "unknown";
    if (!byCountry.has(country)) byCountry.set(country, []);
    byCountry.get(country).push(f);
  }
  return byCountry;
}

function renderGallery(featureCollection) {
  const container = document.getElementById("gallery-list");
  const feats = featureCollection.features;

  if (!feats.length) {
    container.innerHTML = '<p class="gallery-empty">No mushrooms yet — add the first one!</p>';
    return;
  }

  const sorted = [...feats].sort(
    (a, b) => new Date(b.properties.created_at) - new Date(a.properties.created_at)
  );
  const byCountry = groupByCountry(sorted);

  let html = "";
  if (galleryLeaf) {
    const p = galleryLeaf.properties;
    html += `<button type="button" class="gallery-back-btn">‹ Back</button>`;
    html += `<div class="gallery-detail">${popupHtml(p, galleryLeaf.geometry.coordinates)}</div>`;
    html += `<button type="button" class="btn-secondary" id="gallery-view-on-map-btn">🗺️ View on map</button>`;
  } else if (galleryCountry === null) {
    // byCountry was built from `sorted` (newest leaf first), so each
    // country's key is already inserted in order of its own most recent
    // leaf — no extra sort needed beyond keeping "unknown" last.
    const countries = [...byCountry.keys()];
    const unknownIdx = countries.indexOf("unknown");
    if (unknownIdx !== -1) countries.push(countries.splice(unknownIdx, 1)[0]);
    for (const country of countries) {
      const countryFeats = byCountry.get(country);
      const count = countryFeats.length;
      const thumbFeat = countryFeats.find((f) => f.properties.photo_url);
      const thumb = thumbFeat
        ? `<img src="${escapeHtml(thumbFeat.properties.photo_url)}" alt="" />`
        : "<div></div>";
      html += `
        <div class="gallery-country-row" data-country="${escapeHtml(country)}">
          ${thumb}
          <span class="gallery-country-name">${escapeHtml(country === "unknown" ? "Unknown country" : country)}</span>
          <span class="gallery-country-count">${count} mushroom${count === 1 ? "" : "s"} ›</span>
        </div>`;
    }
  } else {
    const label = galleryCountry === "unknown" ? "Unknown country" : galleryCountry;
    html += `<button type="button" class="gallery-back-btn">‹ Countries</button>`;
    html += `<div class="gallery-country">${escapeHtml(label)}</div>`;
    html += galleryMonthGroupsHtml(byCountry.get(galleryCountry) || []);
  }
  container.innerHTML = html;

  if (galleryLeaf) {
    const backBtn = container.querySelector(".gallery-back-btn");
    if (backBtn) {
      backBtn.addEventListener("click", () => {
        galleryLeaf = null;
        renderGallery(leavesData);
      });
    }
    document.getElementById("gallery-view-on-map-btn").addEventListener("click", () => {
      const [lng, lat] = galleryLeaf.geometry.coordinates;
      document.getElementById("gallery-panel").classList.add("hidden");
      map.flyTo({ center: [lng, lat], zoom: 15 });
    });
    return;
  }

  container.querySelectorAll(".gallery-country-row").forEach((row) => {
    row.addEventListener("click", () => {
      galleryCountry = row.dataset.country;
      renderGallery(leavesData);
    });
  });

  const backBtn = container.querySelector(".gallery-back-btn");
  if (backBtn) {
    backBtn.addEventListener("click", () => {
      galleryCountry = null;
      renderGallery(leavesData);
    });
  }

  container.querySelectorAll(".gallery-row").forEach((row) => {
    row.addEventListener("click", () => {
      const feature = leavesData.features.find((f) => f.properties.id === row.dataset.id);
      if (feature) {
        galleryLeaf = feature;
        renderGallery(leavesData);
      }
    });
  });
}

// ---------- Tree of life: the evolutionary tree of the species collected so far ----------
// The structure comes from GET /api/tree (backend/tree.py): the Open Tree of Life
// phylogeny connecting the species, or - if that service is unreachable - the
// Linnaean classification as a last resort. Drawn with the tips aligned on the
// right ("today"). For a dated tree (data.dated) every branching point sits at its
// real age, so branch lengths are to scale and a time axis is drawn underneath.
// Otherwise it is a plain cladogram - each branching point placed by how many splits
// sit below it - where only the branching pattern means anything.
const TREE_PALETTE = [
  "#2f7d4f", "#d98e04", "#3b6fb6", "#c4553d", "#8e5bb5",
  "#2aa198", "#b5a300", "#d1669a", "#6b7280", "#7fb069",
];
const TREE_NO_DATA_COLOR = "#cfc7c2";
const TREE_COLOR_OPTIONS = [
  { value: "family", label: "Family" },
  { value: "order", label: "Order" },
  { value: "class", label: "Class" },
  { value: "biome", label: "Biome (ecosystem)" },
  { value: "country", label: "Country" },
  { value: "time", label: "Time added" },
];
const TREE_ROW_H = 26;
const TREE_W = 360; // viewBox width; scales to the panel
const TREE_LEFT = 10;
const TREE_RIGHT = 195; // tips line up here; names start just after

let treeColorBy = "family";
const TREE = { signature: null, data: null };

const treeColorSelect = document.getElementById("tree-color-select");
treeColorSelect.innerHTML = TREE_COLOR_OPTIONS.map((o) => `<option value="${o.value}">${escapeHtml(o.label)}</option>`).join("");
treeColorSelect.value = treeColorBy;
treeColorSelect.addEventListener("change", (e) => {
  treeColorBy = e.target.value;
  renderTree();
});

document.getElementById("tree-btn").addEventListener("click", () => {
  closeAllSheets();
  document.getElementById("tree-detail").classList.add("hidden");
  document.getElementById("tree-normal-view").classList.remove("hidden");
  renderTree();
  document.getElementById("tree-panel").classList.remove("hidden");
});

// accepted species name -> { name, leaves: [feature, ...] }
function speciesGroups() {
  const groups = new Map();
  for (const f of leavesData.features) {
    const sp = f.properties.taxonomy && f.properties.taxonomy.species;
    if (!sp) continue;
    if (!groups.has(sp)) groups.set(sp, { name: sp, leaves: [] });
    groups.get(sp).leaves.push(f);
  }
  return groups;
}

function mostCommon(values) {
  const counts = new Map();
  for (const v of values) if (v) counts.set(v, (counts.get(v) || 0) + 1);
  let best = null;
  let bestN = 0;
  for (const [v, n] of counts) if (n > bestN) { best = v; bestN = n; }
  return best;
}

// The value a species is coloured by. Taxonomic ranks are the same for every leaf
// of a species; for place/time a species can span several, so take the most common.
function treeValueForGroup(group, colorBy) {
  const first = group.leaves[0].properties;
  if (colorBy === "family" || colorBy === "order" || colorBy === "class") return first.taxonomy[colorBy] || "Unknown";
  if (colorBy === "biome") return mostCommon(group.leaves.map((f) => f.properties.ecosystem && f.properties.ecosystem.biome)) || "Unknown";
  if (colorBy === "country") return mostCommon(group.leaves.map((f) => f.properties.country).filter((c) => c !== "unknown")) || "Unknown";
  return mostCommon(group.leaves.map((f) => monthYearLabel(f.properties.created_at ? new Date(f.properties.created_at) : null))) || "Unknown";
}

// Size/shape bookkeeping on the nested tree: height (splits below), tip count, names.
function annotateTree(node) {
  if (!node.children) {
    node.height = 0;
    node.tipCount = 1;
    node.names = node.species || [node.name];
    return node;
  }
  node.children.forEach(annotateTree);
  node.height = 1 + Math.max(...node.children.map((c) => c.height));
  node.tipCount = node.children.reduce((s, c) => s + c.tipCount, 0);
  node.names = node.children.flatMap((c) => c.names);
  return node;
}

// Name an unlabeled branching point by the deepest rank all its species share
// ("Fagaceae" for oak + beech), using the same GBIF taxonomy shown everywhere
// else; fall back to the name the Open Tree gave the clade, if any.
function cladeLabel(node, groups) {
  if (!node.children) return null;
  const taxa = node.names.map((n) => groups.get(n) && groups.get(n).leaves[0].properties.taxonomy).filter(Boolean);
  if (taxa.length === node.names.length && taxa.length > 1) {
    for (const rank of ["genus", "family", "order", "class", "phylum"]) {
      const v = taxa[0][rank];
      if (v && taxa.every((t) => t[rank] === v)) return v;
    }
  }
  return node.name || null;
}

function layoutTree(root, groups, dated) {
  const span = TREE_RIGHT - TREE_LEFT;
  const unit = span / Math.max(root.height, 1);
  let row = 0;
  const tips = [];
  const edges = [];
  const labels = [];

  (function place(node, parentLabel, parent) {
    node.parent = parent || null;
    node.x = dated ? TREE_RIGHT - (node.age / Math.max(root.age, 1e-6)) * span : TREE_RIGHT - node.height * unit;
    if (!node.children) {
      node.y = 14 + row * TREE_ROW_H;
      row++;
      tips.push(node);
      return;
    }
    const label = cladeLabel(node, groups);
    node.children.forEach((c) => place(c, label || parentLabel, node));
    node.y = (node.children[0].y + node.children[node.children.length - 1].y) / 2;
    edges.push({
      x: node.x,
      y1: Math.min(...node.children.map((c) => c.y)),
      y2: Math.max(...node.children.map((c) => c.y)),
      children: node.children.map((c) => ({ x: c.x, y: c.y })),
    });
    // Skip a label that just repeats the one on the branch it sits on.
    if (label && label !== parentLabel) labels.push({ x: node.x, y: node.y, text: label, size: node.tipCount });
  })(root, null, null);

  // Real divergence times bunch many branching points close together, so labels
  // would pile up. Keep the biggest clades' labels and drop any smaller one that
  // would land on top of one already kept.
  const taken = [];
  const kept = labels
    .slice()
    .sort((a, b) => b.size - a.size || a.x - b.x)
    .filter((l) => {
      l.nearTips = TREE_RIGHT - l.x < 48; // near the tips: put it left of the branch point instead
      const w = l.text.length * 4.7 + 4;
      const x0 = l.nearTips ? l.x - 3 - w : l.x + 3;
      const box = { x0, x1: x0 + w, y0: l.y - 13, y1: l.y - 3 };
      if (taken.some((p) => box.x0 < p.x1 && box.x1 > p.x0 && box.y0 < p.y1 && box.y1 > p.y0)) return false;
      taken.push(box);
      return true;
    });

  return { tips, edges, labels: kept, rows: row };
}

function describeTree(data, groups) {
  const nSpecies = groups.size;
  const nLeaves = [...groups.values()].reduce((s, g) => s + g.leaves.length, 0);
  const families = new Map();
  const orders = new Set();
  for (const g of groups.values()) {
    const t = g.leaves[0].properties.taxonomy;
    if (t.family) families.set(t.family, (families.get(t.family) || 0) + g.leaves.length);
    if (t.order) orders.add(t.order);
  }
  const topFamily = [...families.entries()].sort((a, b) => b[1] - a[1])[0];

  const sentences = [];
  if (data.dated) {
    // The deepest and the most recent split, named by the clades on each side.
    const internals = [];
    (function walk(n) { if (n.children) { internals.push(n); n.children.forEach(walk); } })(data.tree);
    const sideName = (node) => cladeLabel(node, groups) || (node.children ? `${node.tipCount} species` : node.name);
    const oldest = data.tree;
    const youngest = internals.reduce((a, b) => (b.age < a.age ? b : a));
    const pair = (n) => `${sideName(n.children[0])} and ${sideName(n.children[1])}`;
    sentences.push(
      `This is a time-scaled evolutionary tree of the ${nSpecies} species in your ${nLeaves} identified mushrooms: branch lengths are drawn to scale, and the axis below counts millions of years before today. ` +
        `The oldest split among them, about ${Math.round(oldest.age)} million years ago, separates ${pair(oldest)}; the most recent, about ${Math.round(youngest.age)} million years ago, is between ${pair(youngest)}.`
    );
    sentences.push(
      "Treat the dates as approximate: they come from the Agaricomycetes megaphylogeny of Varga et al. (2019), different studies disagree by tens of millions of years, and fungal dating rests on very few fossils."
    );
    const approxNames = Object.keys(data.approx || {});
    if (approxNames.length) {
      sentences.push(
        `* ${approxNames.length === 1 ? "marks a species that isn't" : "marks species that aren't"} in that tree (${approxNames.join(", ")}) - ${approxNames.length === 1 ? "it is" : "they are"} placed at the root of ${approxNames.length === 1 ? "its" : "their"} genus or family, so the exact position inside it is unknown.`
      );
    }
  } else if (data.source === "otl") {
    sentences.push(
      `This is an evolutionary tree of the ${nSpecies} species in your ${nLeaves} identified mushrooms, taken from the Open Tree of Life — a tree assembled from published studies. Species that sit closer together share a more recent common ancestor; only the branching pattern is meaningful, not the branch lengths.`
    );
  } else {
    sentences.push(
      `The Open Tree of Life couldn't be reached just now, so this shows the Linnaean classification of your ${nSpecies} species instead (kingdom → phylum → class → order → family → genus). It groups related fungi correctly, but it is a classification, not a true evolutionary tree.`
    );
  }
  if (families.size) {
    sentences.push(
      `They span ${families.size} ${families.size === 1 ? "family" : "families"} in ${orders.size} ${orders.size === 1 ? "order" : "orders"}` +
        (topFamily && families.size > 1 ? `; the best represented is ${topFamily[0]} (${topFamily[1]} ${topFamily[1] === 1 ? "mushroom" : "mushrooms"}).` : ".")
    );
  }
  if (data.unplaced && data.unplaced.length) {
    const who = data.dated ? "the dated tree (it covers mushroom-forming fungi (Agaricomycetes) only)" : "the Open Tree of Life";
    sentences.push(`${data.unplaced.length} ${data.unplaced.length === 1 ? "species is" : "species are"} left out because ${who} has no place for ${data.unplaced.length === 1 ? "it" : "them"}: ${data.unplaced.join(", ")}.`);
  }
  sentences.push("Use the dropdown above to colour the species by family, order, class, biome, country or time; tap a species to see its mushrooms.");
  return sentences.join(" ");
}

async function renderTree() {
  const plot = document.getElementById("tree-plot");
  const legend = document.getElementById("tree-legend");
  const summary = document.getElementById("tree-summary");
  const selected = document.getElementById("tree-selected");
  selected.innerHTML = "";

  const groups = speciesGroups();
  if (groups.size < 2) {
    plot.innerHTML = "";
    legend.innerHTML = "";
    summary.textContent = "";
    plot.innerHTML = `<p class="gallery-empty">${
      groups.size === 0
        ? "No identified mushrooms yet — add a few, with automatic identification on, to grow a tree."
        : "Only one species so far — add a mushroom of a different species to see how they're related."
    }</p>`;
    return;
  }

  const signature = [...groups.keys()].sort().join("|");
  if (TREE.signature !== signature) {
    plot.innerHTML = '<p class="gallery-empty">Building the tree…</p>';
    legend.innerHTML = "";
    summary.textContent = "";
    try {
      const res = await fetch("/api/tree");
      if (!res.ok) throw new Error(`tree request failed (${res.status})`);
      TREE.data = await res.json();
      TREE.signature = signature;
    } catch (err) {
      console.error(err);
      plot.innerHTML = '<p class="gallery-empty">Couldn\'t build the tree right now — check your connection and reopen this panel.</p>';
      return;
    }
  }
  drawTree(groups);
}

function drawTree(groups) {
  const plot = document.getElementById("tree-plot");
  const legend = document.getElementById("tree-legend");
  const summary = document.getElementById("tree-summary");
  const data = TREE.data;
  if (!data || !data.tree) {
    plot.innerHTML = '<p class="gallery-empty">Not enough species to draw a tree yet.</p>';
    return;
  }

  const root = annotateTree(data.tree);
  const dated = !!data.dated;
  const { tips, edges, labels, rows } = layoutTree(root, groups, dated);
  const AXIS_H = dated ? 38 : 0;
  const height = 14 + rows * TREE_ROW_H + AXIS_H;

  // Colours: one per distinct value, most common first.
  const valueOfTip = new Map();
  const counts = new Map();
  for (const tip of tips) {
    const group = tip.names.map((n) => groups.get(n)).find(Boolean);
    const value = group ? treeValueForGroup(group, treeColorBy) : "Unknown";
    valueOfTip.set(tip, { value, group });
    counts.set(value, (counts.get(value) || 0) + 1);
  }
  const ordered = [...counts.keys()].sort((a, b) => counts.get(b) - counts.get(a));
  const colorOf = new Map(ordered.map((v, i) => [v, v === "Unknown" ? TREE_NO_DATA_COLOR : TREE_PALETTE[i % TREE_PALETTE.length]]));

  // Time axis (dated trees only): ticks every 10/20/25/50/100/200 Myr, whichever
  // gives at most ~6, with faint gridlines running up through the tree.
  let axis = "";
  let grid = "";
  if (dated) {
    const R = Math.max(root.age, 1);
    const step = [10, 20, 25, 50, 100, 200, 500].find((s) => R / s <= 6) || 100;
    const yAxis = 14 + rows * TREE_ROW_H - TREE_ROW_H / 2 + 6;
    const span = TREE_RIGHT - TREE_LEFT;
    for (let t = 0; t <= R; t += step) {
      const x = (TREE_RIGHT - (t / R) * span).toFixed(1);
      grid += `<line class="tree-grid" x1="${x}" y1="4" x2="${x}" y2="${yAxis}" />`;
      axis += `<line class="tree-axis" x1="${x}" y1="${yAxis}" x2="${x}" y2="${yAxis + 4}" /><text x="${x}" y="${yAxis + 14}" class="tree-tick" text-anchor="middle">${t}</text>`;
    }
    axis += `<line class="tree-axis" x1="${TREE_LEFT}" y1="${yAxis}" x2="${TREE_RIGHT}" y2="${yAxis}" />`;
    axis += `<text x="${((TREE_LEFT + TREE_RIGHT) / 2).toFixed(1)}" y="${yAxis + 27}" class="tree-tick tree-axis-caption" text-anchor="middle">million years ago</text>`;
  }

  const lines = edges
    .map(
      (e) =>
        `<line x1="${e.x}" y1="${e.y1}" x2="${e.x}" y2="${e.y2}" class="tree-branch" />` +
        e.children.map((c) => `<line x1="${e.x}" y1="${c.y}" x2="${c.x}" y2="${c.y}" class="tree-branch" />`).join("")
    )
    .join("");
  // A label normally sits just right of its branch point, above the branches. Near
  // the tips that would run into the dots, so those go to the left of the branch
  // point instead (right-aligned, above the branch leading into it) - layoutTree
  // decided which, and dropped the ones that would collide.
  const cladeText = labels
    .map((l) => {
      const x = l.nearTips ? l.x - 3 : l.x + 3;
      return `<text x="${x.toFixed(1)}" y="${(l.y - 4).toFixed(1)}" class="tree-clade"${l.nearTips ? ' text-anchor="end"' : ""}>${escapeHtml(l.text)}</text>`;
    })
    .join("");
  const tipMarks = tips
    .map((tip, i) => {
      const { value, group } = valueOfTip.get(tip);
      const n = group ? group.leaves.length : 0;
      const star = tip.approx ? " *" : "";
      const label = `${escapeHtml(tip.name)}${n > 1 ? ` ×${n}` : ""}${star}${tip.approx ? ` (placed at ${tip.approx} level)` : ""}`;
      return `
      <g class="tree-tip" data-i="${i}">
        <rect x="0" y="${tip.y - TREE_ROW_H / 2}" width="${TREE_W}" height="${TREE_ROW_H}" class="tree-hit" />
        <circle cx="${TREE_RIGHT}" cy="${tip.y}" r="5.5" fill="${colorOf.get(value)}" class="tree-dot" />
        <text x="${TREE_RIGHT + 11}" y="${tip.y + 3.5}" class="tree-name"><tspan font-style="italic">${escapeHtml(tip.name)}</tspan>${n > 1 ? ` ×${n}` : ""}${star}</text>
        <title>${label}</title>
      </g>`;
    })
    .join("");

  plot.innerHTML = `<svg viewBox="0 0 ${TREE_W} ${height}" width="100%" role="img" aria-label="Evolutionary tree of the collected species">${grid}${lines}${cladeText}${tipMarks}${axis}</svg>`;

  legend.innerHTML = ordered
    .map((v) => `<div class="tree-legend-item"><span class="tree-legend-swatch" style="background:${colorOf.get(v)}"></span>${escapeHtml(v)} <span class="meta">(${counts.get(v)})</span></div>`)
    .join("");
  summary.textContent = describeTree(data, groups);

  plot.querySelectorAll(".tree-tip").forEach((g) => {
    g.addEventListener("click", () => {
      const tip = tips[Number(g.dataset.i)];
      plot.querySelectorAll(".tree-dot").forEach((c) => c.classList.remove("selected"));
      g.querySelector(".tree-dot").classList.add("selected");
      showTreeSelection(valueOfTip.get(tip).group, tip.name, tip);
    });
  });
}

// Tapping a species lists its leaves; tapping a leaf opens the full description in
// place (with a Back button) so you never leave the tree.
function showTreeSelection(group, fallbackName, tip) {
  const selected = document.getElementById("tree-selected");
  if (!group) {
    selected.innerHTML = `<div class="tree-selected-head"><div class="sci">${sciNameHtml(fallbackName)}</div><div class="meta">No mushrooms of this species on this device yet — reload to refresh.</div></div>`;
    return;
  }
  const t = group.leaves[0].properties.taxonomy;
  const lineage = ["class", "order", "family"].map((r) => t[r]).filter(Boolean).join(" › ");
  const common = mostCommon(group.leaves.map((f) => f.properties.title).filter((x) => x && x !== "unknown"));
  // For a dated tree: who in your collection is its nearest relative, and how long ago they split.
  let splitLine = "";
  if (TREE.data && TREE.data.dated && tip && tip.parent) {
    const relatives = tip.parent.names.filter((n) => !tip.names.includes(n));
    const shown = relatives.slice(0, 3).map((n) => `<em>${escapeHtml(n)}</em>`).join(", ") + (relatives.length > 3 ? ` +${relatives.length - 3} more` : "");
    splitLine = `<div class="meta">Nearest relatives here: ${shown} — split about ${Math.round(tip.parent.age)} million years ago${tip.approx ? " (approximate: this species is placed only at its " + tip.approx + " level)" : ""}.</div>`;
  }
  selected.innerHTML = `
    <div class="tree-selected-head">
      <div class="title">${escapeHtml(common || group.name)}</div>
      <div class="sci">${sciNameHtml(group.name)}</div>
      <div class="meta">${escapeHtml(lineage)}</div>
      ${splitLine}
    </div>
    ${group.leaves.map(galleryRowHtml).join("")}`;

  selected.querySelectorAll(".gallery-row").forEach((row) => {
    row.addEventListener("click", () => {
      const f = group.leaves.find((x) => x.properties.id === row.dataset.id);
      if (!f) return;
      const normalView = document.getElementById("tree-normal-view");
      const detail = document.getElementById("tree-detail");
      detail.innerHTML = `
        <button type="button" class="gallery-back-btn">‹ Back</button>
        <div class="gallery-detail">${popupHtml(f.properties, f.geometry.coordinates)}</div>`;
      normalView.classList.add("hidden");
      detail.classList.remove("hidden");
      detail.querySelector(".gallery-back-btn").addEventListener("click", () => {
        detail.classList.add("hidden");
        normalView.classList.remove("hidden");
      });
    });
  });
}

// ---------- Locate me ----------
document.getElementById("locate-btn").addEventListener("click", () => {
  if (!navigator.geolocation) {
    showToast("Geolocation isn't available on this device/browser.");
    return;
  }
  navigator.geolocation.getCurrentPosition(
    (pos) => map.flyTo({ center: [pos.coords.longitude, pos.coords.latitude], zoom: 13 }),
    () => showToast("Couldn't get your location. Check location permissions."),
    { enableHighAccuracy: true, timeout: 10000 }
  );
});

// ---------- Toast ----------
let toastTimer = null;
function showToast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 3200);
}

// ---------- Add leaf flow ----------
const addPanel = document.getElementById("add-panel");
const stepLocation = document.getElementById("step-location");
const leafForm = document.getElementById("leaf-form");
const locationStatus = document.getElementById("location-status");
const coordsReadout = document.getElementById("coords-readout");
const pickHint = document.getElementById("pick-hint");
const passphraseRow = document.getElementById("passphrase-row");
const passphraseInput = document.getElementById("passphrase-input");

// Holds whichever photo was most recently picked, from either the camera
// input or the gallery input - everything downstream (preview, AI identification,
// submit) reads this instead of reaching into a specific <input>'s .files.
let currentPhotoFile = null;

// The location always has to be pinned down before the camera opens, so it's
// never ambiguous which coordinate a photo will be attached to. A map
// long-press already IS that pin (presetCoords), so it skips
// straight to the camera; the "+" button doesn't know a location yet, so it
// shows the same "Where did you find it?" step first, then opens the camera
// itself once that's answered.
function openAddPanel(presetCoords) {
  closeAllSheets();
  pendingCoords = presetCoords || null;
  document.getElementById("photo-step").classList.add("hidden");
  stepLocation.classList.add("hidden");
  leafForm.classList.add("hidden");
  locationStatus.textContent = "";
  leafForm.reset();
  document.getElementById("photo-input").value = "";
  currentPhotoFile = null;
  document.getElementById("photo-preview").classList.add("hidden");
  document.getElementById("submit-status").textContent = "";
  const saved = localStorage.getItem(PASSPHRASE_KEY);
  passphraseRow.classList.toggle("hidden", !!saved);
  clearIdentification();
  document.getElementById("leaf-details").classList.add("hidden");
  refreshAiCredits();
  addPanel.classList.remove("hidden");

  if (pendingCoords) {
    openCamera();
  } else {
    stepLocation.classList.remove("hidden");
  }
}

function openCamera() {
  document.getElementById("photo-step").classList.remove("hidden");
  document.getElementById("photo-input").click();
}

// Called once a location is known (GPS, map tap, or a preset from
// long-press) - opens the camera if a photo isn't picked yet.
function locationReady() {
  stepLocation.classList.add("hidden");
  if (currentPhotoFile) {
    proceedIfReady();
  } else {
    openCamera();
  }
}

document.getElementById("add-btn").addEventListener("click", () => openAddPanel());

document.getElementById("retake-photo-btn").addEventListener("click", () => {
  document.getElementById("photo-input").click();
});

document.querySelectorAll(".close-sheet").forEach((btn) =>
  btn.addEventListener("click", (e) => {
    e.target.closest(".sheet").classList.add("hidden");
    if (pickingOnMap) cancelPicking();
  })
);

// ---------- Swipe down to dismiss a sheet ----------
// Works from anywhere in the sheet, not just the handle - but a drag only
// actually "arms" once the user is clearly pulling down (past a small
// threshold) while already scrolled to the very top of that sheet's own
// content. Until then this does nothing at all, so normal scrolling of a
// long list and normal taps on rows/buttons/circles/selects are completely
// unaffected - only a deliberate pull-down-from-the-top engages it.
const SHEET_DRAG_CLOSE_PX = 90;
const SHEET_DRAG_ARM_PX = 10;

function wireSheetDrag(sheet) {
  let startY = null;
  let dragging = false;

  sheet.addEventListener("pointerdown", (e) => {
    startY = e.clientY;
    dragging = false;
  });

  sheet.addEventListener("pointermove", (e) => {
    if (startY === null) return;
    const delta = e.clientY - startY;
    if (!dragging) {
      if (sheet.scrollTop > 0 || delta < SHEET_DRAG_ARM_PX) return;
      dragging = true;
      sheet.style.transition = "none"; // follow the finger 1:1, no easing lag
      sheet.setPointerCapture(e.pointerId);
    }
    sheet.style.transform = `translateY(${Math.max(0, delta)}px)`;
  });

  const endDrag = (e) => {
    if (startY === null) return;
    const delta = Math.max(0, e.clientY - startY);
    startY = null;
    if (!dragging) return;
    dragging = false;
    sheet.style.transition = ""; // restore the normal eased transition
    if (delta > SHEET_DRAG_CLOSE_PX) {
      // Animate on from wherever the drag left off (not a snap back to 0
      // first) - only swap in the "hidden" class, which takes over the
      // resting position for next time, once that animation has finished.
      sheet.style.transform = "translateY(110%)";
      setTimeout(() => {
        sheet.classList.add("hidden");
        sheet.style.transform = "";
      }, 250);
      if (pickingOnMap) cancelPicking();
    } else {
      sheet.style.transform = ""; // didn't drag far enough - ease back open
    }
  };
  sheet.addEventListener("pointerup", endDrag);
  sheet.addEventListener("pointercancel", endDrag);
}

document.querySelectorAll(".sheet").forEach(wireSheetDrag);

document.getElementById("use-gps-btn").addEventListener("click", () => {
  if (!navigator.geolocation) {
    locationStatus.textContent = "Geolocation isn't available on this device/browser.";
    return;
  }
  locationStatus.textContent = "Getting your location…";
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      pendingCoords = { lat: pos.coords.latitude, lon: pos.coords.longitude };
      locationReady();
    },
    () => {
      locationStatus.textContent = "Couldn't get your location. Check location permissions, or choose on the map instead.";
    },
    { enableHighAccuracy: true, timeout: 10000 }
  );
});

document.getElementById("pick-map-btn").addEventListener("click", () => {
  addPanel.classList.add("hidden");
  pickHint.classList.remove("hidden");
  pickingOnMap = true;
  map.getCanvas().style.cursor = "crosshair";
  map.on("click", onMapPick);
});

function onMapPick(e) {
  pendingCoords = { lat: e.lngLat.lat, lon: e.lngLat.lng };
  cancelPicking();
  addPanel.classList.remove("hidden");
  locationReady();
}

function cancelPicking() {
  pickingOnMap = false;
  pickHint.classList.add("hidden");
  map.getCanvas().style.cursor = "";
  map.off("click", onMapPick);
}

// Called whenever either the photo or the location just became available;
// once both are, move on to the details step (and kick off AI identification).
function proceedIfReady() {
  const photoFile = currentPhotoFile;
  if (!photoFile || !pendingCoords) return;
  stepLocation.classList.add("hidden");
  leafForm.classList.remove("hidden");
  coordsReadout.textContent = `📍 ${pendingCoords.lat.toFixed(5)}, ${pendingCoords.lon.toFixed(5)}`;
  const toggle = document.getElementById("toggle-ai");
  if (toggle.checked && !toggle.disabled) {
    maybeIdentify();
  } else {
    revealDetails();
  }
}

function handlePhotoFile(file) {
  if (!file) return; // user cancelled the native picker - leave things as they were
  currentPhotoFile = file;
  const preview = document.getElementById("photo-preview");
  const reader = new FileReader();
  reader.onload = () => {
    preview.src = reader.result;
    preview.classList.remove("hidden");
  };
  reader.readAsDataURL(file);
  clearIdentification();

  if (pendingCoords) {
    proceedIfReady();
  } else {
    stepLocation.classList.remove("hidden");
  }
}

document.getElementById("photo-input").addEventListener("change", (e) => handlePhotoFile(e.target.files[0]));

function revealDetails() {
  document.getElementById("leaf-details").classList.remove("hidden");
}

// ---------- Automatic leaf identification ----------
let lastIdentification = null; // {confidence, alternatives, features} for the leaf being added
let aiSuggestedName = null; // the species the AI proposed - to tell if the user changed it
let identifying = false;

async function refreshAiCredits() {
  const toggle = document.getElementById("toggle-ai");
  const hint = document.getElementById("ai-credits-hint");
  try {
    const res = await fetch("/api/ai-credits");
    const credits = await res.json();
    applyCreditsToUi(credits);
  } catch (err) {
    toggle.disabled = true;
    hint.textContent = "";
  }
}

function applyCreditsToUi(credits) {
  const toggle = document.getElementById("toggle-ai");
  const hint = document.getElementById("ai-credits-hint");
  if (!credits.configured) {
    document.getElementById("ai-toggle-row").classList.add("hidden");
    hint.textContent = "";
    return;
  }
  document.getElementById("ai-toggle-row").classList.remove("hidden");
  if (credits.remaining <= 0) {
    toggle.checked = false;
    toggle.disabled = true;
    hint.textContent = "No AI credits left today — try again tomorrow.";
  } else {
    toggle.disabled = false;
    hint.textContent = `${credits.remaining} AI ${credits.remaining === 1 ? "identification" : "identifications"} left today.`;
  }
}

document.getElementById("toggle-ai").addEventListener("change", () => maybeIdentify());

function maybeIdentify() {
  const toggle = document.getElementById("toggle-ai");
  if (!toggle.checked || !currentPhotoFile || !pendingCoords || identifying) return;
  identifyLeaf(currentPhotoFile);
}

function showRetryAi() {
  document.getElementById("retry-ai-btn").classList.remove("hidden");
}

function hideRetryAi() {
  document.getElementById("retry-ai-btn").classList.add("hidden");
}

document.getElementById("retry-ai-btn").addEventListener("click", () => {
  if (currentPhotoFile && !identifying) identifyLeaf(currentPhotoFile);
});

function clearIdentification() {
  lastIdentification = null;
  aiSuggestedName = null;
  document.getElementById("identification-preview").innerHTML = "";
  document.getElementById("ai-status").textContent = "";
  hideRetryAi();
}

// What the AI came back with, shown before saving: how sure it is, the runners-up,
// the resolved classification and the ecosystem. Every candidate - the AI's own first
// choice included - is a chip, so switching is always reversible, and a switch sets the
// common AND scientific name together (they can't drift apart).
function renderIdentificationPreview(body) {
  const preview = document.getElementById("identification-preview");
  const chart = identificationChartHtml({
    scientific_name: body.scientific_name,
    identification: { confidence: body.confidence, alternatives: body.alternatives },
  });
  const top = { scientific_name: body.scientific_name, common_name: body.title };
  const options = [top, ...(body.alternatives || [])];
  let current = top;

  const draw = () => {
    const chips = options
      .map((o, i) => ({ o, i }))
      .filter(({ o }) => o !== current)
      .map(({ o, i }) => `<button type="button" class="alt-chip" data-i="${i}">Use <em>${escapeHtml(o.scientific_name)}</em></button>`)
      .join("");
    // The classification shown belongs to the AI's first choice only; for any other
    // pick it is looked up fresh when the leaf is saved.
    const safety = body.safety && {
      ...body.safety,
      edibility: current === top ? body.safety.edibility : current.edibility ?? 0,
      toxicity: current === top ? body.safety.toxicity : current.toxicity ?? 5,
    };
    const rel = body.reliability && (current === top ? body.reliability : (body.reliability.alternatives || [])[options.indexOf(current) - 1]);
    preview.innerHTML = `${safetyHtml(safety, false, rel)}${chart}${chips ? `<div class="alt-chips">${chips}</div>` : ""}${current === top ? classificationHtml(body.taxonomy) : ""}${ecosystemHtml(body.ecosystem)}`;
    preview.querySelectorAll(".alt-chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        current = options[Number(chip.dataset.i)];
        document.getElementById("scientific-input").value = current.scientific_name === "unknown" ? "" : current.scientific_name;
        document.getElementById("title-input").value = current.common_name === "unknown" ? "" : current.common_name;
        document.getElementById("ai-status").textContent =
          current === top
            ? "Back to the AI's first choice."
            : `Switched to ${current.scientific_name} — its classification is looked up when you save.`;
        draw();
      });
    });
  };
  draw();
}

async function identifyLeaf(photoFile) {
  identifying = true;
  const status = document.getElementById("ai-status");
  status.textContent = "🤖 Identifying…";
  hideRetryAi();

  const form = new FormData();
  form.append("lat", pendingCoords.lat);
  form.append("lon", pendingCoords.lon);
  form.append("photo", photoFile);

  try {
    const res = await fetch("/api/identify", { method: "POST", body: form });
    const body = await res.json();
    if (!res.ok) {
      status.textContent = (body.detail || "AI identification failed.") + " Please fill in the details yourself, or retry.";
      showRetryAi();
      refreshAiCredits();
      revealDetails();
      return;
    }
    if (body.credits) applyCreditsToUi(body.credits);
    if (!body.is_fungus) {
      status.textContent = "That doesn't look like a mushroom or other fungus — retake the photo, or fill in the details yourself.";
      revealDetails();
      return;
    }
    document.getElementById("title-input").value = body.title === "unknown" ? "" : body.title;
    document.getElementById("scientific-input").value = body.scientific_name === "unknown" ? "" : body.scientific_name;
    document.getElementById("description-input").value = body.description === "unknown" ? "" : body.description;
    aiSuggestedName = body.scientific_name;
    lastIdentification = { confidence: body.confidence, alternatives: body.alternatives, features: body.features, safety: body.safety, reliability: body.reliability };
    renderIdentificationPreview(body);
    status.textContent =
      body.confidence < 0.5
        ? "Not very sure about this one — please double-check the species."
        : "Filled in from the AI identification — feel free to edit anything.";
    revealDetails();
  } catch (err) {
    status.textContent = "Network error during identification. Please fill in the details yourself, or retry.";
    showRetryAi();
    revealDetails();
  } finally {
    identifying = false;
  }
}

leafForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!pendingCoords) return;

  const submitBtn = document.getElementById("submit-btn");
  const submitStatus = document.getElementById("submit-status");
  const photoFile = currentPhotoFile;
  if (!photoFile) {
    submitStatus.textContent = "Please add a photo.";
    return;
  }

  const savedPass = localStorage.getItem(PASSPHRASE_KEY);
  const typedPass = passphraseInput.value;
  const passphrase = savedPass || typedPass;
  if (!passphrase) {
    submitStatus.textContent = "Please enter the shared passphrase.";
    return;
  }

  const scientificName = document.getElementById("scientific-input").value.trim();
  // The AI's confidence and runners-up only describe the species it proposed - if
  // the name was changed, they'd be describing a different species, so drop them.
  const keepIdentification = lastIdentification && scientificName && scientificName === aiSuggestedName;

  const form = new FormData();
  form.append("lat", pendingCoords.lat);
  form.append("lon", pendingCoords.lon);
  form.append("title", document.getElementById("title-input").value);
  form.append("scientific_name", scientificName);
  form.append("description", document.getElementById("description-input").value);
  form.append("identification", keepIdentification ? JSON.stringify(lastIdentification) : "");
  form.append("passphrase", passphrase);
  form.append("photo", photoFile);

  submitBtn.disabled = true;
  submitStatus.textContent = "Uploading…";

  try {
    const res = await fetch("/api/leaves", { method: "POST", body: form });
    if (res.status === 403) {
      localStorage.removeItem(PASSPHRASE_KEY);
      passphraseRow.classList.remove("hidden");
      submitStatus.textContent = "Wrong passphrase — try again.";
      submitBtn.disabled = false;
      return;
    }
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      submitStatus.textContent = body.detail || "Something went wrong. Please try again.";
      submitBtn.disabled = false;
      return;
    }
    const feature = await res.json();
    if (!savedPass && typedPass) localStorage.setItem(PASSPHRASE_KEY, typedPass);

    leavesData.features.unshift(feature);
    renderLeafMarkers(leavesData);

    addPanel.classList.add("hidden");
    showToast("Mushroom added 🍄");
  } catch (err) {
    submitStatus.textContent = "Network error. Please try again.";
  } finally {
    submitBtn.disabled = false;
  }
});
