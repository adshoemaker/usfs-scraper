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
    # Washington
    {"name": "Mt. Baker-Snoqualmie National Forest", "code": "mbs"},
    {"name": "Olympic National Forest",              "code": "olympic"},
    {"name": "Okanogan-Wenatchee National Forest",   "code": "okanogan-wenatchee"},
    {"name": "Gifford Pinchot National Forest",      "code": "giffordpinchot"},
    {"name": "Colville National Forest",             "code": "colville"},
    # Oregon
    {"name": "Rogue River-Siskiyou National Forest", "code": "rogue-siskiyou"},
    {"name": "Wallowa-Whitman National Forest",      "code": "wallowa-whitman"},
    {"name": "Fremont-Winema National Forest",       "code": "fremont-winema"},
    # California
    {"name": "Shasta-Trinity National Forest",       "code": "shasta-trinity"},
    {"name": "Inyo National Forest",                 "code": "inyo"},
    {"name": "Los Padres National Forest",           "code": "lospadres"},
    {"name": "Klamath National Forest",              "code": "klamath"},
    # Alaska
    {"name": "Tongass National Forest",              "code": "tongass"},
]

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
                    days="", category="", sort=""):
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
        if category and p.get("category") != category:
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
        results.sort(key=lambda p: STATUS_SORT_ORDER.get(p.get("status", ""), 99))

    # Always pin projects currently accepting comments to the top
    results.sort(key=lambda p: 0 if p.get("accepting_comments") else 1)

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
            background: #3a5c30;
            border-bottom: 2px solid #2d4a24;
            padding: 12px 30px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 20px;
            flex-wrap: wrap;
        }

        .header-left {
            display: flex;
            align-items: center;
            gap: 14px;
        }

        .header-logo {
            height: 52px;
            width: 52px;
            border-radius: 6px;
            object-fit: cover;
            border: 2px solid rgba(255,255,255,0.2);
            flex-shrink: 0;
        }

        header h1 {
            font-size: 1.43rem;
            font-weight: 500;
            font-family: 'Outfit', sans-serif;
            color: white;
            letter-spacing: 0.3px;
            line-height: 1.2;
        }

        /* Forest summary bar */
        .forest-summary {
            background: #f7f7f0;
            border-bottom: 1px solid var(--border);
            padding: 8px 30px;
        }

        .forest-summary-inner {
            max-width: 1150px;
            margin: 0 auto;
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
            font-size: 0.75rem;
        }

        .tracking-label {
            font-weight: 700;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            font-size: 0.68rem;
            margin-right: 4px;
            white-space: nowrap;
        }

        .forest-pill {
            display: inline-flex;
            align-items: center;
            gap: 5px;
            background: white;
            border: 1px solid var(--border);
            border-radius: 20px;
            padding: 2px 10px 2px 8px;
            font-size: 0.72rem;
            font-weight: 500;
            color: var(--text);
            white-space: nowrap;
        }

        .forest-pill-count {
            background: var(--border);
            border-radius: 10px;
            padding: 0px 6px;
            font-size: 0.65rem;
            font-weight: 700;
            color: var(--text-muted);
        }

        .summary-totals {
            margin-left: auto;
            color: var(--text-muted);
            font-size: 0.72rem;
            white-space: nowrap;
        }

        .summary-totals strong {
            color: var(--text);
            font-weight: 700;
        }

        .header-search {
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .header-search input[type="text"] {
            padding: 7px 14px;
            border: 1px solid rgba(255,255,255,0.25);
            border-radius: 6px;
            font-family: 'Lexend', sans-serif;
            font-size: 0.85rem;
            width: 230px;
            background: rgba(255,255,255,0.12);
            color: white;
            outline: none;
            transition: border-color 0.2s;
        }

        .header-search input[type="text"]::placeholder { color: rgba(255,255,255,0.5); }
        .header-search input[type="text"]:focus { border-color: rgba(255,255,255,0.6); }

        .header-search button {
            padding: 7px 14px;
            background: rgba(255,255,255,0.18);
            color: white;
            border: 1px solid rgba(255,255,255,0.25);
            border-radius: 6px;
            font-family: 'Lexend', sans-serif;
            font-size: 0.82rem;
            font-weight: 700;
            cursor: pointer;
            transition: background 0.15s;
        }

        .header-search button:hover { background: rgba(255,255,255,0.28); }

        /* ── Container ── */
        .container {
            max-width: 1150px;
            margin: 0 auto;
            padding: 20px;
        }

        /* ── Filter bar ── */
        .filters {
            background: var(--bg2);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 14px 18px;
            margin-bottom: 10px;
            display: flex;
            gap: 12px;
            align-items: flex-end;
            flex-wrap: wrap;
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
            border-radius: 6px;
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
            border-left: 4px solid var(--border);
            border-radius: 10px;
            padding: 16px 18px;
            margin-bottom: 10px;
            transition: border-color 0.15s, box-shadow 0.15s;
        }

        .project-card:hover {
            border-color: var(--border2);
            box-shadow: 0 2px 10px rgba(0,0,0,0.10);
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
            font-size: 0.65rem;
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
            display: inline-block;
            padding: 3px 10px;
            border-radius: 20px;
            font-size: 0.65rem;
            font-weight: 700;
            color: #0f1117;
            white-space: nowrap;
            flex-shrink: 0;
            letter-spacing: 0.3px;
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
        }

        /* Taking Comments Now badge */
        .comment-open-badge {
            display: inline-flex;
            flex-direction: column;
            align-items: center;
            padding: 6px 14px;
            border-radius: 6px;
            background: #fbbf24;
            border: 2px solid #f59e0b;
            color: #7c2d12;
            font-weight: 700;
            font-size: 0.78rem;
            line-height: 1.3;
            text-align: center;
            animation: pulse-yellow 2s ease-in-out infinite;
            flex-shrink: 0;
        }

        .comment-open-badge .badge-title {
            font-size: 0.82rem;
            font-weight: 800;
            letter-spacing: 0.3px;
        }

        .comment-open-badge .badge-deadline {
            font-size: 0.68rem;
            font-weight: 600;
            opacity: 0.85;
            margin-top: 1px;
        }

        @keyframes pulse-yellow {
            0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(251,191,36,0.5); }
            50%       { opacity: 0.75; box-shadow: 0 0 0 6px rgba(251,191,36,0); }
        }

        /* Extra red outline for cards taking comments */
        .project-card.taking-comments {
            outline: 3px solid #cc1111;
            outline-offset: 2px;
        }

        .new-badge {
            display: inline-block;
            background: rgba(251,191,36,0.15);
            color: #fbbf24;
            border: 1px solid rgba(251,191,36,0.4);
            border-radius: 4px;
            font-size: 0.62rem;
            font-weight: 700;
            padding: 1px 5px;
            vertical-align: middle;
            margin-left: 6px;
            letter-spacing: 0.5px;
        }

        /* ── Card body layout ── */
        .card-body {
            display: grid;
            grid-template-columns: 1fr 310px;
            grid-template-rows: auto 1fr;
            gap: 10px 16px;
            margin-top: 6px;
            min-height: 60px;
        }

        .card-body .description {
            grid-column: 1;
            grid-row: 1;
            font-size: 0.82rem;
            color: var(--text-muted);
            line-height: 1.6;
            font-weight: 400;
            align-self: start;
        }

        /* ── Milestone table ── */
        .milestone-section {
            grid-column: 2;
            grid-row: 1 / 3;
            align-self: end;
            justify-self: end;
            width: 310px;
            border: 1px solid var(--border2);
            border-radius: 6px;
            overflow: hidden;
            background: #e8e8e4;
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
            border-radius: 5px;
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
            border-radius: 5px;
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
            background: transparent;
            color: var(--text-muted);
            border: 1px solid var(--border2);
        }

        .btn-comment.primary {
            border: 1px solid var(--green);
        }

        .btn-comment.secondary:hover {
            color: var(--text);
            border-color: var(--text-dim);
        }

        /* ── Mobile layout ── */
        @media (max-width: 680px) {
            .card-body {
                display: flex;
                flex-direction: column;
            }

            .milestone-section {
                width: 100%;
                order: 2;
                grid-column: unset;
                grid-row: unset;
            }

            .card-body .description {
                order: 1;
                grid-column: unset;
                grid-row: unset;
            }

            .comment-buttons {
                order: 3;
            }

            header {
                flex-direction: column;
                align-items: flex-start;
                gap: 10px;
                padding: 12px 16px;
            }

            .header-search input[type="text"] {
                width: 100%;
            }

            .header-search {
                width: 100%;
            }

            .forest-summary-inner {
                gap: 6px;
            }

            .filters {
                gap: 8px;
            }

            .filters select {
                width: 100%;
            }

            .container {
                padding: 12px;
            }

            .project-card {
                padding: 12px 14px;
            }

            .summary-totals {
                margin-left: 0;
                width: 100%;
            }
        }

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
    <div class="header-left">
        <img src="/static/LFDC_Logo.jpg" alt="LFDC Logo" class="header-logo">
        <h1>National Forest NEPA Project Tracker</h1>
    </div>
    <form class="header-search" method="GET" action="/" id="searchform">
        <input type="hidden" name="forest"   value="{{ selected_forest }}">
        <input type="hidden" name="status"   value="{{ selected_status }}">
        <input type="hidden" name="days"     value="{{ selected_days }}">
        <input type="hidden" name="sort"     value="{{ selected_sort }}">
        <input type="hidden" name="category" value="{{ selected_category }}">
        <input type="text" name="q"
               placeholder="Search projects..."
               value="{{ search }}"
               autocomplete="off">
        <button type="submit">Search</button>
    </form>
</header>

<!-- Forest summary bar -->
<div class="forest-summary">
    <div class="forest-summary-inner">
        <span class="tracking-label">Currently tracking:</span>
        {% for f in forests %}
        <span class="forest-pill">
            {{ f.name.replace('National Forest', 'NF') }}
            <span class="forest-pill-count">{{ forest_counts[f.code].total }}</span>
        </span>
        {% endfor %}
        <span class="summary-totals">
            <strong>{{ total }}</strong> projects total
            &nbsp;·&nbsp;
            <strong>{{ active_count }}</strong> active / planning
        </span>
    </div>
</div>

<div class="container">

    <form class="filters" method="GET" action="/">
        <input type="hidden" name="q"        value="{{ search }}">
        <input type="hidden" name="category" value="{{ selected_category }}">
        <div>
            <label for="forest">Forest</label>
            <select id="forest" name="forest" onchange="this.form.submit()">
                <option value="">All forests</option>
                {% for f in forests %}
                <option value="{{ f.code }}"
                    {% if selected_forest == f.code %}selected{% endif %}>
                    {{ f.name.replace('National Forest', 'NF') }}
                </option>
                {% endfor %}
            </select>
        </div>
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
            </select>
        </div>
        {% if search or selected_forest or selected_status or selected_days or selected_category or selected_sort %}
        <a class="clear" href="/">Clear all</a>
        {% endif %}
    </form>

    <div class="category-filters">
        <span>Show only:</span>
        <a href="{{ url_with_category('extractive') }}"
           class="cat-btn extractive {{ 'active' if selected_category == 'extractive' else '' }}">
            <span class="dot extractive-dot"></span>
            Extractive ({{ counts.extractive }})
        </a>
        <a href="{{ url_with_category('mixed') }}"
           class="cat-btn mixed {{ 'active' if selected_category == 'mixed' else '' }}">
            <span class="dot mixed-dot"></span>
            Mixed ({{ counts.mixed }})
        </a>
        <a href="{{ url_with_category('restorative') }}"
           class="cat-btn restorative {{ 'active' if selected_category == 'restorative' else '' }}">
            <span class="dot restorative-dot"></span>
            Restorative ({{ counts.restorative }})
        </a>
    </div>

    <div class="legend">
        <div class="legend-item"><div class="legend-stripe" style="background:var(--red)"></div> Extractive</div>
        <div class="legend-item"><div class="legend-stripe" style="background:var(--green)"></div> Restorative</div>
        <div class="legend-item"><div class="legend-stripe" style="background:var(--orange)"></div> Mixed</div>
        <div class="legend-item"><div class="legend-stripe" style="background:var(--border2)"></div> Uncategorized</div>
    </div>

    <div class="results-header">
        {% if search or selected_forest or selected_status or selected_days or selected_category %}
            Showing <strong>{{ projects|length }}</strong> result{% if projects|length != 1 %}s{% endif %}
            {% if selected_category %} — <strong>{{ selected_category }}</strong>{% endif %}
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
        <div class="project-card {{ p.category or '' }} {{ 'taking-comments' if p.get('accepting_comments') else '' }}"
             style="background: {{ cat_bg }};
                    border: 1px solid {{ status_color }};
                    border-left: 4px solid {{ status_color }};">

            <div class="card-top">
                <div>
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
                <div style="display:flex; flex-direction:column; align-items:flex-end; gap:6px; flex-shrink:0;">
                    {% if p.get('accepting_comments') %}
                    <div class="comment-open-badge">
                        <span class="badge-title">💬 Taking Comments Now!</span>
                        {% if p.get('comment_deadline') %}
                        <span class="badge-deadline">Deadline: {{ p.comment_deadline }}</span>
                        {% endif %}
                    </div>
                    {% endif %}
                    {% if p.status %}
                    <span class="status-badge"
                          style="background: {{ status_colors.get(p.status, '#8892a4') }}">
                        {{ p.status }}
                    </span>
                    {% endif %}
                    {% if p.get('analysis_type') %}
                    <span class="analysis-badge"
                          style="background: {{ analysis_colors.get(p.analysis_type, '#888') }}; color: white; border-color: transparent;"
                          title="{{ analysis_tooltips.get(p.analysis_type, '') }}">
                        {{ p.analysis_type }}
                    </span>
                    {% endif %}
                </div>
            </div>

            <div class="card-body">
                {% if p.description %}
                <div class="description">{{ p.description }}</div>
                {% endif %}

                {% if has_milestones %}
                <div class="milestone-section">
                    <div class="milestone-section-label">Project Milestones</div>
                    <table class="milestone-table">
                        <thead>
                            <tr><th>Milestone</th><th>Date</th></tr>
                        </thead>
                        <tbody>
                            {% for m in p['milestones'] %}
                            <tr>
                                <td>{{ m.milestone }}</td>
                                <td class="date-cell {{ 'estimated' if m.estimated else '' }}">
                                    {{ m.date if m.date else '—' }}
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
                {% endif %}
            </div>

            {% if has_milestones %}
            {% set project_id = p.project_url.rstrip('/').split('/')[-1] %}
            <div class="comment-buttons">
                <a class="btn-comment primary"
                   href="https://cara.fs2c.usda.gov/Public/CommentInput?Project={{ project_id }}"
                   target="_blank" rel="noopener">
                   ✍️ Submit New Comments
                </a>
                <a class="btn-comment secondary"
                   href="https://cara.fs2c.usda.gov/Public/ReadingRoom?Project={{ project_id }}"
                   target="_blank" rel="noopener">
                   📖 View Prior Comments
                </a>
            </div>
            {% endif %}

            <div class="meta">
                {% if p.unit %}<span>📍 {{ p.unit }}</span>{% endif %}
                {% if p.purpose %}<span>🏷 {{ p.purpose.replace('|', ' · ') }}</span>{% endif %}
                {% if p.first_seen %}<span>Added: {{ p.first_seen[:10] }}</span>{% endif %}
            </div>
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
</html>
"""


@app.route("/")
def index():
    search            = request.args.get("q", "").strip()
    selected_forest   = request.args.get("forest", "").strip()
    selected_status   = request.args.get("status", "").strip()
    selected_days     = request.args.get("days", "").strip()
    selected_category = request.args.get("category", "").strip()
    selected_sort     = request.args.get("sort", "").strip()

    all_projects, last_scraped = load_projects()

    counts = {
        "extractive":  sum(1 for p in all_projects if p.get("category") == "extractive"),
        "restorative": sum(1 for p in all_projects if p.get("category") == "restorative"),
        "mixed":       sum(1 for p in all_projects if p.get("category") == "mixed"),
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

    projects = filter_projects(
        all_projects,
        search=search,
        forest_code=selected_forest,
        status=selected_status,
        days=selected_days,
        category=selected_category,
        sort=selected_sort,
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
        if search:          args["q"]      = search
        if selected_forest: args["forest"] = selected_forest
        if selected_status: args["status"] = selected_status
        if selected_days:   args["days"]   = selected_days
        if selected_sort:   args["sort"]   = selected_sort
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
        forest_counts=forest_counts,
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
