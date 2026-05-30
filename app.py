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
from flask import Flask, request, render_template_string

app = Flask(__name__, static_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static'))

STATUS_BORDER_COLORS = {
    "Developing Proposal": "#9b72d8",
    "In Progress":         "#4a90d9",
    "On Hold":             "#e08848",
    "Completed":           "#5aaa48",
}

CATEGORY_BG = {
    "extractive":  "rgba(204,17,17,0.18)",
    "restorative": "rgba(45,122,31,0.15)",
    "mixed":       "rgba(196,106,48,0.16)",
}

STATUS_COLORS = {
    "Developing Proposal": "#9b72d8",
    "In Progress":         "#4a90d9",
    "On Hold":             "#e08848",
    "Completed":           "#5aaa48",
}

ANALYSIS_COLORS = {
    "Categorical Exclusion":        "#cc1111",
    "Environmental Assessment":     "#c46a30",
    "Environmental Impact Statement": "#2d7a1f",
}

FOREST_ABBREVS = {
    "Mt. Baker-Snoqualmie National Forest":  "MBS",
    "Olympic National Forest":               "ONF",
    "Okanogan-Wenatchee National Forest":    "Okan-Wen",
    "Gifford Pinchot National Forest":       "GPNF",
    "Colville National Forest":              "Colville",
    "Rogue River-Siskiyou National Forest":  "RRS",
    "Wallowa-Whitman National Forest":       "Wallowa-Whitman",
    "Fremont-Winema National Forest":        "Fremont-Winema",
    "Shasta-Trinity National Forest":        "Shasta-Trinity",
    "Inyo National Forest":                  "Inyo",
    "Los Padres National Forest":            "Los Padres",
    "Klamath National Forest":               "Klamath",
    "Tongass National Forest":               "Tongass",
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

# Colors for each state column
STATE_COLORS = {
    "CA":    {"pill": "#cc3333", "label": "#8b1a1a"},
    "CA+OR": {"pill": "#c96a00", "label": "#7a3e00"},
    "OR":    {"pill": "#b8960a", "label": "#6b5500"},
    "OR+WA": {"pill": "#7a9a2f", "label": "#445a18"},
    "WA":    {"pill": "#2d7a1f", "label": "#1a4f0f"},
    "AK":    {"pill": "#5b4fa8", "label": "#352d6e"},
}


DATE_RANGES = [
    ("7",  "Last 7 days"),
    ("30", "Last 30 days"),
    ("90", "Last 90 days"),
]

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
    return projects, scraped_at


def filter_projects(projects, search="", forest_code="", status="",
                    days="", category="", sort="", sort2=""):
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
            # Also include multi-forest projects that contain this forest
            if not (p.get("is_multi_forest") and forest_code in p.get("forest_name", "")):
                continue
        if status and p.get("status") != status:
            continue
        if category:
            if category == "unclassified":
                if p.get("category"):
                    continue
            elif category == "taking_comments":
                if not p.get("accepting_comments"):
                    continue
            elif p.get("category") != category:
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

    # Analysis type sort order: EIS (highest) → EA → CatEx (lowest) → blank
    ANALYSIS_SORT_ORDER = {
        "Environmental Impact Statement": 0,
        "Environmental Assessment":       1,
        "Categorical Exclusion":          2,
    }

    if sort == "newest":
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
        results.sort(key=lambda p: (
            STATUS_SORT_ORDER.get(p.get("status", ""), 99),
            CATEGORY_SORT_ORDER.get(p.get("category", ""), 3),
        ))

    # Impact category sort order
    IMPACT_SORT_ORDER = {
        "extractive":  0,
        "mixed":       1,
        "restorative": 2,
        None:          3,
    }

    if sort == "impact":
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
            STATUS_SORT_ORDER2 = {
                "Developing Proposal": 0,
                "In Progress":         1,
                "On Hold":             2,
                "Completed":           3,
            }
            results.sort(key=lambda p: STATUS_SORT_ORDER2.get(p.get("status", ""), 99))
        elif sort2 == "impact":
            results.sort(key=lambda p: IMPACT_SORT_ORDER.get(p.get("category"), 3))
        elif sort2 == "analysis":
            ANALYSIS_SORT_ORDER2 = {
                "Environmental Impact Statement": 0,
                "Environmental Assessment":       1,
                "Categorical Exclusion":          2,
            }
            results.sort(key=lambda p: ANALYSIS_SORT_ORDER2.get(p.get("analysis_type", ""), 99))

    return results


PAGE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>National Forest NEPA Project Tracker</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Lexend:wght@400;500;600;700&family=Outfit:wght@400;500;600&display=swap" rel="stylesheet">
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
            font-family: 'Lexend', sans-serif;
            background: var(--bg);
            color: var(--text);
            font-size: 14px;
            line-height: 1.6;
        }

        /* ── Header ── */
        header {
            position: relative;
            height: 140px;
            overflow: hidden;
            display: flex;
            align-items: stretch;
            background: transparent;
        }

        .header-bg-img {
            display: none;
        }

        .header-overlay {
            position: relative;
            z-index: 1;
            width: 100%;
            max-width: 1150px;
            margin: 0 auto;
            padding: 16px 20px;
            display: flex;
            flex-direction: column;
            align-items: flex-start;
            justify-content: space-between;
        }

        .header-title {
            font-family: 'Outfit', sans-serif;
            font-size: 1.6rem;
            font-weight: 300;
            color: #1a1a1a;
            letter-spacing: 0.3px;
            text-align: center;
            width: 100%;
            text-shadow: none;
        }

        .header-search-row {
            width: 100%;
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
            font-family: 'Lexend', sans-serif;
            font-size: 0.88rem;
            background: white;
            color: #1a1a1a;
            outline: none;
        }

        .header-search input[type="text"]::placeholder { color: #aaa; }
        .header-search input[type="text"]:focus { border-color: #888; }

        .header-search button {
            padding: 7px 18px;
            background: #2d4a24;
            color: white;
            border: none;
            border-radius: 0;
            font-family: 'Lexend', sans-serif;
            font-size: 0.88rem;
            font-weight: 700;
            cursor: pointer;
            white-space: nowrap;
        }

        .header-search button:hover { background: #1e3a12; }

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
            justify-content: flex-end;
            width: 100%;
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
            font-weight: 600;
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
        .filters {
            background: var(--bg2);
            border: 1px solid var(--border);
            border-radius: 0;
            padding: 14px 18px;
            margin-bottom: 10px;
            display: flex;
            gap: 12px;
            align-items: flex-end;
            flex-wrap: wrap;
            justify-content: flex-end;
        }

        .filters label {
            display: block;
            font-size: 0.62rem;
            font-weight: 700;
            color: var(--text-dim);
            margin-bottom: 5px;
            text-transform: uppercase;
            letter-spacing: 0.8px;
        }

        .filters select {
            padding: 7px 11px;
            border: 1px solid var(--border2);
            border-radius: 0;
            font-family: 'Lexend', sans-serif;
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
            font-family: 'Lexend', sans-serif;
            font-size: 0.78rem;
            font-weight: 700;
            cursor: pointer;
            text-decoration: none;
            transition: all 0.15s;
            letter-spacing: 0.2px;
        }

        .cat-btn .dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }

        .cat-btn.unclassified { border-color: #888; color: #555; background: rgba(128,128,128,0.07); }
        .cat-btn.unclassified.active { background: #888; color: white; border: 3px solid #888; }
        .cat-btn .dot.unclassified-dot { background: #888; }
        .cat-btn.taking-comments { border-color: #cc1111; color: #cc1111; background: rgba(251,191,36,0.15); }
        .cat-btn.taking-comments.active { background: #fbbf24; color: #7c2d12; border: 3px solid #cc1111; }
        .cat-btn .dot.taking-comments-dot { background: #fbbf24; border: 1px solid #cc1111; }

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
        .cat-btn.restorative { border-color: var(--green);  color: var(--green);  background: rgba(45,122,31,0.07); }
        .cat-btn.mixed       { border-color: var(--orange); color: var(--orange); background: rgba(196,106,48,0.07); }

        .cat-btn.extractive.active  { background: rgba(204,17,17,0.07);   border: 3px solid var(--red);    color: var(--red); }
        .cat-btn.restorative.active { background: rgba(45,122,31,0.07);  border: 3px solid var(--green);  color: var(--green); }
        .cat-btn.mixed.active       { background: rgba(196,106,48,0.07); border: 3px solid var(--orange); color: var(--orange); }

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
            margin-bottom: 4px;
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
            width: 240px;
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
            width: 240px;
            text-align: center;
            box-sizing: border-box;
        }

        /* Taking Comments Now badge */
        .comment-open-badge {
            display: inline-flex;
            flex-direction: column;
            align-items: center;
            padding: 8px 18px;
            border-radius: 0;
            background: #fbbf24;
            border: 2px solid #cc1111;
            color: #7c2d12;
            font-weight: 700;
            font-size: 0.82rem;
            line-height: 1.4;
            text-align: center;
            animation: pulse-yellow 2.5s ease-in-out infinite;
            flex-shrink: 0;
            box-shadow: 0 2px 8px rgba(204,17,17,0.2);
            width: 480px;
            box-sizing: border-box;
        }

        .comment-open-badge .badge-title {
            font-size: 0.88rem;
            font-weight: 800;
            letter-spacing: 0.4px;
        }

        .comment-open-badge .badge-deadline {
            font-size: 0.72rem;
            font-weight: 600;
            opacity: 0.9;
            margin-top: 2px;
        }

        @keyframes pulse-yellow {
            0%, 100% { opacity: 1; box-shadow: 0 2px 8px rgba(204,17,17,0.2); }
            50%       { opacity: 0.8; box-shadow: 0 2px 16px rgba(204,17,17,0.4); }
        }

        .new-badge {
            display: inline-block;
            background: #fbbf24;
            color: #7c2d12;
            border: 2px solid #cc1111;
            border-radius: 0;
            font-size: 0.82rem;
            font-weight: 700;
            padding: 3px 8px;
            vertical-align: middle;
            margin-left: 6px;
            letter-spacing: 0.3px;
            box-shadow: 0 2px 8px rgba(204,17,17,0.2);
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
            width: 240px;
        }

        .card-body-right .status-badge,
        .card-body-right .analysis-badge,
        .card-body-right .milestone-section {
            width: 240px;
            box-sizing: border-box;
        }

        /* ── Milestone table ── */
        .milestone-section {
            width: 240px;
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

        .btn-title {
            display: inline-flex;
            align-items: center;
            padding: 5px 12px;
            border-radius: 0;
            font-family: 'Lexend', sans-serif;
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
            font-family: 'Lexend', sans-serif;
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
            background: #d8d8d4;
            color: #555;
            border: 2px solid var(--green);
        }

        .btn-comment.primary {
            border: 1px solid var(--green);
        }

        .btn-comment.secondary:hover {
            background: #c8c8c4;
            color: #333;
            border-color: var(--green);
        }

        .btn-comment.primary-inactive {
            background: #d8d8d4 !important;
            color: #555 !important;
            border: 1px solid #b8b8b4 !important;
            cursor: pointer;
        }

        .btn-comment.primary-inactive:hover {
            background: #c8c8c4 !important;
            color: #444 !important;
        }

        /* ── Mobile layout ── */
        @media (max-width: 680px) {

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
            .project-card { padding: 12px 14px; }

            /* ── Card layout: full vertical stack ── */

            /* Comment badge — top, left justified */
            .comment-open-badge {
                align-self: flex-start;
                margin-bottom: 6px;
            }

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

            /* Comment badge centered on mobile */
            .mobile-only.comment-open-badge {
                align-self: center;
                margin: 0 auto 8px auto;
                width: fit-content;
            }

            /* Mobile: single column */
            .project-card {
                display: flex !important;
                flex-direction: column !important;
            }

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

<header>
    <img src="/static/forest_banner.jpg" alt="" class="header-bg-img">
    <div class="header-overlay">
        <div class="header-title">National Forest NEPA Project Tracker</div>
        <div class="header-search-row">
            <form class="header-search" method="GET" action="/" id="searchform">
                <input type="hidden" name="forest"   value="{{ selected_forest }}">
                <input type="hidden" name="status"   value="{{ selected_status }}">
                <input type="hidden" name="days"     value="{{ selected_days }}">
                <input type="hidden" name="sort"     value="{{ selected_sort }}">
                <input type="hidden" name="sort2"    value="{{ selected_sort2 }}">
                <input type="hidden" name="category" value="{{ selected_category }}">
                <input type="hidden" name="forests"  value="{{ selected_forests_str }}">
                <input type="text" name="q"
                       placeholder="Search projects..."
                       value="{{ search }}"
                       autocomplete="off">
                <button type="submit">Search</button>
            </form>
        </div>
    </div>
</header>

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
                    <span class="forest-pill" style="background:{{ sc.get('pill','var(--accent)') }};">
                        {{ f.name.replace('National Forest', 'NF') }}
                        <span class="forest-pill-count">{{ forest_counts[f.code].total }}</span>
                    </span>
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
            <div class="forest-col desktop-only">
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
                <strong>{{ total }}</strong> total &nbsp;·&nbsp; <strong>{{ active_count }}</strong> active / planning
            </span>
        </div>
    </div>
</div>

<div class="container">

    <form class="filters" method="GET" action="/">
        <input type="hidden" name="q"        value="{{ search }}">
        <input type="hidden" name="category" value="{{ selected_category }}">
        <input type="hidden" name="sort2"    value="{{ selected_sort2 }}">
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
                <option value="">Default</option>
                <option value="newest"   {% if selected_sort == "newest"   %}selected{% endif %}>Newest first</option>
                <option value="oldest"   {% if selected_sort == "oldest"   %}selected{% endif %}>Oldest first</option>
                <option value="name"     {% if selected_sort == "name"     %}selected{% endif %}>Project name A–Z</option>
                <option value="forest"   {% if selected_sort == "forest"   %}selected{% endif %}>Forest</option>
                <option value="analysis" {% if selected_sort == "analysis" %}selected{% endif %}>Analysis type</option>
                <option value="status"   {% if selected_sort == "status"   %}selected{% endif %}>Status</option>
                <option value="impact"   {% if selected_sort == "impact"   %}selected{% endif %}>Impact category</option>
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
                <option value="analysis" {% if selected_sort2 == "analysis" %}selected{% endif %}>Analysis type</option>
            </select>
        </div>
        {% if search or selected_forest or selected_status or selected_days or selected_category or selected_sort or selected_sort2 %}
        <a class="clear" href="/">Clear all</a>
        {% endif %}
    </form>

    <div class="category-filters">
        <span>Show only:</span>
        <a href="{{ url_with_category('extractive') }}"
           class="cat-btn extractive {{ 'active' if selected_category == 'extractive' else '' }}">
            <span class="dot extractive-dot"></span>
            Significant Effect ({{ filtered_counts.extractive }} of {{ counts.extractive }})
        </a>
        <a href="{{ url_with_category('mixed') }}"
           class="cat-btn mixed {{ 'active' if selected_category == 'mixed' else '' }}">
            <span class="dot mixed-dot"></span>
            Mixed Impact ({{ filtered_counts.mixed }} of {{ counts.mixed }})
        </a>
        <a href="{{ url_with_category('restorative') }}"
           class="cat-btn restorative {{ 'active' if selected_category == 'restorative' else '' }}">
            <span class="dot restorative-dot"></span>
            Restorative Impact ({{ filtered_counts.restorative }} of {{ counts.restorative }})
        </a>
        <a href="{{ url_with_category('unclassified') }}"
           class="cat-btn unclassified {{ 'active' if selected_category == 'unclassified' else '' }}">
            <span class="dot unclassified-dot"></span>
            Unknown ({{ filtered_counts.unclassified }} of {{ counts.unclassified }})
        </a>
        <a href="{{ url_with_category('taking_comments') }}"
           class="cat-btn taking-comments {{ 'active' if selected_category == 'taking_comments' else '' }}">
            <span class="dot taking-comments-dot"></span>
            💬 Taking Comments Now ({{ filtered_counts.taking_comments }} of {{ counts.taking_comments }})
        </a>
    </div>
    <div class="category-disclaimer-row">
        <span class="category-disclaimer">Impact level assigned based on keywords and intended as a general guide only</span>
    </div>

    <div class="results-header">
        {% set cat_labels = {'extractive': 'Significant Effect', 'mixed': 'Mixed Impact', 'restorative': 'Restorative Impact', 'unclassified': 'Unknown', 'taking_comments': 'Taking Comments Now'} %}
        {% if search or selected_forest or selected_status or selected_days or selected_category %}
            Showing <strong>{{ projects|length }}</strong> result{% if projects|length != 1 %}s{% endif %}
            {% if selected_category %} — <strong>{{ cat_labels.get(selected_category, selected_category) }}</strong>{% endif %}
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
        {% set status_color = status_border_colors.get(p.status, '#d0d0c8') %}
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
                    <div class="forest-tag">{{ p.forest_name }}</div>
                    <div class="btn-title-wrap">
                        <a href="{{ p.project_url }}" target="_blank" class="btn-title">
                            {{ p.project_name }}
                        </a>
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
                            {% if p.get('analysis_type') %}
                            <span class="analysis-badge"
                                  style="background: {{ analysis_colors.get(p.analysis_type, '#888') }}; color:white; border-color:transparent; width:auto;"
                                  title="{{ analysis_tooltips.get(p.analysis_type, '') }}">
                                {{ p.analysis_type }}
                            </span>
                            {% endif %}
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
                        {% if has_milestones %}
                        {% set project_id = p.project_url.rstrip('/').split('/')[-1] %}
                        <div class="comment-buttons">
                            <a class="btn-comment {{ 'primary' if p.get('accepting_comments') else 'primary-inactive' }}"
                               href="https://cara.fs2c.usda.gov/Public/CommentInput?Project={{ project_id }}"
                               target="_blank" rel="noopener">{{ '✍️ ' if p.get('accepting_comments') else '' }}Submit New Comments</a>
                            <a class="btn-comment secondary"
                               href="https://cara.fs2c.usda.gov/Public/ReadingRoom?Project={{ project_id }}"
                               target="_blank" rel="noopener">📖 View Prior Comments</a>
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
                    {% if p.status %}
                    <span class="status-badge" style="background: {{ status_colors.get(p.status, '#8892a4') }}">
                        {{ p.status }}
                    </span>
                    {% endif %}
                    {% if p.get('analysis_type') %}
                    <span class="analysis-badge"
                          style="background: {{ analysis_colors.get(p.analysis_type, '#888') }}; color:white; border-color:transparent;"
                          title="{{ analysis_tooltips.get(p.analysis_type, '') }}">
                        {{ p.analysis_type }}
                    </span>
                    {% endif %}
                    {% if has_milestones %}
                    <div class="milestone-section">
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

</body>
<script>

</script>
</html>
"""


def toggle_forest_url_fn(code, current_str):
    """Return URL with the given forest code toggled in the forests param."""
    from flask import request as req
    current = [f.strip() for f in current_str.split(",") if f.strip()]
    if code in current:
        new = [c for c in current if c != code]
    else:
        new = current + [code]
    args = dict(req.args)
    if new:
        args["forests"] = ",".join(new)
    elif "forests" in args:
        del args["forests"]
    from urllib.parse import urlencode
    return "/?" + urlencode(args) if args else "/"


@app.route("/")
def index():
    search            = request.args.get("q", "").strip()
    selected_forests_str = request.args.get("forests", "").strip()
    selected_forests     = [f.strip() for f in selected_forests_str.split(",") if f.strip()]
    selected_forest   = request.args.get("forest", "").strip()
    selected_status   = request.args.get("status", "").strip()
    selected_days     = request.args.get("days", "").strip()
    selected_category = request.args.get("category", "taking_comments").strip()
    selected_sort     = request.args.get("sort", "").strip()
    selected_sort2    = request.args.get("sort2", "").strip()

    all_projects, last_scraped = load_projects()

    counts = {
        "extractive":      sum(1 for p in all_projects if p.get("category") == "extractive"),
        "restorative":     sum(1 for p in all_projects if p.get("category") == "restorative"),
        "mixed":           sum(1 for p in all_projects if p.get("category") == "mixed"),
        "unclassified":    sum(1 for p in all_projects if not p.get("category")),
        "taking_comments": sum(1 for p in all_projects if p.get("accepting_comments")),
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
        forest_visible = [p for p in all_projects if p.get('forest_code') in selected_forests]
    else:
        forest_visible = all_projects

    # Filtered counts based on forest selection, before category filter
    filtered_counts = {
        "extractive":      sum(1 for p in forest_visible if p.get("category") == "extractive"),
        "restorative":     sum(1 for p in forest_visible if p.get("category") == "restorative"),
        "mixed":           sum(1 for p in forest_visible if p.get("category") == "mixed"),
        "unclassified":    sum(1 for p in forest_visible if not p.get("category")),
        "taking_comments": sum(1 for p in forest_visible if p.get("accepting_comments")),
    }

    projects = filter_projects(
        forest_visible,
        search=search,
        forest_code=selected_forest,
        status=selected_status,
        days=selected_days,
        category=selected_category,
        sort=selected_sort,
        sort2=selected_sort2,
    )

    status_list = sorted(set(p["status"] for p in all_projects if p.get("status")))

    selected_forest_name = ""
    for f in FORESTS:
        if f["code"] == selected_forest:
            selected_forest_name = f["name"]
            break

    recent_cutoff = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)
    ).strftime("%Y-%m-%d")

    def url_with_category(cat):
        args = {}
        if search:                args["q"]       = search
        if selected_forest:       args["forest"]  = selected_forest
        if selected_status:       args["status"]  = selected_status
        if selected_days:         args["days"]    = selected_days
        if selected_sort:         args["sort"]    = selected_sort
        if selected_forests_str:  args["forests"] = selected_forests_str
        if selected_category != cat:
            args["category"] = cat
        from urllib.parse import urlencode
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
        selected_sort=selected_sort,
        selected_sort2=selected_sort2,
        status_colors=STATUS_COLORS,
        status_border_colors=STATUS_BORDER_COLORS,
        forest_abbrevs=FOREST_ABBREVS,
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
        selected_forests=selected_forests,
        selected_forests_str=selected_forests_str,
        toggle_forest_url=toggle_forest_url_fn,
        active_count=active_count,
        url_with_category=url_with_category,
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting USFS NEPA Project Tracker on port {port}...")
    if port == 5000:
        print("Open your browser and go to: http://localhost:5000")
    print("Press Ctrl+C to stop.")
    app.run(host="0.0.0.0", port=port, debug=False)
