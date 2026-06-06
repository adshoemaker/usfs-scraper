# app.py
# ---------------------------------------------------------------
# Web interface for browsing and searching USFS NEPA projects.
# Reads from projects.json, updated by GitHub Actions.
#
# To run locally:
#   python3 app.py
# Then open: http://localhost:5000
# ---------------------------------------------------------------

import json
import os
import datetime
import base64
import urllib.request
import urllib.error
import urllib.parse
from flask import Flask, request, render_template_string, session, redirect, url_for

app = Flask(__name__, static_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static'))
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-in-production")

STATUS_COLORS = {
    "Developing Proposal": "#9b72d8",
    "In Progress":         "#4a90d9",
    "On Hold":             "#e08848",
    "Completed":           "#5aaa48",
}

ANALYSIS_COLORS = {
    "Categorical Exclusion":          "#cc1111",
    "Environmental Assessment":       "#c46a30",
    "Environmental Impact Statement": "#2d7a1f",
    "Unknown":                        "#999",
}

FORESTS = [
    {"name": "Mt. Baker-Snoqualmie National Forest", "code": "mbs",              "state": "WA"},
    {"name": "Olympic National Forest",              "code": "olympic",           "state": "WA"},
    {"name": "Okanogan-Wenatchee National Forest",   "code": "okanogan-wenatchee","state": "WA"},
    {"name": "Gifford Pinchot National Forest",      "code": "giffordpinchot",   "state": "WA"},
    {"name": "Colville National Forest",             "code": "colville",          "state": "WA"},
    {"name": "Rogue River-Siskiyou National Forest", "code": "rogue-siskiyou",   "state": "CA+OR"},
    {"name": "Wallowa-Whitman National Forest",      "code": "wallowa-whitman",  "state": "OR"},
    {"name": "Fremont-Winema National Forest",       "code": "fremont-winema",   "state": "OR"},
    {"name": "Shasta-Trinity National Forest",       "code": "shasta-trinity",   "state": "CA"},
    {"name": "Inyo National Forest",                 "code": "inyo",              "state": "CA"},
    {"name": "Los Padres National Forest",           "code": "lospadres",         "state": "CA"},
    {"name": "Klamath National Forest",              "code": "klamath",           "state": "CA+OR"},
    {"name": "Tongass National Forest",              "code": "tongass",           "state": "AK"},
]

# Column order for forest summary
STATE_COLUMNS = ["CA", "CA+OR", "OR", "OR+WA", "WA", "AK"]

# Map forest code -> state for color lookup
FOREST_STATE_MAP = {f["code"]: f["state"] for f in FORESTS}

# Map forest code -> abbreviation used in multi-forest project names
FOREST_CODE_TO_ABBREV = {
    "mbs":                "MBS",
    "olympic":            "ONF",
    "okanogan-wenatchee": "Okan-Wen",
    "giffordpinchot":     "GPNF",
    "colville":           "Colville",
    "rogue-siskiyou":     "RRS",
    "wallowa-whitman":    "Wallowa-Whitman",
    "fremont-winema":     "Fremont-Winema",
    "shasta-trinity":     "Shasta-Trinity",
    "inyo":               "Inyo",
    "lospadres":          "Los Padres",
    "klamath":            "Klamath",
    "tongass":            "Tongass",
}

# Colors for each state column
STATE_COLORS = {
    "CA":    {"pill": "#cc3333", "label": "#8b1a1a"},
    "CA+OR": {"pill": "#c96a00", "label": "#7a3e00"},
    "OR":    {"pill": "#d4bc00", "label": "#6b5f00"},
    "OR+WA": {"pill": "#7a9a2f", "label": "#445a18"},
    "WA":    {"pill": "#2d7a1f", "label": "#1a4f0f"},
    "AK":    {"pill": "#5b4fa8", "label": "#352d6e"},
}


DATE_RANGES = [
    ("7",  "Last 7 days"),
    ("30", "Last 30 days"),
    ("90", "Last 90 days"),
]

STATUS_SORT_ORDER = {
    "Developing Proposal": 0,
    "In Progress":         1,
    "On Hold":             2,
    "Completed":           3,
}

CATEGORY_SORT_ORDER = {
    "extractive":  0,
    "mixed":       1,
    "restorative": 2,
}

IMPACT_SORT_ORDER = {
    "extractive":  0,
    "mixed":       1,
    "restorative": 2,
    None:          3,
}

ANALYSIS_SORT_ORDER = {
    "Environmental Impact Statement": 0,
    "Environmental Assessment":       1,
    "Categorical Exclusion":          2,
}

# These match against the purpose tag field (pipe-separated values from USFS)
EXTRACTIVE_KEYWORDS = [
    "forest products",
    "fuels management",
    "grazing management",
    "minerals and geology",
    "vegetation management (other than forest products)",
    "land management planning",
]

RESTORATIVE_KEYWORDS = [
    "climate change adaptation",
    "watershed management",
    "wildlife, fish, rare plants",
    "special area management",
]

# Road management appears in both lists so it always resolves to mixed
MIXED_KEYWORDS = [
    "road management",
]


def has_thinning_badge(project):
    purpose = (project.get("purpose") or "").lower()
    return "forest products" in purpose or "fuels management" in purpose


def has_wildfire_badge(project):
    purpose = (project.get("purpose") or "").lower()
    return "fuels management" in purpose or "vegetation management" in purpose


def classify_project(project):
    # Match against individual purpose tags (pipe-separated)
    purpose_tags = [
        t.strip().lower()
        for t in (project.get("purpose") or "").split("|")
        if t.strip()
    ]
    # Road management always forces mixed regardless of other tags
    if any(kw in purpose_tags for kw in MIXED_KEYWORDS):
        return "mixed"
    has_extractive  = any(kw in purpose_tags for kw in EXTRACTIVE_KEYWORDS)
    has_restorative = any(kw in purpose_tags for kw in RESTORATIVE_KEYWORDS)
    if has_extractive and has_restorative:
        return "mixed"
    elif has_extractive:
        return "extractive"
    elif has_restorative:
        return "restorative"
    return None


def load_projects():
    json_path = os.path.join(os.path.dirname(__file__), "projects.json")
    if not os.path.exists(json_path):
        return [], "never"
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    scraped_at = data.get("scraped_at", "")[:10]
    projects = data.get("projects", [])
    for p in projects:
        p["category"] = classify_project(p)
        if p.get("analysis_type") == "Decision Memo":
            p["analysis_type"] = "Environmental Assessment"
        # Extract key milestone dates
        scoping = decision = implementation = ""
        for m in p.get("milestones", []):
            name = m.get("milestone", "").lower()
            date = m.get("date", "")
            if "scoping" in name and "start" in name:
                scoping = date
            elif "decision" in name and not decision:
                decision = date
            elif "implementation" in name and not implementation:
                implementation = date
        p["scoping_start"]    = scoping
        p["decision_date"]    = decision
        p["implementation_date"] = implementation
    return projects, scraped_at


def filter_projects(projects, search="", forest_code="", status="",
                    days="", categories=None, sort="", sort2="", recent_cutoff=""):
    if categories is None: categories = []
    results = []
    search_lower = search.lower()
    cutoff = None
    if days:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - \
                 datetime.timedelta(days=int(days))

    for p in projects:
        if search and search_lower not in p.get("project_name", "").lower() \
                  and search_lower not in p.get("description", "").lower():
            continue
        if forest_code and p.get("forest_code") != forest_code:
            if not p.get("is_multi_forest"):
                continue
            # For multi-forest projects, check if the forest name or code appears
            fn = p.get("forest_name", "")
            abbrev = FOREST_CODE_TO_ABBREV.get(forest_code, '')
            if not abbrev or abbrev not in fn:
                continue
        if status and p.get("status") != status:
            continue
        if categories:
            IMPACT_CATS = {"extractive", "mixed", "restorative", "unclassified"}
            UNIQUE_CATS = {"taking_comments", "active", "newly_added"}
            selected_impact = [c for c in categories if c in IMPACT_CATS]
            selected_unique = [c for c in categories if c in UNIQUE_CATS]

            # Impact: OR logic — must match at least one if any selected
            if selected_impact:
                impact_match = False
                for cat in selected_impact:
                    if cat == "unclassified" and not p.get("category"):
                        impact_match = True; break
                    elif p.get("category") == cat:
                        impact_match = True; break
                if not impact_match:
                    continue

            # Unique: AND logic — must match all selected
            for cat in selected_unique:
                if cat == "taking_comments" and not p.get("accepting_comments"):
                    continue  # outer loop handles skip
                if cat == "active" and p.get("status") not in ("In Progress", "Developing Proposal"):
                    break
                if cat == "newly_added" and not (p.get("first_seen", "")[:10] >= recent_cutoff):
                    break
            else:
                pass  # all unique filters passed

            # Re-check unique filters cleanly
            unique_match = True
            for cat in selected_unique:
                if cat == "taking_comments" and not p.get("accepting_comments"):
                    unique_match = False; break
                if cat == "active" and p.get("status") not in ("In Progress", "Developing Proposal"):
                    unique_match = False; break
                if cat == "newly_added" and not (p.get("first_seen", "")[:10] >= recent_cutoff):
                    unique_match = False; break
            if not unique_match:
                continue
        if cutoff:
            first_seen_str = p.get("first_seen", "")
            if not first_seen_str:
                continue
            try:
                fs = first_seen_str.replace("Z", "+00:00")
                first_seen_dt = datetime.datetime.fromisoformat(fs)
                if first_seen_dt < cutoff:
                    continue
            except ValueError:
                continue
        results.append(p)

    if sort == "cara_newest":
        results.sort(key=lambda p: (0 if p.get("accepting_comments") else 1, p.get("first_seen", "") + "z" if not p.get("accepting_comments") else ""), reverse=False)
        results.sort(key=lambda p: (0 if p.get("accepting_comments") else 1,))
        # stable secondary: newest within each group
        from operator import itemgetter
        cara = sorted([p for p in results if p.get("accepting_comments")], key=lambda p: p.get("first_seen",""), reverse=True)
        rest = sorted([p for p in results if not p.get("accepting_comments")], key=lambda p: p.get("first_seen",""), reverse=True)
        results[:] = cara + rest
    elif sort == "newest":
        results.sort(key=lambda p: p.get("first_seen", ""), reverse=True)
    elif sort == "oldest":
        results.sort(key=lambda p: p.get("first_seen", ""))
    elif sort == "name":
        results.sort(key=lambda p: p.get("project_name", "").lower())
    elif sort == "forest":
        results.sort(key=lambda p: p.get("forest_name", "").lower())
    elif sort == "analysis":
        results.sort(key=lambda p: ANALYSIS_SORT_ORDER.get(p.get("analysis_type", ""), 99))
    elif sort == "status":
        results.sort(key=lambda p: (
            STATUS_SORT_ORDER.get(p.get("status", ""), 99),
            CATEGORY_SORT_ORDER.get(p.get("category", ""), 3),
        ))

    def date_key(field, p, reverse=False):
        import re
        d = (p.get(field) or "").replace("\xa0", " ").replace("(Estimated)", "").strip()
        # Try MM/DD/YYYY
        m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', d)
        if m:
            return f"{m.group(3)}/{m.group(1).zfill(2)}/{m.group(2).zfill(2)}"
        # Try MM/YYYY — assign day 01
        m = re.match(r'(\d{1,2})/(\d{4})', d)
        if m:
            return f"{m.group(2)}/{m.group(1).zfill(2)}/01"
        return "0000/00/00" if reverse else "9999/99/99"

    if sort == "scoping_newest":
        results.sort(key=lambda p: date_key("scoping_start", p, True), reverse=True)
    elif sort == "scoping_oldest":
        results.sort(key=lambda p: date_key("scoping_start", p))
    elif sort == "decision_newest":
        results.sort(key=lambda p: date_key("decision_date", p, True), reverse=True)
    elif sort == "decision_oldest":
        results.sort(key=lambda p: date_key("decision_date", p))
    elif sort == "implementation_newest":
        results.sort(key=lambda p: date_key("implementation_date", p, True), reverse=True)
    elif sort == "implementation_oldest":
        results.sort(key=lambda p: date_key("implementation_date", p))
    elif sort == "impact":
        results.sort(key=lambda p: IMPACT_SORT_ORDER.get(p.get("category"), 3))

    # Secondary sort
    if sort2:
        if sort2 == "newest":
            results.sort(key=lambda p: p.get("first_seen", ""), reverse=True)
        elif sort2 == "oldest":
            results.sort(key=lambda p: p.get("first_seen", ""))
        elif sort2 == "name":
            results.sort(key=lambda p: p.get("project_name", "").lower())
        elif sort2 == "forest":
            results.sort(key=lambda p: p.get("forest_name", "").lower())
        elif sort2 == "status":
            results.sort(key=lambda p: STATUS_SORT_ORDER.get(p.get("status", ""), 99))
        elif sort2 == "impact":
            results.sort(key=lambda p: IMPACT_SORT_ORDER.get(p.get("category"), 3))
        elif sort2 == "analysis":
            results.sort(key=lambda p: ANALYSIS_SORT_ORDER.get(p.get("analysis_type", ""), 99))
        elif sort2 == "scoping_newest":
            results.sort(key=lambda p: date_key("scoping_start", p, True), reverse=True)
        elif sort2 == "scoping_oldest":
            results.sort(key=lambda p: date_key("scoping_start", p))
        elif sort2 == "decision_newest":
            results.sort(key=lambda p: date_key("decision_date", p, True), reverse=True)
        elif sort2 == "decision_oldest":
            results.sort(key=lambda p: date_key("decision_date", p))
        elif sort2 == "implementation_newest":
            results.sort(key=lambda p: date_key("implementation_date", p, True), reverse=True)
        elif sort2 == "implementation_oldest":
            results.sort(key=lambda p: date_key("implementation_date", p))

    return results


PAGE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LFDC NEPA Tracker</title>
    <link rel="icon" type="image/png" href="/static/LFDC_Logo.png">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Lexend:wght@400;500;600;700&family=Outfit:wght@400;500;600&family=Poppins:wght@300;400;500;600&display=swap" rel="stylesheet">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }

        :root {
            --bg:          #e8ede3;
            --bg2:         #ffffff;
            --bg3:         #f2f5ee;
            --border:      #d0d0c8;
            --border2:     #b8b8b0;
            --text:        #111111;
            --text-muted:  #444444;
            --text-dim:    #777777;
            --accent:      #2d7a1f;
            --link:        #1a4fa0;
            --red:         #cc1111;
            --green:       #2d7a1f;
            --orange:      #c46a30;
            --purple:      #6d3eb0;
            --blue:        #1a4fa0;
        }

        body {
            font-family: 'Poppins', sans-serif;
            background: var(--bg);
            color: var(--text);
            font-size: 14px;
            line-height: 1.6;
        }

        /* ── Header ── */
        .top-search-bar {
            background: var(--bg3);
            border-bottom: 1px solid var(--border);
            padding: 8px 20px;
        }

        .top-search-inner {
            max-width: 1150px;
            margin: 0 auto;
            display: flex;
            justify-content: flex-end;
        }

        .header-search {
            display: flex;
            align-items: center;
            gap: 0;
        }

        .header-search input[type="text"] {
            flex: 1;
        }

        .header-search input[type="text"] {
            padding: 7px 14px;
            border: 1px solid #ccc;
            border-radius: 0;
            font-family: 'Poppins', sans-serif;
            font-size: 0.88rem;
            background: white;
            color: #1a1a1a;
            outline: none;
        }

        .header-search input[type="text"]::placeholder { color: #aaa; }
        .header-search input[type="text"]:focus { border-color: #888; }

        .header-search button {
            padding: 7px 18px;
            background: #e05a2b;
            color: white;
            border: none;
            border-radius: 0;
            font-family: 'Poppins', sans-serif;
            font-size: 0.88rem;
            font-weight: 400;
            cursor: pointer;
            white-space: nowrap;
        }

        .header-search button:hover { background: #c44d22; }

        /* ── Forest summary bar ── */
        .forest-summary {
            background: #f7f7f0;
            border-bottom: 1px solid var(--border);
            padding: 10px 20px;
        }

        .forest-summary-inner {
            max-width: 1150px;
            margin: 0 auto;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }

        .forest-cols-row {
            display: flex;
            gap: 0;
            justify-content: center;
            width: 100%;
        }

        .forest-totals-row {
            display: flex;
            flex-direction: row;
            align-items: center;
            justify-content: flex-end;
            gap: 12px;
            width: 100%;
        }

        .forest-reset-btn {
            display: inline-block;
            padding: 5px 12px;
            background: #e05a2b;
            color: white;
            font-family: 'Poppins', sans-serif;
            font-size: 0.62rem;
            font-weight: 400;
            border: none;
            cursor: pointer;
            text-decoration: none;
            white-space: nowrap;
        }

        .forest-reset-btn:hover {
            background: #c44d22;
        }

        .forest-col {
            display: flex;
            flex-direction: column;
            gap: 4px;
            flex: 1;
            min-width: 0;
            padding: 0 8px;
        }

        .forest-col-label {
            font-size: 0.6rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.6px;
            color: var(--text-dim);
            margin-bottom: 2px;
        }

        .forest-pill {
            transition: opacity 0.15s, box-shadow 0.15s;
        }

        .forest-pill.pill-selected {
            box-shadow: 0 0 0 2px white, 0 0 0 3px currentColor;
        }

        .forest-pill-link {
            text-decoration: none;
        }

        /* Original forest-pill styles below */
        .forest-pill {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 5px;
            background: var(--accent);
            border-radius: 20px;
            padding: 3px 10px 3px 10px;
            font-size: 0.7rem;
            font-weight: 400;
            color: white;
            white-space: nowrap;
            width: 100%;
            box-sizing: border-box;
        }

        .forest-pill-count {
            background: rgba(255,255,255,0.25);
            border-radius: 10px;
            padding: 0 5px;
            font-size: 0.62rem;
            font-weight: 700;
            color: white;
        }

        .summary-totals {
            color: var(--text-muted);
            font-size: 0.72rem;
            text-align: right;
        }

        .summary-totals strong {
            color: var(--text);
            font-weight: 700;
        }

        /* ── Search section ── */
        .search-section {
            border-bottom: 1px solid var(--border);
            padding: 0;
            position: relative;
            height: 120px;
            overflow: hidden;
        }

        .search-section-bg {
            position: absolute;
            top: 0; left: 0; right: 0; bottom: 0;
            width: 100%;
            height: 100%;
            object-fit: cover;
            object-position: center;
            filter: brightness(0.75);
        }

        .search-section-inner {
            position: relative;
            z-index: 1;
            max-width: 1150px;
            margin: 0 auto;
            height: 100%;
            display: flex;
            align-items: flex-end;
            justify-content: flex-end;
            padding: 0 20px 14px 20px;
        }

        .search-section .header-search {
            width: 100%;
            max-width: 400px;
        }
        .search-section .header-search input[type="text"] {
            flex: 1;
            width: auto;
        }

        /* ── Container ── */
        .container {
            max-width: 1150px;
            margin: 0 auto;
            padding: 20px 20px;
        }

        /* ── Filter bar ── */
        .filters-wrapper {
            display: flex;
            justify-content: flex-end;
            margin-bottom: 10px;
        }

        .filters {
            background: var(--bg2);
            border: 1px solid var(--border);
            border-radius: 0;
            padding: 8px 12px;
            display: inline-flex;
            gap: 10px;
            align-items: center;
            flex-wrap: wrap;
            justify-content: flex-end;
        }

        .filters label {
            display: block;
            font-size: 0.58rem;
            font-weight: 600;
            color: var(--text-dim);
            margin-bottom: 5px;
            text-transform: uppercase;
            letter-spacing: 0.8px;
        }

        .filters select {
            padding: 5px 8px;
            border: 1px solid var(--border2);
            border-radius: 0;
            font-family: 'Poppins', sans-serif;
            font-size: 0.82rem;
            font-weight: 500;
            background: var(--bg3);
            color: var(--text);
            width: 170px;
            cursor: pointer;
        }

        .filters select:focus { outline: none; border-color: var(--accent); }

        .filters a.clear {
            padding: 7px 12px;
            color: var(--text-muted);
            font-size: 0.8rem;
            font-weight: 600;
            text-decoration: none;
            transition: color 0.15s;
        }

        .filters a.clear:hover { color: var(--text); }

        /* ── Category buttons ── */
        .category-filters {
            display: flex;
            gap: 10px;
            padding: 0 0 0 24px;
            align-items: center;
            flex-wrap: wrap;
            justify-content: flex-end;
            margin-bottom: 14px;
        }

        .category-filters span {
            font-size: 0.62rem;
            font-weight: 700;
            color: var(--text-dim);
            text-transform: uppercase;
            letter-spacing: 0.8px;
        }

        .cat-btn {
            display: inline-flex;
            align-items: center;
            gap: 7px;
            padding: 5px 14px;
            border-radius: 20px;
            border: 1.5px solid transparent;
            font-family: 'Poppins', sans-serif;
            font-size: 0.78rem;
            font-weight: 700;
            cursor: pointer;
            text-decoration: none;
            transition: all 0.15s;
            letter-spacing: 0.2px;
        }

        .cat-btn .dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }

        .cat-btn.unclassified { border-color: #888; color: #555; background: rgba(128,128,128,0.07); }
        .cat-btn.unclassified.active { background: #888; color: white; border-width: 3px; }
        .cat-btn .dot.unclassified-dot { background: #888; }
        .cat-btn.newly-added { border-color: #2563eb; border-width: 3px; color: #1d4ed8; background: rgba(37,99,235,0.1); padding: 6px 37px; font-size: 0.78rem; }
        .cat-btn.newly-added.active { background: #2563eb; color: white; border: 3px solid #1d4ed8; }
        .cat-btn .dot.newly-added-dot { background: #2563eb; }
        .cat-btn.taking-comments { border-color: #cc1111; border-width: 3px; color: #cc1111; background: #e8e8e4; padding: 6px 37px; font-size: 0.78rem; }
        .cat-btn.taking-comments.active { background: #cc1111; color: white; border: 3px solid #cc1111; }
        .cat-btn.active-filter { border-color: #2d7a1f; border-width: 3px; color: #1a4f0f; background: rgba(45,122,31,0.15); padding: 6px 37px; font-size: 0.78rem; }
        .cat-btn.active-filter.active { background: #2d7a1f; color: white; border: 3px solid #1a4f0f; }
        .cat-btn .dot.active-filter-dot { background: #2d7a1f; }
        .cat-btn.taking-comments.active { background: #cc1111; color: white; border: 3px solid #cc1111; }
        .cat-btn .dot.taking-comments-dot { background: #fbbf24; border: 1px solid #cc1111; }

        .annotation-box {
            margin-top: 12px;
            display: inline-block;
        }

        .annotation-toggle {
            background: transparent;
            color: #2563eb;
            border: 2px solid #2563eb;
            padding: 5px 14px;
            font-size: 0.78rem;
            font-family: 'Poppins', sans-serif;
            cursor: pointer;
            font-weight: 600;
            width: 100%;
            text-align: left;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .annotation-toggle:hover { background: rgba(37,99,235,0.08); }

        .ann-arrow {
            display: inline-block;
            transition: transform 0.2s;
            font-style: normal;
        }

        .annotation-content {
            border: 2px solid #2563eb;
            border-top: none;
            background: #f0f4ff;
            padding: 10px 14px;
        }

        .annotation-text {
            font-size: 0.82rem;
            color: #1a1a1a;
            line-height: 1.5;
            white-space: pre-wrap;
            margin-bottom: 8px;
        }

        .annotation-copy {
            background: #2563eb;
            color: white;
            border: none;
            padding: 4px 12px;
            font-size: 0.75rem;
            cursor: pointer;
            font-family: 'Poppins', sans-serif;
        }

        .annotation-copy:hover { background: #1d4ed8; }

        .wildfire-badge:hover { background: #7a9079 !important; }

        .wildfire-badge {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            background: #8fa68e !important;
            color: white !important;
            border: none;
            border-radius: 4px;
            font-family: 'Poppins', sans-serif;
            font-size: 1.02rem;
            font-weight: 200;
            text-transform: none;
            letter-spacing: 0.8px;
            padding: 2px 4px;
            width: 255px;
            box-sizing: border-box;
            cursor: pointer;
        }

        .lfdc-commented-badge:hover { background: #7a9079 !important; }

        .lfdc-commented-badge {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            background: #8fa68e !important;
            color: white !important;
            border: none;
            border-radius: 4px;
            font-family: 'Poppins', sans-serif;
            font-size: 0.82rem;
            font-weight: 200;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            padding: 4px 6px;
            width: 255px;
            box-sizing: border-box;
            cursor: pointer;
        }

        @keyframes pulse-green {
            0%, 100% { box-shadow: 0 0 0 0 rgba(45,122,31,0.7); background: #2d7a1f; }
            50% { box-shadow: 0 0 0 10px rgba(45,122,31,0); background: #4aaa35; }
        }

        .btn-comment.primary.pulsing {
            animation: pulse-green 2s ease-in-out infinite;
        }

        .category-disclaimer {
            font-size: 0.62rem;
            color: var(--text-dim);
            font-style: italic;
        }

        .category-disclaimer-row {
            display: flex;
            justify-content: flex-end;
            padding: 3px 0 6px 0;
        }
        .cat-btn.extractive  { border-color: var(--red);    color: var(--red);    background: rgba(204,17,17,0.07); }
        .cat-btn.extractive.active  { background: var(--red);    color: white; border-width: 3px; }
        .cat-btn.restorative { border-color: var(--green);  color: var(--green);  background: rgba(45,122,31,0.07); }
        .cat-btn.restorative.active { background: var(--green);  color: white; border-width: 3px; }
        .cat-btn.mixed       { border-color: var(--orange); color: var(--orange); background: rgba(196,106,48,0.07); }
        .cat-btn.mixed.active       { background: var(--orange); color: white; border-width: 3px; }


        .cat-btn .dot.extractive-dot  { background: var(--red); }
        .cat-btn .dot.restorative-dot { background: var(--green); }
        .cat-btn .dot.mixed-dot       { background: var(--orange); }
        .cat-btn.active .dot          { background: currentColor; }

        /* ── Legend ── */
        .legend {
            display: flex;
            gap: 18px;
            font-size: 0.72rem;
            color: var(--text-dim);
            margin-bottom: 10px;
            flex-wrap: wrap;
            font-weight: 500;
        }

        .legend-item { display: flex; align-items: center; gap: 6px; }

        .legend-stripe { width: 12px; height: 12px; border-radius: 2px; flex-shrink: 0; }

        /* ── Results header ── */
        .results-header {
            font-size: 0.78rem;
            color: var(--text-muted);
            margin-bottom: 12px;
            margin-top: 4px;
            font-weight: 500;
        }

        .results-header strong { color: var(--text); font-weight: 700; }

        /* ── Project cards ── */
        .project-card {
            font-family: 'Poppins', sans-serif;
            background: var(--bg2);
            border: 1px solid var(--border);
            border-radius: 0;
            padding: 16px 18px 16px 46px;
            margin-bottom: 10px;
            transition: border-color 0.15s, box-shadow 0.15s;
            position: relative;
        }

        .card-category-bar {
            position: absolute;
            left: 0;
            top: 0;
            bottom: 0;
            width: 28px;
            display: flex;
            align-items: center;
            justify-content: center;
        }



        .card-category-label {
            writing-mode: vertical-rl;
            transform: rotate(180deg);
            font-size: 0.65rem;
            font-weight: 400;
            color: white;
            letter-spacing: 1.5px;
            text-transform: uppercase;
            white-space: nowrap;
            user-select: none;
        }

        .card-category-top {
            display: none;
            font-size: 0.62rem;
            font-weight: 400;
            color: white;
            letter-spacing: 1.5px;
            text-transform: uppercase;
            padding: 3px 12px;
            margin: -16px -16px 10px -16px;
        }

        .project-card:hover {
            border-color: var(--border2);
            box-shadow: 2px 2px 0 rgba(0,0,0,0.08);
        }

        /* border colors now set inline per card */

        .card-top {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 12px;
            margin-bottom: 6px;
        }

        .forest-tag {
            font-size: 1.3rem;
            font-weight: 700;
            color: var(--accent);
            text-transform: uppercase;
            letter-spacing: 0.8px;
            margin-bottom: 30px;
        }

        .project-card h2 { font-size: 0.92rem; font-weight: 700; }

        .project-card h2 a {
            color: var(--link);
            text-decoration: none;
            transition: color 0.15s;
        }

        .project-card h2 a:hover { color: white; }

        .status-badge {
            display: block;
            padding: 3px 10px;
            border-radius: 20px;
            font-size: 0.65rem;
            font-weight: 700;
            color: white;
            white-space: nowrap;
            letter-spacing: 0.3px;
            text-align: center;
            width: 255px;
            box-sizing: border-box;
        }

        .analysis-badge {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.62rem;
            font-weight: 600;
            color: var(--text-muted);
            background: var(--bg3);
            border: 1px solid var(--border);
            white-space: nowrap;
            letter-spacing: 0.2px;
            width: 255px;
            text-align: center;
            box-sizing: border-box;
        }

        /* Taking Comments Now badge */
        .comment-open-badge {
            display: inline-flex;
            flex-direction: column;
            align-items: center;
            padding: 3px 10px;
            border-radius: 0;
            background: #e8e8e4;
            border: 3px solid #cc1111;
            color: #cc1111;
            font-weight: 700;
            font-size: 0.82rem;
            line-height: 1.2;
            text-align: center;
            animation: pulse-yellow 2.5s ease-in-out infinite;
            flex-shrink: 0;
            box-shadow: 0 2px 8px rgba(204,17,17,0.2);
            width: 475px;
            box-sizing: border-box;
        }

        .comment-open-badge .badge-title {
            font-size: 0.88rem;
            font-weight: 800;
            letter-spacing: 0.4px;
        }

        .comment-open-badge .badge-deadline {
            font-family: 'Poppins', sans-serif;
            font-size: 0.72rem;
            font-weight: 200;
            opacity: 0.9;
            margin-top: 2px;
        }

        @keyframes pulse-yellow {
            0%, 100% { opacity: 1; box-shadow: 0 2px 8px rgba(204,17,17,0.2); }
            50%       { opacity: 0.8; box-shadow: 0 2px 16px rgba(204,17,17,0.4); }
        }

        .new-badge {
            display: inline-block;
            background: rgba(37,99,235,0.1);
            color: #2563eb;
            border: 2px solid #2563eb;
            border-radius: 0;
            font-size: 0.78rem;
            font-weight: 700;
            padding: 3px 8px;
            vertical-align: middle;
            margin-left: 6px;
            letter-spacing: 0.3px;
        }

        /* ── Card layout ── */

        /* Card header: forest + title left, taking comments badge right */
        .card-header-row {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 16px;
            margin-bottom: 10px;
        }

        .card-header-left {
            flex: 1;
            min-width: 0;
        }

        /* Desktop: badge in header right, hidden on mobile */
        .card-header-badge {
            flex-shrink: 0;
            width: 240px;
            display: flex;
            justify-content: flex-end;
        }

        /* Two-column body below header */
        .card-body {
            display: flex;
            flex-direction: row;
            gap: 16px;
            min-height: 80px;
        }

        /* Left column: description fills, buttons pin to bottom */
        .card-body-left {
            flex: 1;
            display: flex;
            flex-direction: column;
            min-width: 0;
        }

        .card-body-left .description {
            font-size: 0.82rem;
            color: var(--text-muted);
            line-height: 1.6;
            font-weight: 400;
            flex: 1;
        }

        .card-body-left .left-bottom {
            margin-top: auto;
            display: flex;
            flex-direction: column;
            gap: 6px;
            padding-top: 10px;
        }

        .card-body .description {
            font-size: 0.82rem;
            color: var(--text-muted);
            line-height: 1.6;
            font-weight: 400;
        }

        /* Right column: status + analysis + milestone, top-aligned */
        .card-body-right {
            display: flex;
            flex-direction: column;
            align-items: flex-end;
            justify-content: flex-start;
            gap: 6px;
            flex-shrink: 0;
            width: 255px;
        }

        .card-body-right .status-badge,
        .card-body-right .analysis-badge,
        .card-body-right .milestone-section {
            width: 255px;
            box-sizing: border-box;
        }

        /* ── Milestone table ── */
        .milestone-section {
            width: 255px;
            border: 1px solid var(--border2);
            border-radius: 0;
            overflow: hidden;
            background: #e8e8e4;
            flex-shrink: 0;
        }

        .milestone-section-label {
            font-size: 0.6rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            color: #555;
            padding: 5px 10px 4px;
            background: #d0d0cc;
            border-bottom: 1px solid #c0c0bc;
            border-radius: 0;
        }

        .milestone-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.72rem;
        }

        .milestone-table th {
            text-align: left;
            padding: 4px 10px;
            background: #d8d8d4;
            color: #555;
            font-weight: 700;
            font-size: 0.6rem;
            text-transform: uppercase;
            letter-spacing: 0.6px;
            border-bottom: 1px solid #c0c0bc;
        }

        .milestone-table td {
            padding: 4px 10px;
            border-bottom: 1px solid var(--border);
            color: var(--text);
            font-weight: 500;
        }

        .milestone-table tr:last-child td { border-bottom: none; }

        .milestone-table td.date-cell {
            white-space: nowrap;
            color: var(--text-muted);
            text-align: right;
        }

        .milestone-table td.date-cell.estimated {
            color: var(--text-dim);
            font-style: italic;
        }

        /* ── Title button ── */
        .btn-title-wrap {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 8px;
        }

        .project-title-text {
            font-family: 'Poppins', sans-serif;
            font-size: 1.3rem;
            font-weight: 400;
            color: #1a1a1a;
            letter-spacing: 0.8px;
            line-height: 1.3;
            display: block;
            padding-right: 10px;
        }

        .btn-comment.project-link {
            background: white;
            color: #2563eb;
            border: 2px solid #2563eb;
        }

        .btn-comment.project-link:hover {
            background: #f0f4ff;
            color: #1d4ed8;
        }

        .btn-title {
            display: inline-flex;
            align-items: center;
            padding: 5px 12px;
            border-radius: 0;
            font-family: 'Poppins', sans-serif;
            font-size: 0.88rem;
            font-weight: 600;
            text-decoration: none;
            color: var(--text);
            background: #e8e8e4;
            border: 1px solid var(--border2);
            transition: background 0.15s, border-color 0.15s;
        }

        .btn-title:hover {
            background: #d8d8d4;
            border-color: var(--text-dim);
            color: var(--text);
        }

        /* ── Comment buttons ── */
        .comment-buttons {
            display: flex;
            gap: 8px;
            margin-top: 8px;
            flex-wrap: wrap;
        }

        .btn-comment {
            display: inline-block;
            padding: 5px 12px;
            border-radius: 0;
            font-family: 'Poppins', sans-serif;
            font-size: 0.72rem;
            font-weight: 600;
            text-decoration: none;
            transition: opacity 0.15s;
            white-space: nowrap;
        }

        .btn-comment:hover { opacity: 0.82; }

        .btn-comment.primary {
            background: var(--green);
            color: white;
        }

        .btn-comment.secondary {
            background: white;
            color: #555;
            border: 2px solid var(--green);
        }

        .btn-comment.primary {
            border: 1px solid var(--green);
        }

        .btn-comment.secondary:hover {
            background: #f0fff0;
            color: #333;
            border-color: var(--green);
        }

        .btn-comment.primary-inactive {
            background: white !important;
            color: #999 !important;
            border: 1px solid #b8b8b4 !important;
            cursor: pointer;
        }

        .btn-comment.primary-inactive:hover {
            background: #f8f8f8 !important;
            color: #777 !important;
        }

        /* ── Mobile layout ── */
        @media (max-width: 680px) {

            /* Scale down all fonts by 15% on mobile */
            html { font-size: 85%; }

            /* Prevent horizontal overflow in iFrame */
            html, body { max-width: 100%; overflow-x: hidden; }
            * { box-sizing: border-box; }

            /* Header stacks vertically */
            header {
                flex-direction: column;
                align-items: flex-start;
                gap: 10px;
                padding: 12px 16px;
            }

            .header-search { width: 100%; }
            .header-search input[type="text"] { width: 100%; }

            .forest-summary-inner { gap: 6px; }
            .summary-totals { margin-left: 0; width: 100%; }

            /* Mobile: 2-column forest summary */
            .forest-cols-row {
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 12px;
                width: 100%;
            }

            .forest-col {
                width: 100% !important;
                flex: unset !important;
                padding: 0 !important;
            }

            .forest-pill {
            transition: opacity 0.15s, box-shadow 0.15s;
        }

        .forest-pill.pill-selected {
            box-shadow: 0 0 0 2px white, 0 0 0 3px currentColor;
        }

        .forest-pill-link {
            text-decoration: none;
        }

        /* Original forest-pill styles below */
        .forest-pill {
                width: 100%;
                box-sizing: border-box;
            }

            /* Column grouping for mobile */
            .forest-col-group {
                display: flex;
                flex-direction: column;
                gap: 4px;
            }

            .filters { gap: 8px; }
            .filters select { width: 100%; }

            .container { padding: 10px; }
            .project-card {
            font-family: 'Poppins', sans-serif; padding: 12px 14px; }

            /* ── Card layout: full vertical stack ── */

            /* Comment badge — mobile: full width, smaller */
            .comment-open-badge {
                width: 100% !important;
                box-sizing: border-box;
                font-size: 0.72rem !important;
                padding: 5px 10px !important;
                margin-bottom: 6px;
                align-self: stretch;
            }
            .comment-open-badge .badge-title { font-size: 0.76rem !important; }
            .comment-open-badge .badge-deadline { font-size: 0.65rem !important; }

            /* Card top: forest + title full width, then status/analysis right */
            .card-top {
                display: flex;
                flex-direction: column;
                gap: 6px;
            }

            .card-top-left {
                width: 100%;
            }

            .card-top-left .forest-tag {
                font-size: 0.7rem;
            }

            .card-top-left .btn-title {
                width: 100%;
                display: block;
                box-sizing: border-box;
            }

            .card-top-right {
                display: flex;
                flex-direction: row;
                align-items: center;
                justify-content: flex-end;
                gap: 6px;
                flex-wrap: wrap;
                width: 100%;
            }

            /* Show/hide desktop vs mobile elements */
            .desktop-only { display: none !important; }
            .mobile-only  { display: flex !important; }
            div.mobile-only { display: flex !important; }
            .forest-col-group.mobile-only { display: flex !important; flex-direction: column; gap: 8px; }
            .forest-col.desktop-only { display: none !important; visibility: hidden !important; pointer-events: none !important; }

            /* Comment badge centered on mobile */
            .mobile-only.comment-open-badge {
                align-self: center;
                margin: 0 auto 8px auto;
                width: fit-content;
            }

            /* Mobile: single column */
            .project-card {
            font-family: 'Poppins', sans-serif;
                display: flex !important;
                flex-direction: column !important;
                padding-left: 16px !important;
            }

            /* Hide vertical left bar on mobile */
            .card-category-bar { display: none !important; }

            /* Show horizontal top label on mobile */
            .card-category-top { display: block !important; }

            .card-header-row {
                flex-direction: column;
            }

            .card-header-left { width: 100%; }

            .card-header-badge { display: none !important; }

            .card-body {
                flex-direction: column;
                gap: 10px;
                width: 100%;
            }

            .card-body-left { width: 100%; }

            .card-body .description { width: 100%; }

            .milestone-section {
                width: 100% !important;
            }

            /* Comment buttons stack vertically */
            .comment-buttons {
                flex-direction: column;
                gap: 6px;
            }

            .btn-comment {
                width: 100%;
                text-align: center;
                justify-content: center;
            }

            /* Meta at very bottom */
            .meta { margin-top: 10px; }
        }

        /* Desktop/mobile visibility helpers */
        .desktop-only { display: flex; }
        .mobile-only  { display: none; }

        /* ── Meta ── */
        .meta {
            font-size: 0.7rem;
            color: var(--text-dim);
            margin-top: 10px;
            font-weight: 500;
        }

        .meta span { margin-right: 14px; }

        /* ── No results ── */
        .no-results {
            text-align: center;
            padding: 60px 20px;
            color: var(--text-dim);
            font-size: 0.95rem;
            font-weight: 500;
        }

        /* ── Footer ── */
        footer {
            text-align: center;
            padding: 28px;
            font-size: 0.7rem;
            color: var(--text-dim);
            margin-top: 20px;
            font-weight: 500;
        }
    </style>
</head>
<body>

<div class="top-search-bar">
    <div class="top-search-inner">
        <form class="header-search" method="GET" action="/" id="searchform" style="position:relative;">
            <input type="hidden" name="forest"   value="{{ selected_forest }}">
            <input type="hidden" name="status"   value="{{ selected_status }}">
            <input type="hidden" name="days"     value="{{ selected_days }}">
            <input type="hidden" name="sort"     value="{{ selected_sort }}">
            <input type="hidden" name="sort2"    value="{{ selected_sort2 }}">
            <input type="hidden" name="category" value="{{ selected_category_str }}">
            <input type="hidden" name="forests"  value="{{ selected_forests_str }}">
            <div style="position:relative; display:inline-block;">
                <input type="text" name="q" id="search-q"
                       placeholder="Search projects..."
                       value="{{ search }}"
                       autocomplete="off">
                <button type="button" id="search-clear"
                        onclick="document.getElementById('search-q').value=''; document.getElementById('search-q').dispatchEvent(new Event('input')); this.style.display='none';"
                        style="position:absolute; right:6px; top:50%; transform:translateY(-50%); background:none; border:none; color:#aaa; font-size:1rem; cursor:pointer; padding:0; line-height:1; display:{{ 'flex' if search else 'none' }};">✕</button>
            </div>
            <button type="submit">Search</button>
        </form>
    </div>
</div>

<!-- Forest summary bar -->
<div class="forest-summary">
    <div class="forest-summary-inner">
        <div class="forest-cols-row">
            <!-- Desktop: individual columns. Mobile: left group (CA/CA+OR/OR), right group (WA/AK) -->
            {% set left_states = ['CA', 'CA+OR', 'OR'] %}
            {% set right_states = ['WA', 'AK'] %}

            <!-- Left mobile group -->
            <div class="forest-col-group mobile-only" style="display:none;">
                {% for state in left_states %}
                {% set col_forests = forests|selectattr('state','eq',state)|sort(attribute='name')|list %}
                {% if col_forests %}
                {% set sc = state_colors.get(state, {}) %}
                <div class="forest-col">
                    <div class="forest-col-label" style="color:{{ sc.get('label','var(--text-dim)') }};">{{ state }}</div>
                    {% for f in col_forests %}
                    {% set is_sel = f.code in selected_forests %}
                    <a href="{{ toggle_forest_url(f.code, selected_forests_str) }}"
                       class="forest-pill {{ 'pill-selected' if is_sel else '' }}"
                       style="background:{{ sc.get('pill','var(--accent)') }}; opacity:{{ '1' if (not selected_forests or is_sel) else '0.4' }}; text-decoration:none;">
                        {{ f.name.replace('National Forest', 'NF') }}
                        <span class="forest-pill-count">{{ forest_counts[f.code].total }}</span>
                    </a>
                    {% endfor %}
                </div>
                {% endif %}
                {% endfor %}
            </div>

            <!-- Right mobile group -->
            <div class="forest-col-group mobile-only" style="display:none;">
                {% for state in right_states %}
                {% set col_forests = forests|selectattr('state','eq',state)|sort(attribute='name')|list %}
                {% if col_forests %}
                {% set sc = state_colors.get(state, {}) %}
                <div class="forest-col">
                    <div class="forest-col-label" style="color:{{ sc.get('label','var(--text-dim)') }};">{{ state }}</div>
                    {% for f in col_forests %}
                    {% set is_sel = f.code in selected_forests %}
                    <a href="{{ toggle_forest_url(f.code, selected_forests_str) }}"
                       class="forest-pill {{ 'pill-selected' if is_sel else '' }}"
                       style="background:{{ sc.get('pill','var(--accent)') }}; opacity:{{ '1' if (not selected_forests or is_sel) else '0.4' }}; text-decoration:none;">
                        {{ f.name.replace('National Forest', 'NF') }}
                        <span class="forest-pill-count">{{ forest_counts[f.code].total }}</span>
                    </a>
                    {% endfor %}
                </div>
                {% endif %}
                {% endfor %}
            </div>

            <!-- Desktop: individual columns (hidden on mobile) -->
            {% for state in state_columns %}
            {% set col_forests = forests|selectattr('state','eq',state)|sort(attribute='name')|list %}
            {% if col_forests %}
            {% set sc = state_colors.get(state, {}) %}
            <div class="forest-col desktop-only" style="display:flex;">
                <div class="forest-col-label" style="color:{{ sc.get('label','var(--text-dim)') }};">{{ state }}</div>
                {% for f in col_forests %}
                {% set is_sel = f.code in selected_forests %}
                <a href="{{ toggle_forest_url(f.code, selected_forests_str) }}"
                   class="forest-pill {{ 'pill-selected' if is_sel else '' }}"
                   style="background:{{ sc.get('pill','var(--accent)') }}; opacity:{{ '1' if (not selected_forests or is_sel) else '0.4' }}; text-decoration:none;">
                    {{ f.name.replace('National Forest', 'NF') }}
                    <span class="forest-pill-count">{{ forest_counts[f.code].total }}</span>
                </a>
                {% endfor %}
            </div>
            {% endif %}
            {% endfor %}
        </div>
        <div class="forest-totals-row">
            <span class="summary-totals">
                <strong>{{ total }}</strong> total
            </span>
            <a href="/" class="forest-reset-btn">Reset</a>
        </div>
    </div>
</div>

<div class="container">

    <div class="filters-wrapper">
    <form class="filters" method="GET" action="/">
        <input type="hidden" name="q"        value="{{ search }}">
        <input type="hidden" name="category" value="{{ selected_category_str }}">
        <input type="hidden" name="forests"  value="{{ selected_forests_str }}">
        <input type="hidden" name="forest"   value="{{ selected_forest }}">
        <div>
            <label for="status">Status</label>
            <select id="status" name="status" onchange="this.form.submit()">
                <option value="">All statuses</option>
                {% for s in status_list %}
                <option value="{{ s }}"
                    {% if selected_status == s %}selected{% endif %}>
                    {{ s }}
                </option>
                {% endfor %}
            </select>
        </div>
        <div>
            <label for="days">Added to tracker</label>
            <select id="days" name="days" onchange="this.form.submit()">
                <option value="">Any time</option>
                {% for value, label in date_ranges %}
                <option value="{{ value }}"
                    {% if selected_days == value %}selected{% endif %}>
                    {{ label }}
                </option>
                {% endfor %}
            </select>
        </div>
        <div>
            <label for="sort">Sort by</label>
            <select id="sort" name="sort" onchange="this.form.submit()">
                <option value="cara_newest" {% if selected_sort == "cara_newest" %}selected{% endif %}>Default</option>
                <option value="newest"   {% if selected_sort == "newest"   %}selected{% endif %}>Newest first</option>
                <option value="oldest"   {% if selected_sort == "oldest"   %}selected{% endif %}>Oldest first</option>
                <option value="name"     {% if selected_sort == "name"     %}selected{% endif %}>Project name A–Z</option>
                <option value="forest"   {% if selected_sort == "forest"   %}selected{% endif %}>Forest</option>
                <option value="analysis" {% if selected_sort == "analysis" %}selected{% endif %}>Analysis type</option>
                <option value="status"   {% if selected_sort == "status"   %}selected{% endif %}>Status</option>
                <option value="impact"        {% if selected_sort == "impact"        %}selected{% endif %}>Impact category</option>
                <option value="scoping_newest"        {% if selected_sort == "scoping_newest"        %}selected{% endif %}>Scoping date newest</option>
                <option value="decision_newest"       {% if selected_sort == "decision_newest"       %}selected{% endif %}>Decision date newest</option>
                <option value="implementation_newest" {% if selected_sort == "implementation_newest" %}selected{% endif %}>Implementation newest</option>
            </select>
        </div>
        <div>
            <label for="sort2">Then sort by</label>
            <select id="sort2" name="sort2" onchange="this.form.submit()">
                <option value="">None</option>
                <option value="newest"   {% if selected_sort2 == "newest"   %}selected{% endif %}>Newest first</option>
                <option value="oldest"   {% if selected_sort2 == "oldest"   %}selected{% endif %}>Oldest first</option>
                <option value="name"     {% if selected_sort2 == "name"     %}selected{% endif %}>Project name A–Z</option>
                <option value="forest"   {% if selected_sort2 == "forest"   %}selected{% endif %}>Forest</option>
                <option value="status"   {% if selected_sort2 == "status"   %}selected{% endif %}>Status</option>
                <option value="impact"   {% if selected_sort2 == "impact"   %}selected{% endif %}>Impact category</option>
                <option value="analysis"      {% if selected_sort2 == "analysis"      %}selected{% endif %}>Analysis type</option>
                <option value="scoping_newest"        {% if selected_sort2 == "scoping_newest"        %}selected{% endif %}>Scoping date newest</option>
                <option value="decision_newest"       {% if selected_sort2 == "decision_newest"       %}selected{% endif %}>Decision date newest</option>
                <option value="implementation_newest" {% if selected_sort2 == "implementation_newest" %}selected{% endif %}>Implementation newest</option>
            </select>
        </div>
        {% if search or selected_forest or selected_status or selected_days or selected_category_str or selected_sort or selected_sort2 %}
        <a class="clear" href="/">Clear all</a>
        {% endif %}
    </form>
    </div>
    <div class="category-filters">
        <span>Show only:</span>
        <a href="{{ url_with_category('extractive') }}"
           class="cat-btn extractive {{ 'active' if 'extractive' in selected_categories else '' }}">
            <span class="dot extractive-dot"></span>
            Significant Effect ({{ filtered_counts.extractive }} of {{ counts.extractive }})
        </a>
        <a href="{{ url_with_category('mixed') }}"
           class="cat-btn mixed {{ 'active' if 'mixed' in selected_categories else '' }}">
            <span class="dot mixed-dot"></span>
            Mixed Impact ({{ filtered_counts.mixed }} of {{ counts.mixed }})
        </a>
        <a href="{{ url_with_category('restorative') }}"
           class="cat-btn restorative {{ 'active' if 'restorative' in selected_categories else '' }}">
            <span class="dot restorative-dot"></span>
            Restorative Impact ({{ filtered_counts.restorative }} of {{ counts.restorative }})
        </a>
        <a href="{{ url_with_category('unclassified') }}"
           class="cat-btn unclassified {{ 'active' if 'unclassified' in selected_categories else '' }}">
            <span class="dot unclassified-dot"></span>
            Unknown ({{ filtered_counts.unclassified }} of {{ counts.unclassified }})
        </a>
        <a href="{{ url_with_category('newly_added') }}"
           class="cat-btn newly-added {{ 'active' if 'newly_added' in selected_categories else '' }}">
            <span class="dot newly-added-dot"></span>
            Newly Added ({{ filtered_counts.newly_added }} of {{ counts.newly_added }})
        </a>
        <a href="{{ url_with_category('taking_comments') }}"
           class="cat-btn taking-comments {{ 'active' if 'taking_comments' in selected_categories else '' }}">
            <span class="dot taking-comments-dot"></span>
            💬 Taking Comments Now ({{ filtered_counts.taking_comments }} of {{ counts.taking_comments }})
        </a>
        <a href="{{ url_with_category('active') }}"
           class="cat-btn active-filter {{ 'active' if 'active' in selected_categories else '' }}">
            <span class="dot active-filter-dot"></span>
            Active / In Development ({{ filtered_counts.active }} of {{ counts.active }})
        </a>
    </div>
    <div class="category-disclaimer-row">
        <span class="category-disclaimer">*Impact level assigned automatically, based on keywords and is intended as a general guide only</span>
    </div>

    <div class="results-header">
        {% set cat_labels = {'extractive': 'Significant Effect', 'mixed': 'Mixed Impact', 'restorative': 'Restorative Impact', 'unclassified': 'Unknown', 'taking_comments': 'Taking Comments Now', 'active': 'Active / In Development', 'newly_added': 'Newly Added'} %}
        {% if search or selected_forest or selected_status or selected_days or selected_category_str %}
            Showing <strong>{{ projects|length }}</strong> result{% if projects|length != 1 %}s{% endif %}
            {% if selected_categories %} — <strong>{% for cat in selected_categories %}{{ cat_labels.get(cat, cat) }}{% if not loop.last %} · {% endif %}{% endfor %}</strong>{% endif %}
            {% if selected_days %} added in the last <strong>{{ selected_days }} days</strong>{% endif %}
            {% if search %} matching "<strong>{{ search }}</strong>"{% endif %}
            {% if selected_status %} · status: <strong>{{ selected_status }}</strong>{% endif %}
            {% if selected_forest %} · <strong>{{ selected_forest_name }}</strong>{% endif %}
        {% else %}
            Showing all <strong>{{ projects|length }}</strong> active projects
        {% endif %}
    </div>

    {% if projects %}
        {% for p in projects %}
        {% set has_milestones = p.get('milestones') and p['milestones']|length > 0 %}
        {% set status_color = status_colors.get(p.status, '#d0d0c8') %}
        {% set cat_bg = {'extractive': 'rgba(204,17,17,0.18)', 'restorative': 'rgba(45,122,31,0.15)', 'mixed': 'rgba(196,106,48,0.16)'}.get(p.category or '', 'white') %}
        {% set cat_border = {'extractive': '#cc1111', 'restorative': '#2d7a1f', 'mixed': '#c46a30'}.get(p.category or '', '#d0d0c8') %}
        {% set cat_label = {'extractive': 'Significant Effect', 'restorative': 'Restorative Impact', 'mixed': 'Mixed Impact'}.get(p.category or '', '') %}
        {% set is_tcn = p.get('accepting_comments') %}
        <div class="project-card {{ p.category or '' }}"
             style="background: {{ 'white' if is_tcn else cat_bg }};
                    border: {{ '2px' if is_tcn else '1px' }} solid {{ cat_border }};">
            <div class="card-category-bar" style="background: {{ cat_border }};">
                {% if cat_label %}
                <span class="card-category-label">{{ cat_label }}</span>
                {% endif %}
            </div>

            {% if cat_label %}
            <div class="card-category-top" style="background: {{ cat_border }};">
                {{ cat_label }}
            </div>
            {% endif %}

            <!-- CARD HEADER: forest name + title left, taking comments badge right -->
            <div class="card-header-row">
                <div class="card-header-left">
                    {% if p.get('accepting_comments') %}
                    <div class="comment-open-badge mobile-only" style="margin-bottom:8px;">
                        <span class="badge-title">💬 Taking Comments Now!</span>
                        {% if p.get('comment_deadline') %}
                        <span class="badge-deadline">Deadline: {{ p.comment_deadline }}</span>
                        {% endif %}
                    </div>
                    {% endif %}
                    {% set _fstate = forest_state_map.get(p.forest_code, '') %}
                    {% set _fcolor = state_colors.get(_fstate, {}).get('pill', '#2d7a1f') %}
                    <div class="forest-tag" style="color: {{ _fcolor }};">{{ p.forest_name }}</div>
                    <div class="btn-title-wrap">
                        <span class="project-title-text">{{ p.project_name }}</span>
                        {% if p.get('first_seen') and p['first_seen'][:10] >= recent_cutoff %}
                        <span class="new-badge">NEW</span>
                        {% endif %}
                    </div>
                </div>
                <div class="card-header-badge desktop-only">
                {% if p.get('accepting_comments') %}
                <div class="comment-open-badge">
                    <span class="badge-title">💬 Taking Comments Now!</span>
                    {% if p.get('comment_deadline') %}
                    <span class="badge-deadline">Deadline: {{ p.comment_deadline }}</span>
                    {% endif %}
                </div>
                {% endif %}
                </div>
            </div>

            <!-- LEFT BOTTOM: description + buttons + meta (grid row 2) -->
            <div class="card-body">
                <div class="card-body-left">
                    {% if p.description %}
                    <div class="description">{{ p.description }}</div>
                    {% endif %}
                    <div class="left-bottom">
                        <!-- Mobile: status + analysis above buttons -->
                        <div class="mobile-only" style="display:none; justify-content:flex-end; gap:6px; flex-wrap:wrap;">
                            {% if p.status %}
                            <span class="status-badge" style="background: {{ status_colors.get(p.status, '#8892a4') }}; width:auto;">
                                {{ p.status }}
                            </span>
                            {% endif %}
                            {% set atype = p.analysis_type if p.get('analysis_type') else 'Unknown' %}
                            <span class="analysis-badge"
                                  style="background: {{ analysis_colors.get(atype, '#999') }}; color:white; border-color:transparent; width:auto;"
                                  title="{{ analysis_tooltips.get(atype, '') }}">
                                {{ atype }}
                            </span>
                        </div>
                        <!-- Mobile milestone table -->
                        {% if has_milestones %}
                        <div class="milestone-section mobile-only" style="width:100%;">
                            <div class="milestone-section-label">Project Milestones</div>
                            <table class="milestone-table">
                                <thead><tr><th>Milestone</th><th>Date</th></tr></thead>
                                <tbody>
                                    {% for m in p['milestones'] %}
                                    <tr>
                                        <td>{{ m.milestone }}</td>
                                        <td class="date-cell {{ 'estimated' if m.estimated else '' }}">{{ m.date if m.date else '—' }}</td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                        {% endif %}
                        <!-- Comment buttons (desktop: side by side; mobile: stacked) -->
                        {% set project_id = p.project_url.rstrip('/').split('/')[-1] %}
                        <div class="comment-buttons">
                            <a class="btn-comment project-link"
                               href="{{ p.project_url }}"
                               target="_blank" rel="noopener">🔗 View Project Page</a>
                            {% if has_milestones %}
                            <a class="btn-comment {{ 'primary' if p.get('accepting_comments') else 'primary-inactive' }}"
                               href="https://cara.fs2c.usda.gov/Public/CommentInput?Project={{ project_id }}"
                               target="_blank" rel="noopener">{{ '✍️ ' if p.get('accepting_comments') else '' }}Submit New Comments</a>
                            <a class="btn-comment secondary"
                               href="https://cara.fs2c.usda.gov/Public/ReadingRoom?Project={{ project_id }}"
                               target="_blank" rel="noopener">📖 View Prior Comments</a>
                            {% endif %}
                        </div>
        {% set ann = annotations.get(p.project_url, {}) %}
                        {% if ann.get('annotation') %}
                        <div class="annotation-box">
                            <button class="annotation-toggle" onclick="
                                var box = this.nextElementSibling;
                                var isHidden = box.style.display === 'none' || box.style.display === '';
                                box.style.display = isHidden ? 'block' : 'none';
                                var arrow = this.querySelector('.ann-arrow');
                                if (arrow) arrow.style.transform = isHidden ? 'rotate(90deg)' : 'rotate(0deg)';
                                var card = this.closest('.project-card');
                                var submitBtn = card ? card.querySelector('.btn-comment.primary') : null;
                                if (submitBtn) submitBtn.classList.toggle('pulsing', isHidden);
                            "><i class="ann-arrow">▶</i> View and Copy Suggested Comment</button>
                            <div class="annotation-content" style="display:none;">
                                <div class="annotation-text">{{ ann.annotation }}</div>
                                <button class="annotation-copy" onclick="navigator.clipboard.writeText(this.previousElementSibling.innerText); this.innerText='Copied!'; setTimeout(()=>this.innerText='Copy to clipboard',2000)">Copy to clipboard</button>
                            </div>
                        </div>
                        {% endif %}
                        <!-- Meta tags -->
                        <div class="meta">
                            {% if p.unit %}<span>📍 {{ p.unit }}</span>{% endif %}
                            {% if p.purpose %}<span>🏷 {{ p.purpose.replace('|', ' · ') }}</span>{% endif %}
                            {% if p.first_seen %}<span>Added: {{ p.first_seen[:10] }}</span>{% endif %}
                        </div>
                    </div>
                </div><!-- card-body-left -->

                <!-- RIGHT COLUMN (desktop only): status + analysis + milestone -->
                <div class="card-body-right desktop-only">
                    {% if p.project_url in thinning_urls %}
                    <a href="{{ thinning_url }}" target="_blank" rel="noopener" class="wildfire-badge" style="text-decoration:none;">
                        Learn About Thinning <svg style="width:14px;height:14px;flex-shrink:0;margin-left:4px;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
                    </a>
                    {% endif %}
                    {% if p.project_url in wildfire_urls %}
                    <a href="{{ wildfire_url }}" target="_blank" rel="noopener" class="wildfire-badge" style="text-decoration:none;">
                        Learn About Wildfire <svg style="width:14px;height:14px;flex-shrink:0;margin-left:4px;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
                    </a>
                    {% endif %}
                    {% if p.project_url in commented_urls %}
                    {% set comment_link = commented_urls_map.get(p.project_url, '') %}
                    {% if comment_link %}
                    <a href="{{ comment_link }}" target="_blank" rel="noopener" class="lfdc-commented-badge" style="text-decoration:none;">
                        <img src="/static/LFDC_Logo.png" style="height:30px; width:30px; object-fit:contain; vertical-align:middle;"> LFDC Commented <svg style="width:14px;height:14px;flex-shrink:0;margin-left:4px;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
                    </a>
                    {% else %}
                    <div class="lfdc-commented-badge">
                        <img src="/static/LFDC_Logo.png" style="height:30px; width:30px; object-fit:contain; vertical-align:middle;"> LFDC Commented <svg style="width:12px;height:12px;flex-shrink:0;margin-left:4px;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
                    </div>
                    {% endif %}
                    {% endif %}
                    {% if p.status %}
                    <span class="status-badge" style="background: {{ status_colors.get(p.status, '#8892a4') }}">
                        {{ p.status }}
                    </span>
                    {% endif %}
                    {% set atype = p.analysis_type if p.get('analysis_type') else 'Unknown' %}
                    <span class="analysis-badge"
                          style="background: {{ analysis_colors.get(atype, '#999') }}; color:white; border-color:transparent;"
                          title="{{ analysis_tooltips.get(atype, '') }}">
                        {{ atype }}
                    </span>
                    {% if has_milestones %}
                    <div class="milestone-section">
                        <table class="milestone-table">
                            <thead><tr><th>Milestone</th><th>Date</th></tr></thead>
                            <tbody>
                                {% for m in p['milestones'] %}
                                <tr>
                                    <td>{{ m.milestone }}</td>
                                    <td class="date-cell {{ 'estimated' if m.estimated else '' }}">{{ m.date if m.date else '—' }}</td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                    {% endif %}
                </div><!-- card-body-right -->
            </div><!-- card-body -->
        </div>
        {% endfor %}
    {% else %}
        <div class="no-results">No projects found matching your search.</div>
    {% endif %}

</div>

<footer>
    Data scraped from fs.usda.gov &nbsp;·&nbsp; Last updated: {{ last_scraped }}
</footer>

<script>
// Client-side instant search — filters cards without page reload
(function() {
    var input = document.querySelector('#searchform input[name="q"]');
    if (!input) return;

    // Give each card a searchable text attribute on load
    document.querySelectorAll('.project-card').forEach(function(card) {
        var text = card.innerText.toLowerCase();
        card.dataset.searchText = text;
    });

    function doFilter() {
        var term = input.value.toLowerCase().trim();
        var cards = document.querySelectorAll('.project-card');
        var visible = 0;
        cards.forEach(function(card) {
            var match = !term || card.dataset.searchText.indexOf(term) !== -1;
            card.style.display = match ? '' : 'none';
            if (match) visible++;
        });
        // Update results count if element exists
        var countEl = document.querySelector('.results-header strong');
        if (countEl && term) countEl.innerText = visible;
    }

    input.addEventListener('input', function() {
        var clearBtn = document.getElementById('search-clear');
        if (clearBtn) clearBtn.style.display = input.value ? 'flex' : 'none';
        doFilter();
    });

    // Still allow form submit (e.g. hitting Enter) for full server filter
    // but intercept if it's just a search with no other filters active
})();
</script>
</body>
<script>

</script>
</html>
"""


def toggle_forest_url_fn(code, current_str):
    """Return URL with the given forest code toggled in the forests param."""
    from flask import request as req
    from urllib.parse import urlencode
    current = [f.strip() for f in current_str.split(",") if f.strip()]
    if code in current:
        new = [c for c in current if c != code]
    else:
        new = current + [code]
    args = {}
    if req.args.get("q"):         args["q"]        = req.args.get("q")
    if req.args.get("status"):    args["status"]   = req.args.get("status")
    if req.args.get("days"):      args["days"]     = req.args.get("days")
    if req.args.get("sort"):      args["sort"]     = req.args.get("sort")
    if req.args.get("sort2"):     args["sort2"]    = req.args.get("sort2")
    # Don't carry category when toggling forest — avoids zero results
    if new:                       args["forests"]  = ",".join(new)
    return "/?" + urlencode(args) if args else "/"


@app.route("/")
def index():
    search            = request.args.get("q", "").strip()
    selected_forests_str = request.args.get("forests", "").strip()
    selected_forests     = [f.strip() for f in selected_forests_str.split(",") if f.strip()]
    selected_forest   = request.args.get("forest", "").strip()
    selected_status   = request.args.get("status", "").strip()
    selected_days     = request.args.get("days", "").strip()
    selected_category_str = request.args.get("category", "").strip()
    selected_categories = [c.strip() for c in selected_category_str.split(",") if c.strip()]
    selected_sort     = request.args.get("sort", "cara_newest").strip()
    selected_sort2    = request.args.get("sort2", "").strip()

    all_projects, last_scraped = load_projects()
    annotations = load_annotations()
    commented_urls = set(annotations.get("_commented", []))
    wildfire_urls_manual = set(annotations.get("_wildfire", []))
    thinning_urls_manual = set(annotations.get("_thinning", []))
    # Combine auto + manual
    wildfire_urls = wildfire_urls_manual | {p["project_url"] for p in all_projects if has_wildfire_badge(p)}
    thinning_urls = thinning_urls_manual | {p["project_url"] for p in all_projects if has_thinning_badge(p)}

    recent_cutoff = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=72)
    ).strftime("%Y-%m-%d")

    counts = {
        "extractive":      sum(1 for p in all_projects if p.get("category") == "extractive"),
        "restorative":     sum(1 for p in all_projects if p.get("category") == "restorative"),
        "mixed":           sum(1 for p in all_projects if p.get("category") == "mixed"),
        "unclassified":    sum(1 for p in all_projects if not p.get("category")),
        "taking_comments": sum(1 for p in all_projects if p.get("accepting_comments")),
        "active":          sum(1 for p in all_projects if p.get("status") in ("In Progress", "Developing Proposal")),
        "newly_added":     sum(1 for p in all_projects if p.get("first_seen", "")[:10] >= recent_cutoff),
    }

    # Per-forest project counts for the summary bar
    forest_counts = {}
    for f in FORESTS:
        forest_projects = [p for p in all_projects if p.get("forest_code") == f["code"]]
        forest_counts[f["code"]] = {
            "total": len(forest_projects),
        }

    # Active = In Progress + Developing Proposal
    active_count = sum(
        1 for p in all_projects
        if p.get("status") in ("In Progress", "Developing Proposal")
    )

    if selected_forests:
        def matches_forest_filter(p, selected):
            if p.get('forest_code') in selected:
                return True
            if p.get('is_multi_forest') or p.get('forest_code') == 'multi':
                fn = p.get('forest_name', '')
                return any(
                    FOREST_CODE_TO_ABBREV.get(code, '') in fn
                    for code in selected
                )
            return False
        forest_visible = [p for p in all_projects if matches_forest_filter(p, selected_forests)]
    else:
        forest_visible = all_projects

    # Filtered counts based on forest selection, before category filter
    filtered_counts = {
        "extractive":      sum(1 for p in forest_visible if p.get("category") == "extractive"),
        "restorative":     sum(1 for p in forest_visible if p.get("category") == "restorative"),
        "mixed":           sum(1 for p in forest_visible if p.get("category") == "mixed"),
        "unclassified":    sum(1 for p in forest_visible if not p.get("category")),
        "taking_comments": sum(1 for p in forest_visible if p.get("accepting_comments")),
        "active":          sum(1 for p in forest_visible if p.get("status") in ("In Progress", "Developing Proposal")),
        "newly_added":     sum(1 for p in forest_visible if p.get("first_seen", "")[:10] >= recent_cutoff),
    }
    selected_category = selected_categories[0] if len(selected_categories) == 1 else ""

    projects = filter_projects(
        forest_visible,
        search=search,
        forest_code=selected_forest,
        status=selected_status,
        days=selected_days,
        categories=selected_categories,
        recent_cutoff=recent_cutoff,
        sort=selected_sort,
        sort2=selected_sort2,
    )

    status_list = sorted(set(p["status"] for p in all_projects if p.get("status")))

    selected_forest_name = ""
    for f in FORESTS:
        if f["code"] == selected_forest:
            selected_forest_name = f["name"]
            break

    def url_with_category(cat):
        from urllib.parse import urlencode
        cats = list(selected_categories)
        if cat in cats:
            cats.remove(cat)
        else:
            cats.append(cat)
        args = {}
        if search:                args["q"]       = search
        if selected_forest:       args["forest"]  = selected_forest
        if selected_status:       args["status"]  = selected_status
        if selected_days:         args["days"]    = selected_days
        if selected_sort:         args["sort"]    = selected_sort
        if selected_sort2:        args["sort2"]   = selected_sort2
        if selected_forests_str:  args["forests"] = selected_forests_str
        if cats:                  args["category"] = ",".join(cats)
        qs = urlencode(args)
        return f"/?{qs}" if qs else "/"

    return render_template_string(
        PAGE_TEMPLATE,
        projects=projects,
        forests=FORESTS,
        status_list=status_list,
        date_ranges=DATE_RANGES,
        search=search,
        selected_forest=selected_forest,
        selected_forest_name=selected_forest_name,
        selected_status=selected_status,
        selected_days=selected_days,
        selected_category=selected_category,
        selected_categories=selected_categories,
        selected_category_str=selected_category_str,
        selected_sort=selected_sort,
        selected_sort2=selected_sort2,
        status_colors=STATUS_COLORS,
        analysis_colors=ANALYSIS_COLORS,
        analysis_tooltips={
            "Categorical Exclusion": "Lowest rigor of analysis",
            "Environmental Assessment": "Medium rigor of analysis",
            "Environmental Impact Statement": "Highest rigor of analysis",
        },
        total=len(all_projects),
        last_scraped=last_scraped,
        recent_cutoff=recent_cutoff,
        counts=counts,
        filtered_counts=filtered_counts,
        forest_counts=forest_counts,
        state_columns=STATE_COLUMNS,
        state_colors=STATE_COLORS,
        forest_state_map=FOREST_STATE_MAP,
        selected_forests=selected_forests,
        selected_forests_str=selected_forests_str,
        toggle_forest_url=toggle_forest_url_fn,
        active_count=active_count,
        url_with_category=url_with_category,
        annotations=annotations,
        commented_urls=commented_urls,
        commented_urls_map=annotations.get("_commented_urls", {}),
        wildfire_urls=wildfire_urls,
        thinning_urls=thinning_urls,
        thinning_url="https://johnmuirproject.org/wp-content/uploads/2024/12/JMP-fact-sheet-thinning-and-fire-29Nov24.pdf",
        wildfire_url="https://www.forestclimatealliance.org/s/Final-Wildfire-in-the-Age-of-Climate-Change-compressed.pdf",
    )


# ── Annotations ──────────────────────────────────────────────

ANNOTATIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "annotations.json")


def load_annotations() -> dict:
    try:
        with open(ANNOTATIONS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_annotations_local(annotations: dict):
    with open(ANNOTATIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(annotations, f, indent=2, ensure_ascii=False)


def save_annotations_github(annotations: dict) -> bool:
    """Commit annotations.json to GitHub via the API. Returns True on success."""
    token  = os.environ.get("GITHUB_TOKEN")
    repo   = os.environ.get("GITHUB_REPO")   # e.g. "username/usfs-scraper"
    if not token or not repo:
        return False  # fall back to local only

    content = json.dumps(annotations, indent=2, ensure_ascii=False).encode("utf-8")
    encoded = base64.b64encode(content).decode("utf-8")

    api_url = f"https://api.github.com/repos/{repo}/contents/annotations.json"

    # Get current SHA (needed for update)
    sha = None
    try:
        req = urllib.request.Request(api_url, headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
        })
        with urllib.request.urlopen(req) as resp:
            sha = json.loads(resp.read())["sha"]
    except Exception:
        pass  # file doesn't exist yet — create it

    payload = {
        "message": f"Update annotations {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M')}",
        "content": encoded,
    }
    if sha:
        payload["sha"] = sha

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(api_url, data=data, method="PUT", headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(req):
            pass
        return True
    except Exception as e:
        print(f"GitHub commit failed: {e}")
        return False


ADMIN_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>LFDC Tracker Admin</title>
<style>
  body { font-family: 'Segoe UI', sans-serif; background: #f0f0ea; margin: 0; padding: 20px; color: #1a1a1a; }
  h1 { font-size: 1.3rem; font-weight: 600; margin-bottom: 6px; }
  h2 { font-size: 1.05rem; font-weight: 600; margin: 28px 0 10px 0; border-bottom: 2px solid #ccc; padding-bottom: 6px; max-width: 900px; }
  .subtitle { font-size: 0.8rem; color: #666; margin-bottom: 24px; }
  .project-list { display: flex; flex-direction: column; gap: 16px; max-width: 800px; }
  .project-card { background: white; border: 2px solid #e0c040; border-radius: 0; padding: 16px; }
  .project-name { font-weight: 600; font-size: 1rem; margin-bottom: 4px; }
  .forest-name { font-size: 0.78rem; color: #666; margin-bottom: 12px; }
  .deadline { font-size: 0.78rem; color: #cc1111; font-weight: 600; margin-bottom: 12px; }
  label { font-size: 0.72rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; color: #555; display: block; margin-bottom: 4px; }
  textarea { width: 100%; box-sizing: border-box; padding: 8px; font-family: inherit; font-size: 0.85rem; border: 1px solid #ccc; resize: vertical; min-height: 80px; }
  .save-btn { margin-top: 8px; padding: 6px 18px; background: #2d7a1f; color: white; border: none; font-size: 0.82rem; cursor: pointer; }
  .save-btn:hover { background: #1e5a12; }
  .no-tcn { color: #888; font-size: 0.9rem; margin-top: 20px; }
  .logout { float: right; font-size: 0.75rem; color: #888; text-decoration: none; }
  .logout:hover { color: #333; }
  .flash { background: #d4edda; border: 1px solid #2d7a1f; padding: 8px 14px; margin-bottom: 16px; font-size: 0.85rem; color: #1a4f0f; max-width: 900px; }
  .flash.error { background: #fde8e8; border-color: #cc1111; color: #7c0000; }

  /* LFDC Commented section */
  .commented-section { max-width: 900px; }
  .forest-accordion { margin-bottom: 6px; border: 1px solid #ddd; }
  .forest-accordion-header { width: 100%; text-align: left; background: #f0ede4; border: none; padding: 10px 14px; font-size: 0.88rem; font-weight: 600; cursor: pointer; display: flex; align-items: center; gap: 10px; font-family: inherit; color: #1a1a1a; }
  .forest-accordion-header:hover { background: #e8e4d8; }
  .acc-arrow { font-size: 0.7rem; color: #888; }
  .acc-count { margin-left: auto; font-size: 0.72rem; color: #888; font-weight: 400; }
  .forest-accordion-body { padding: 0; }
  .project-table { width: 100%; border-collapse: collapse; font-size: 0.78rem; }
  .project-table th { background: #f7f7f0; padding: 7px 10px; text-align: left; border-bottom: 2px solid #ddd; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.5px; color: #555; white-space: nowrap; }
  .project-table th.sortable { cursor: pointer; user-select: none; }
  .project-table th.sortable:hover { background: #ededde; }
  .sort-icon { font-size: 0.65rem; margin-left: 3px; }
  .project-table td { padding: 6px 10px; border-bottom: 1px solid #eee; vertical-align: middle; }
  .project-table tr:hover td { background: #faf9f4; }
  .project-table tr.new-project td { background: #fff8e6; }
  .project-table tr.new-project:hover td { background: #fff0cc; }
  .proj-name-cell { color: #1a1a1a; }
  .proj-date-cell { color: #666; white-space: nowrap; }
  .proj-check-cell { text-align: center; width: 60px; }
  .proj-check-cell input[type=checkbox] { width: 16px; height: 16px; cursor: pointer; accent-color: #c94f1a; }
  .proj-url-cell { min-width: 200px; }
  .comment-url-input { width: 100%; padding: 4px 6px; font-size: 0.72rem; border: 1px solid #ccc; box-sizing: border-box; font-family: inherit; }
  .auto-check { color: #2d7a1f; font-weight: 700; font-size: 1rem; cursor: default; }
  .save-commented-btn { margin-top: 16px; padding: 8px 24px; background: #c94f1a; color: white; border: none; font-size: 0.88rem; cursor: pointer; font-family: inherit; font-weight: 600; }
  .save-commented-btn:hover { background: #a33d12; }
</style>
</head>
<body>
<a href="/admin/logout" class="logout">Log out</a>
<h1>LFDC Tracker — Admin</h1>

{% if flash %}
<div class="flash {{ 'error' if flash_type == 'error' else '' }}">{{ flash }}</div>
{% endif %}

<!-- ── Section 1: Suggested Comments ── -->
<h2>💬 Suggested Comments (Projects Taking Comments Now)</h2>
<p class="subtitle">Add suggested comment text to projects currently accepting comments.</p>

{% if tcn_projects %}
<div class="project-list">
{% for p in tcn_projects %}
<div class="project-card">
  <div class="project-name">{{ p.project_name }}</div>
  <div class="forest-name">{{ p.forest_name }}</div>
  {% if p.comment_deadline %}<div class="deadline">Comments due: {{ p.comment_deadline }}</div>{% endif %}
  <form method="POST" action="/admin/save">
    <input type="hidden" name="project_url" value="{{ p.project_url }}">
    <label>Suggested Comment Text</label>
    <textarea name="annotation" placeholder="Enter suggested comment text for users to copy...">{{ annotations.get(p.project_url, {}).get('annotation', '') }}</textarea>
    <br>
    <label style="margin-top:10px;">Internal Notes (not shown to public)</label>
    <textarea name="notes" placeholder="Internal notes for LFDC staff only...">{{ annotations.get(p.project_url, {}).get('notes', '') }}</textarea>
    <br>
    <button type="submit" class="save-btn">Save</button>
  </form>
</div>
{% endfor %}
</div>
{% else %}
<p class="no-tcn">No projects are currently accepting comments.</p>
{% endif %}

<!-- ── Section 2: LFDC Commented ── -->
<h2>🟠 LFDC Commented</h2>
<p class="subtitle">Check projects where LFDC has submitted formal comments. Projects highlighted in amber were added in the last 72 hours.</p>

<form method="POST" action="/admin/save-commented">
<div class="commented-section">
{% for forest_name, forest_projects in all_projects_by_forest %}
<div class="forest-accordion">
  <button type="button" class="forest-accordion-header" onclick="
    var body = this.nextElementSibling;
    var isOpen = body.style.display !== 'none';
    body.style.display = isOpen ? 'none' : 'block';
    this.querySelector('.acc-arrow').innerText = isOpen ? '▶' : '▼';
  ">
    <span class="acc-arrow">▶</span>
    {{ forest_name }}
    <span class="acc-count">{{ forest_projects|length }} projects</span>
  </button>
  <div class="forest-accordion-body" style="display:none;">
    <table class="project-table" data-sort-col="1" data-sort-dir="desc">
      <thead>
        <tr>
          <th class="sortable" onclick="sortTable(this, 0)">Project <span class="sort-icon">↕</span></th>
          <th class="sortable" onclick="sortTable(this, 1)">Date Added <span class="sort-icon">↓</span></th>
          <th>Thinning Factsheet</th>
          <th>Wildfire Factsheet</th>
          <th>LFDC Commented</th>
          <th>Comment URL</th>
        </tr>
      </thead>
      <tbody>
        {% for p in forest_projects %}
        <tr class="{{ 'new-project' if p.get('first_seen','')[:10] >= recent_cutoff else '' }}">
          <td class="proj-name-cell">{{ p.project_name }}</td>
          <td class="proj-date-cell" data-date="{{ p.get('first_seen','')[:10] }}">{{ p.get('first_seen','')[:10] }}</td>
          <td class="proj-check-cell">
            {% if p.project_url in thinning_urls and p.project_url not in thinning_urls_manual %}
              <span class="auto-check" title="Auto: fuels management or forest products">✓</span>
            {% else %}
              <input type="checkbox" name="thinning" value="{{ p.project_url }}"
                     {{ 'checked' if p.project_url in thinning_urls_manual else '' }}>
            {% endif %}
          </td>
          <td class="proj-check-cell">
            {% if p.project_url in wildfire_urls and p.project_url not in wildfire_urls_manual %}
              <span class="auto-check" title="Auto: fuels management or vegetation management">✓</span>
            {% else %}
              <input type="checkbox" name="wildfire" value="{{ p.project_url }}"
                     {{ 'checked' if p.project_url in wildfire_urls_manual else '' }}>
            {% endif %}
          </td>
          <td class="proj-check-cell">
            <input type="checkbox" name="commented" value="{{ p.project_url }}"
                   {{ 'checked' if p.project_url in commented_urls else '' }}>
          </td>
          <td class="proj-url-cell">
            <input type="hidden" name="purl_{{ loop.index }}" value="{{ p.project_url }}">
            <input type="text" name="commented_url_{{ loop.index }}"
                   class="comment-url-input"
                   placeholder="https://..."
                   value="{{ commented_urls_map.get(p.project_url, '') }}">
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
{% endfor %}
</div>
<button type="submit" class="save-commented-btn">Save LFDC Commented List</button>
</form>

<script>
function sortTable(th, colIndex) {
  var table = th.closest('table');
  var tbody = table.querySelector('tbody');
  var rows = Array.from(tbody.querySelectorAll('tr'));
  var currentDir = table.dataset.sortDir === 'asc' && table.dataset.sortCol == colIndex ? 'desc' : 'asc';
  table.dataset.sortDir = currentDir;
  table.dataset.sortCol = colIndex;
  rows.sort(function(a, b) {
    var aVal = a.querySelectorAll('td')[colIndex].dataset.date || a.querySelectorAll('td')[colIndex].innerText.trim().toLowerCase();
    var bVal = b.querySelectorAll('td')[colIndex].dataset.date || b.querySelectorAll('td')[colIndex].innerText.trim().toLowerCase();
    return currentDir === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
  });
  rows.forEach(function(r) { tbody.appendChild(r); });
  table.querySelectorAll('th .sort-icon').forEach(function(icon) { icon.innerText = '↕'; });
  th.querySelector('.sort-icon').innerText = currentDir === 'asc' ? '↑' : '↓';
}
</script>

</body>
</html>
"""

ADMIN_LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>LFDC Admin Login</title>
<style>
  body { font-family: 'Segoe UI', sans-serif; background: #f0f0ea; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
  .box { background: white; padding: 32px; border: 1px solid #ccc; max-width: 320px; width: 100%; }
  h1 { font-size: 1.1rem; margin-bottom: 20px; }
  input[type=password] { width: 100%; box-sizing: border-box; padding: 8px; font-size: 0.9rem; border: 1px solid #ccc; margin-bottom: 12px; }
  button { padding: 8px 20px; background: #2d7a1f; color: white; border: none; font-size: 0.9rem; cursor: pointer; }
  .error { color: #cc1111; font-size: 0.82rem; margin-bottom: 10px; }
</style>
</head>
<body>
<div class="box">
  <h1>LFDC Tracker Admin</h1>
  {% if error %}<div class="error">Incorrect password.</div>{% endif %}
  <form method="POST">
    <input type="password" name="password" placeholder="Password" autofocus>
    <button type="submit">Log in</button>
  </form>
</div>
</body>
</html>
"""


@app.route("/admin", methods=["GET"])
def admin():
    if not session.get("admin_authed"):
        return redirect(url_for("admin_login"))
    projects, _ = load_projects()
    tcn_projects = [p for p in projects if p.get("accepting_comments")]
    annotations  = load_annotations()
    commented_urls = set(annotations.get("_commented", []))
    wildfire_urls_manual = set(annotations.get("_wildfire", []))
    thinning_urls_manual = set(annotations.get("_thinning", []))
    # Combine auto + manual
    wildfire_urls = wildfire_urls_manual | {p["project_url"] for p in projects if has_wildfire_badge(p)}
    thinning_urls = thinning_urls_manual | {p["project_url"] for p in projects if has_thinning_badge(p)}

    # Organize all projects by forest (in state order), then alphabetically by project name
    STATE_ORDER = ["WA", "OR", "CA+OR", "CA", "AK"]
    forests_in_order = []
    seen_forests = set()
    for state in STATE_ORDER:
        for p in projects:
            fn = p.get("forest_name", "")
            fs = FOREST_STATE_MAP.get(p.get("forest_code", ""), "")
            if fs == state and fn not in seen_forests:
                seen_forests.add(fn)
                forests_in_order.append(fn)

    by_forest = {}
    for p in projects:
        fn = p.get("forest_name", "")
        if fn not in by_forest:
            by_forest[fn] = []
        by_forest[fn].append(p)
    for fn in by_forest:
        by_forest[fn].sort(key=lambda p: p.get("project_name", "").lower())

    all_projects_by_forest = [(fn, by_forest[fn]) for fn in forests_in_order if fn in by_forest]

    admin_cutoff = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=72)
    ).strftime("%Y-%m-%d")

    # Also keep by_state for state labels
    STATE_ORDER_FULL = ["WA", "OR", "CA+OR", "CA", "AK"]
    by_state = {s: [] for s in STATE_ORDER_FULL}
    for p in projects:
        state = FOREST_STATE_MAP.get(p.get("forest_code", ""), "")
        if state in by_state:
            by_state[state].append(p)
    for state in by_state:
        by_state[state].sort(key=lambda p: (p.get("forest_name",""), p.get("project_name","").lower()))

    flash = request.args.get("flash", "")
    flash_type = request.args.get("flash_type", "")
    commented_urls_map = annotations.get("_commented_urls", {})
    wildfire_urls_manual = set(annotations.get("_wildfire", []))
    thinning_urls_manual = set(annotations.get("_thinning", []))
    # Combine auto + manual
    wildfire_urls = wildfire_urls_manual | {p["project_url"] for p in projects if has_wildfire_badge(p)}
    thinning_urls = thinning_urls_manual | {p["project_url"] for p in projects if has_thinning_badge(p)}
    thinning_urls = set(annotations.get("_thinning", []))
    return render_template_string(ADMIN_TEMPLATE,
        tcn_projects=tcn_projects,
        annotations=annotations,
        flash=flash,
        flash_type=flash_type,
        all_projects_by_state=by_state,
        all_projects_by_forest=all_projects_by_forest,
        commented_urls=commented_urls,
        commented_urls_map=commented_urls_map,
        wildfire_urls=wildfire_urls,
        thinning_urls=thinning_urls,
        thinning_url="https://johnmuirproject.org/wp-content/uploads/2024/12/JMP-fact-sheet-thinning-and-fire-29Nov24.pdf",
        wildfire_url="https://www.forestclimatealliance.org/s/Final-Wildfire-in-the-Age-of-Climate-Change-compressed.pdf",
        recent_cutoff=admin_cutoff,
    )


@app.route("/admin/save-commented", methods=["POST"])
def admin_save_commented():
    if not session.get("admin_authed"):
        return redirect(url_for("admin_login"))
    commented = request.form.getlist("commented")
    wildfire = request.form.getlist("wildfire")
    thinning = request.form.getlist("thinning")
    annotations = load_annotations()
    annotations["_commented"] = commented
    annotations["_wildfire"] = wildfire
    annotations["_thinning"] = thinning

    # Build URL map: purl_N -> project URL, commented_url_N -> the URL to link to
    commented_urls_map = {}
    for key, project_url in request.form.items():
        if key.startswith("purl_") and project_url.strip():
            idx = key[5:]
            link_url = request.form.get(f"commented_url_{idx}", "").strip()
            if link_url:
                commented_urls_map[project_url] = link_url

    annotations["_commented_urls"] = commented_urls_map
    save_annotations_local(annotations)
    github_ok = save_annotations_github(annotations)
    flash = "LFDC Commented list saved and committed to GitHub ✓" if github_ok else "Saved locally (GitHub token not configured)"
    return redirect(url_for("admin") + f"?flash={urllib.parse.quote(flash)}")
def admin_login():
    if request.method == "POST":
        password = request.form.get("password", "")
        admin_pw = os.environ.get("ADMIN_PASSWORD", "lfdc-admin")
        if password == admin_pw:
            session["admin_authed"] = True
            return redirect(url_for("admin"))
        return render_template_string(ADMIN_LOGIN_TEMPLATE, error=True)
    return render_template_string(ADMIN_LOGIN_TEMPLATE, error=False)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        password = request.form.get("password", "")
        admin_pw = os.environ.get("ADMIN_PASSWORD", "lfdc-admin")
        if password == admin_pw:
            session["admin_authed"] = True
            return redirect(url_for("admin"))
        return render_template_string(ADMIN_LOGIN_TEMPLATE, error=True)
    return render_template_string(ADMIN_LOGIN_TEMPLATE, error=False)


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_authed", None)
    return redirect(url_for("admin_login"))


@app.route("/admin/save", methods=["POST"])
def admin_save():
    if not session.get("admin_authed"):
        return redirect(url_for("admin_login"))

    project_url = request.form.get("project_url", "").strip()
    annotation  = request.form.get("annotation", "").strip()
    notes       = request.form.get("notes", "").strip()

    if not project_url:
        return redirect(url_for("admin"))

    annotations = load_annotations()
    if annotation or notes:
        annotations[project_url] = {
            "annotation": annotation,
            "notes":      notes,
            "updated":    datetime.datetime.utcnow().isoformat(),
        }
    elif project_url in annotations:
        del annotations[project_url]

    save_annotations_local(annotations)
    github_ok = save_annotations_github(annotations)

    flash = "Saved and committed to GitHub ✓" if github_ok else "Saved locally (GitHub token not configured)"
    return redirect(url_for("admin") + f"?flash={urllib.parse.quote(flash)}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting USFS NEPA Project Tracker on port {port}...")
    if port == 5000:
        print("Open your browser and go to: http://localhost:5000")
    print("Press Ctrl+C to stop.")
    app.run(host="0.0.0.0", port=port, debug=False)
