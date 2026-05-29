# app.py
# ---------------------------------------------------------------
# Web interface for browsing and searching USFS NEPA projects.
# Reads from projects.json, updated daily by GitHub Actions.
#
# To run locally:
#   python3 app.py
# Then open: http://localhost:5000
# ---------------------------------------------------------------

import json
import os
import datetime
from flask import Flask, request, render_template_string

app = Flask(__name__)

STATUS_COLORS = {
    "Developing Proposal": "#6c757d",
    "In Progress":         "#0d6efd",
    "On Hold":             "#fd7e14",
    "Completed":           "#198754",
}

FORESTS = [
    {"name": "Mt. Baker-Snoqualmie National Forest", "code": "mbs"},
    {"name": "Olympic National Forest",              "code": "olympic"},
    {"name": "Okanogan-Wenatchee National Forest",   "code": "okanogan-wenatchee"},
    {"name": "Gifford Pinchot National Forest",      "code": "giffordpinchot"},
    {"name": "Colville National Forest",             "code": "colville"},
    {"name": "Tongass National Forest",              "code": "tongass"},
]

DATE_RANGES = [
    ("7",  "Last 7 days"),
    ("30", "Last 30 days"),
    ("90", "Last 90 days"),
]

# ── Keyword categories ────────────────────────────────────────────

EXTRACTIVE_KEYWORDS = [
    "timber harvest", "salvage", "thinning", "logging", "clear cut",
    "forest products", "fuels management", "prescribed burn", "wildfire",
    "road construction", "grazing", "range management", "restoration",
    "old growth", "late successional", "watershed", "wilderness",
]

RESTORATIVE_KEYWORDS = [
    "road removal", "salmon", "fish passage", "riparian",
]


def classify_project(project):
    """
    Returns 'extractive', 'restorative', 'mixed', or None.
    Searches both project name and description (case-insensitive).
    Mixed requires at least one keyword from each category.
    """
    text = (
        (project.get("project_name") or "") + " " +
        (project.get("description") or "") + " " +
        (project.get("purpose") or "")
    ).lower()

    has_extractive  = any(kw in text for kw in EXTRACTIVE_KEYWORDS)
    has_restorative = any(kw in text for kw in RESTORATIVE_KEYWORDS)

    if has_extractive and has_restorative:
        return "mixed"
    elif has_extractive:
        return "extractive"
    elif has_restorative:
        return "restorative"
    return None


def load_projects():
    """Load projects from projects.json."""
    json_path = os.path.join(os.path.dirname(__file__), "projects.json")
    if not os.path.exists(json_path):
        return [], "never"
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    scraped_at = data.get("scraped_at", "")[:10]
    projects = data.get("projects", [])
    # Pre-classify every project
    for p in projects:
        p["category"] = classify_project(p)
    return projects, scraped_at


def filter_projects(projects, search="", forest_code="", status="",
                    days="", category=""):
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

    return results


PAGE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>USFS NEPA Project Tracker</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }

        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            background: #f5f5f0;
            color: #2c2c2c;
        }

        header {
            background: #2d5016;
            color: white;
            padding: 20px 30px;
            border-bottom: 4px solid #4a7c2f;
        }

        header h1 { font-size: 1.5rem; font-weight: 700; }
        header p  { font-size: 0.85rem; opacity: 0.8; margin-top: 4px; }

        .container {
            max-width: 1100px;
            margin: 0 auto;
            padding: 24px 20px;
        }

        .filters {
            background: white;
            border: 1px solid #ddd;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 12px;
            display: flex;
            gap: 12px;
            align-items: flex-end;
            flex-wrap: wrap;
        }

        .filters label {
            display: block;
            font-size: 0.78rem;
            font-weight: 600;
            color: #555;
            margin-bottom: 5px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .filters input[type="text"] {
            padding: 9px 12px;
            border: 1px solid #ccc;
            border-radius: 5px;
            font-size: 0.95rem;
            width: 200px;
        }

        .filters select {
            padding: 9px 12px;
            border: 1px solid #ccc;
            border-radius: 5px;
            font-size: 0.95rem;
            background: white;
            width: 175px;
        }

        .filters button {
            padding: 9px 20px;
            background: #2d5016;
            color: white;
            border: none;
            border-radius: 5px;
            font-size: 0.95rem;
            cursor: pointer;
            font-weight: 600;
        }

        .filters button:hover { background: #4a7c2f; }

        .filters a.clear {
            padding: 9px 14px;
            color: #666;
            font-size: 0.9rem;
            text-decoration: none;
        }

        /* Category filter row */
        .category-filters {
            background: white;
            border: 1px solid #ddd;
            border-radius: 8px;
            padding: 14px 20px;
            margin-bottom: 20px;
            display: flex;
            gap: 20px;
            align-items: center;
            flex-wrap: wrap;
        }

        .category-filters span {
            font-size: 0.78rem;
            font-weight: 600;
            color: #555;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-right: 4px;
        }

        .cat-btn {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 6px 14px;
            border-radius: 20px;
            border: 2px solid transparent;
            font-size: 0.85rem;
            font-weight: 600;
            cursor: pointer;
            text-decoration: none;
            transition: all 0.15s;
        }

        .cat-btn .dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            flex-shrink: 0;
        }

        .cat-btn.extractive  { border-color: #dc3545; color: #dc3545; background: #fff5f5; }
        .cat-btn.restorative { border-color: #198754; color: #198754; background: #f0fff4; }
        .cat-btn.mixed       { border-color: #fd7e14; color: #b85c00; background: #fff8f0; }

        .cat-btn.extractive.active  { background: #dc3545; color: white; }
        .cat-btn.restorative.active { background: #198754; color: white; }
        .cat-btn.mixed.active       { background: #fd7e14; color: white; }

        .cat-btn .dot.extractive-dot  { background: #dc3545; }
        .cat-btn .dot.restorative-dot { background: #198754; }
        .cat-btn .dot.mixed-dot       { background: #fd7e14; }

        .cat-btn.active .dot { background: white; }

        .results-header {
            font-size: 0.88rem;
            color: #666;
            margin-bottom: 12px;
        }

        .results-header strong { color: #2c2c2c; }

        /* Project cards with colored left border */
        .project-card {
            background: white;
            border: 1px solid #e0e0e0;
            border-left: 5px solid #e0e0e0;
            border-radius: 8px;
            padding: 16px 20px;
            margin-bottom: 10px;
            transition: border-color 0.15s;
        }

        .project-card:hover { border-color: #bbb; }

        .project-card.extractive  {
            border-left-color: #dc3545;
        }
        .project-card.restorative {
            border-left-color: #198754;
        }
        .project-card.mixed {
            border-left-color: #fd7e14;
        }

        .card-top {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 12px;
            margin-bottom: 4px;
        }

        .forest-tag {
            font-size: 0.75rem;
            font-weight: 600;
            color: #4a7c2f;
            text-transform: uppercase;
            letter-spacing: 0.4px;
            margin-bottom: 4px;
        }

        .project-card h2 { font-size: 1rem; font-weight: 600; }
        .project-card h2 a { color: #1a3f6f; text-decoration: none; }
        .project-card h2 a:hover { text-decoration: underline; }

        .badges { display: flex; gap: 6px; align-items: center; flex-shrink: 0; }

        .status-badge {
            display: inline-block;
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 0.75rem;
            font-weight: 600;
            color: white;
            white-space: nowrap;
        }

        .new-badge {
            display: inline-block;
            background: #fff3cd;
            color: #856404;
            border: 1px solid #ffc107;
            border-radius: 4px;
            font-size: 0.72rem;
            font-weight: 700;
            padding: 1px 6px;
            vertical-align: middle;
        }

        .description {
            font-size: 0.88rem;
            color: #555;
            line-height: 1.5;
            margin-top: 6px;
        }

        .meta {
            font-size: 0.78rem;
            color: #999;
            margin-top: 8px;
        }

        .meta span { margin-right: 16px; }

        .no-results {
            text-align: center;
            padding: 60px 20px;
            color: #888;
            font-size: 1.1rem;
        }

        /* Legend */
        .legend {
            display: flex;
            gap: 20px;
            font-size: 0.78rem;
            color: #666;
            margin-bottom: 12px;
            flex-wrap: wrap;
        }

        .legend-item {
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .legend-stripe {
            width: 14px;
            height: 14px;
            border-radius: 2px;
            flex-shrink: 0;
        }

        footer {
            text-align: center;
            padding: 30px;
            font-size: 0.8rem;
            color: #aaa;
            margin-top: 20px;
        }
    </style>
</head>
<body>

<header>
    <h1>🌲 USFS NEPA Project Tracker</h1>
    <p>Washington & Alaska National Forests &nbsp;·&nbsp; {{ total }} active projects tracked</p>
</header>

<div class="container">

    <!-- Main filters -->
    <form class="filters" method="GET" action="/" id="mainform">
        <input type="hidden" name="category" value="{{ selected_category }}">
        <div>
            <label for="q">Search</label>
            <input type="text" id="q" name="q"
                   placeholder="e.g. thinning, trail..."
                   value="{{ search }}">
        </div>
        <div>
            <label for="forest">Forest</label>
            <select id="forest" name="forest">
                <option value="">All forests</option>
                {% for f in forests %}
                <option value="{{ f.code }}"
                    {% if selected_forest == f.code %}selected{% endif %}>
                    {{ f.name }}
                </option>
                {% endfor %}
            </select>
        </div>
        <div>
            <label for="status">Status</label>
            <select id="status" name="status">
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
            <select id="days" name="days">
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
            <button type="submit">Search</button>
            {% if search or selected_forest or selected_status or selected_days or selected_category %}
            <a class="clear" href="/">Clear all</a>
            {% endif %}
        </div>
    </form>

    <!-- Category filter buttons -->
    <div class="category-filters">
        <span>Show only:</span>

        {% set ext_url = url_with_category('extractive') %}
        {% set res_url = url_with_category('restorative') %}
        {% set mix_url = url_with_category('mixed') %}

        <a href="{{ ext_url }}"
           class="cat-btn extractive {{ 'active' if selected_category == 'extractive' else '' }}">
            <span class="dot extractive-dot"></span>
            Extractive ({{ counts.extractive }})
        </a>
        <a href="{{ res_url }}"
           class="cat-btn restorative {{ 'active' if selected_category == 'restorative' else '' }}">
            <span class="dot restorative-dot"></span>
            Restorative ({{ counts.restorative }})
        </a>
        <a href="{{ mix_url }}"
           class="cat-btn mixed {{ 'active' if selected_category == 'mixed' else '' }}">
            <span class="dot mixed-dot"></span>
            Mixed ({{ counts.mixed }})
        </a>
    </div>

    <!-- Legend -->
    <div class="legend">
        <div class="legend-item">
            <div class="legend-stripe" style="background:#dc3545"></div>
            Extractive
        </div>
        <div class="legend-item">
            <div class="legend-stripe" style="background:#198754"></div>
            Restorative
        </div>
        <div class="legend-item">
            <div class="legend-stripe" style="background:#fd7e14"></div>
            Mixed
        </div>
        <div class="legend-item">
            <div class="legend-stripe" style="background:#e0e0e0"></div>
            Uncategorized
        </div>
    </div>

    <!-- Results count -->
    <div class="results-header" style="margin-top:10px">
        {% if search or selected_forest or selected_status or selected_days or selected_category %}
            Showing <strong>{{ projects|length }}</strong> result{% if projects|length != 1 %}s{% endif %}
            {% if selected_category %} — <strong>{{ selected_category }}</strong> projects{% endif %}
            {% if selected_days %} added in the last <strong>{{ selected_days }} days</strong>{% endif %}
            {% if search %} matching "<strong>{{ search }}</strong>"{% endif %}
            {% if selected_status %} with status <strong>{{ selected_status }}</strong>{% endif %}
            {% if selected_forest %} in <strong>{{ selected_forest_name }}</strong>{% endif %}
        {% else %}
            Showing all <strong>{{ projects|length }}</strong> active projects
        {% endif %}
    </div>

    <!-- Project cards -->
    {% if projects %}
        {% for p in projects %}
        <div class="project-card {{ p.category or '' }}">
            <div class="card-top">
                <div>
                    <div class="forest-tag">{{ p.forest_name }}</div>
                    <h2>
                        <a href="{{ p.project_url }}" target="_blank">{{ p.project_name }}</a>
                        {% if p.get('first_seen') and p['first_seen'][:10] >= recent_cutoff %}
                        <span class="new-badge">NEW</span>
                        {% endif %}
                    </h2>
                </div>
                <div class="badges">
                    {% if p.status %}
                    <span class="status-badge"
                          style="background: {{ status_colors.get(p.status, '#6c757d') }}">
                        {{ p.status }}
                    </span>
                    {% endif %}
                </div>
            </div>
            {% if p.description %}
            <div class="description">{{ p.description }}</div>
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

    all_projects, last_scraped = load_projects()

    # Count by category (before other filters, so counts are always visible)
    counts = {
        "extractive":  sum(1 for p in all_projects if p.get("category") == "extractive"),
        "restorative": sum(1 for p in all_projects if p.get("category") == "restorative"),
        "mixed":       sum(1 for p in all_projects if p.get("category") == "mixed"),
    }

    projects = filter_projects(
        all_projects,
        search=search,
        forest_code=selected_forest,
        status=selected_status,
        days=selected_days,
        category=selected_category,
    )

    status_list = sorted(set(
        p["status"] for p in all_projects if p.get("status")
    ))

    selected_forest_name = ""
    for f in FORESTS:
        if f["code"] == selected_forest:
            selected_forest_name = f["name"]
            break

    recent_cutoff = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)
    ).strftime("%Y-%m-%d")

    # Helper to build category toggle URLs preserving other filters
    from flask import url_for
    def url_with_category(cat):
        args = {}
        if search:          args["q"]        = search
        if selected_forest: args["forest"]   = selected_forest
        if selected_status: args["status"]   = selected_status
        if selected_days:   args["days"]     = selected_days
        # Toggle: clicking active category deselects it
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
        status_colors=STATUS_COLORS,
        total=len(all_projects),
        last_scraped=last_scraped,
        recent_cutoff=recent_cutoff,
        counts=counts,
        url_with_category=url_with_category,
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting USFS NEPA Project Tracker on port {port}...")
    if port == 5000:
        print("Open your browser and go to: http://localhost:5000")
    print("Press Ctrl+C to stop.")
    app.run(host="0.0.0.0", port=port, debug=False)
