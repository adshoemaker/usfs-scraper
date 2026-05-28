# app.py
# ---------------------------------------------------------------
# Web interface for browsing and searching USFS NEPA projects.
#
# In production (Railway), reads from projects.json which is
# updated daily by GitHub Actions.
#
# To run locally:
#   python3 app.py
# Then open: http://localhost:5000
# ---------------------------------------------------------------

import json
import os
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


def load_projects():
    """Load projects from projects.json."""
    json_path = os.path.join(os.path.dirname(__file__), "projects.json")
    if not os.path.exists(json_path):
        return [], "never"
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    scraped_at = data.get("scraped_at", "")[:10]
    return data.get("projects", []), scraped_at


def filter_projects(projects, search="", forest_code="", status=""):
    """Filter the project list by search term, forest, and/or status."""
    results = []
    search_lower = search.lower()
    for p in projects:
        if search and search_lower not in p.get("project_name", "").lower() \
                  and search_lower not in p.get("description", "").lower():
            continue
        if forest_code and p.get("forest_code") != forest_code:
            continue
        if status and p.get("status") != status:
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
            margin-bottom: 20px;
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
            width: 260px;
        }

        .filters select {
            padding: 9px 12px;
            border: 1px solid #ccc;
            border-radius: 5px;
            font-size: 0.95rem;
            background: white;
            width: 210px;
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

        .results-header {
            font-size: 0.88rem;
            color: #666;
            margin-bottom: 12px;
        }

        .results-header strong { color: #2c2c2c; }

        .project-card {
            background: white;
            border: 1px solid #e0e0e0;
            border-radius: 8px;
            padding: 16px 20px;
            margin-bottom: 10px;
            transition: border-color 0.15s;
        }

        .project-card:hover { border-color: #4a7c2f; }

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

        .status-badge {
            display: inline-block;
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 0.75rem;
            font-weight: 600;
            color: white;
            white-space: nowrap;
            flex-shrink: 0;
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

    <form class="filters" method="GET" action="/">
        <div>
            <label for="q">Search</label>
            <input type="text" id="q" name="q"
                   placeholder="e.g. thinning, trail, salmon..."
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
            <button type="submit">Search</button>
            {% if search or selected_forest or selected_status %}
            <a class="clear" href="/">Clear</a>
            {% endif %}
        </div>
    </form>

    <div class="results-header">
        {% if search or selected_forest or selected_status %}
            Showing <strong>{{ projects|length }}</strong> result{% if projects|length != 1 %}s{% endif %}
            {% if search %} matching "<strong>{{ search }}</strong>"{% endif %}
            {% if selected_status %} with status <strong>{{ selected_status }}</strong>{% endif %}
            {% if selected_forest %} in <strong>{{ selected_forest_name }}</strong>{% endif %}
        {% else %}
            Showing all <strong>{{ projects|length }}</strong> active projects
        {% endif %}
    </div>

    {% if projects %}
        {% for p in projects %}
        <div class="project-card">
            <div class="card-top">
                <div>
                    <div class="forest-tag">{{ p.forest_name }}</div>
                    <h2><a href="{{ p.project_url }}" target="_blank">{{ p.project_name }}</a></h2>
                </div>
                {% if p.status %}
                <span class="status-badge"
                      style="background: {{ status_colors.get(p.status, '#6c757d') }}">
                    {{ p.status }}
                </span>
                {% endif %}
            </div>
            {% if p.description %}
            <div class="description">{{ p.description }}</div>
            {% endif %}
            <div class="meta">
                {% if p.unit %}<span>📍 {{ p.unit }}</span>{% endif %}
                {% if p.purpose %}<span>🏷 {{ p.purpose.replace('|', ' · ') }}</span>{% endif %}
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
    search          = request.args.get("q", "").strip()
    selected_forest = request.args.get("forest", "").strip()
    selected_status = request.args.get("status", "").strip()

    all_projects, last_scraped = load_projects()
    projects = filter_projects(all_projects,
                               search=search,
                               forest_code=selected_forest,
                               status=selected_status)

    # Build status list from whatever's actually in the data
    status_list = sorted(set(
        p["status"] for p in all_projects if p.get("status")
    ))

    selected_forest_name = ""
    for f in FORESTS:
        if f["code"] == selected_forest:
            selected_forest_name = f["name"]
            break

    return render_template_string(
        PAGE_TEMPLATE,
        projects=projects,
        forests=FORESTS,
        status_list=status_list,
        search=search,
        selected_forest=selected_forest,
        selected_forest_name=selected_forest_name,
        selected_status=selected_status,
        status_colors=STATUS_COLORS,
        total=len(all_projects),
        last_scraped=last_scraped,
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting USFS NEPA Project Tracker on port {port}...")
    if port == 5000:
        print("Open your browser and go to: http://localhost:5000")
    print("Press Ctrl+C to stop.")
    app.run(host="0.0.0.0", port=port, debug=False)
