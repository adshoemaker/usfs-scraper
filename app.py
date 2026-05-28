# app.py
# ---------------------------------------------------------------
# A simple web interface for browsing and searching the USFS
# NEPA projects database.
#
# To install Flask (one time only):
#   pip3 install flask
#
# To run:
#   python3 app.py
#
# Then open your browser and go to:
#   http://localhost:5000
# ---------------------------------------------------------------

from flask import Flask, request, render_template_string
from database import get_all_projects, get_connection
from forests import FORESTS

app = Flask(__name__)

# ── HTML Template ─────────────────────────────────────────────────
# The entire web page is defined here as a string.
# It uses Jinja2 templating (built into Flask) to insert data.

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

        header h1 {
            font-size: 1.5rem;
            font-weight: 700;
            letter-spacing: -0.5px;
        }

        header p {
            font-size: 0.85rem;
            opacity: 0.8;
            margin-top: 4px;
        }

        .container {
            max-width: 1100px;
            margin: 0 auto;
            padding: 24px 20px;
        }

        .search-bar {
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

        .search-bar label {
            display: block;
            font-size: 0.78rem;
            font-weight: 600;
            color: #555;
            margin-bottom: 5px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .search-bar input[type="text"] {
            padding: 9px 12px;
            border: 1px solid #ccc;
            border-radius: 5px;
            font-size: 0.95rem;
            width: 320px;
        }

        .search-bar select {
            padding: 9px 12px;
            border: 1px solid #ccc;
            border-radius: 5px;
            font-size: 0.95rem;
            background: white;
            width: 220px;
        }

        .search-bar button {
            padding: 9px 20px;
            background: #2d5016;
            color: white;
            border: none;
            border-radius: 5px;
            font-size: 0.95rem;
            cursor: pointer;
            font-weight: 600;
        }

        .search-bar button:hover { background: #4a7c2f; }

        .search-bar a.clear {
            padding: 9px 14px;
            color: #666;
            font-size: 0.9rem;
            text-decoration: none;
        }

        .search-bar a.clear:hover { color: #333; }

        .results-header {
            font-size: 0.88rem;
            color: #666;
            margin-bottom: 12px;
            padding: 0 2px;
        }

        .results-header strong { color: #2c2c2c; }

        .project-card {
            background: white;
            border: 1px solid #e0e0e0;
            border-radius: 8px;
            padding: 18px 20px;
            margin-bottom: 10px;
            transition: border-color 0.15s;
        }

        .project-card:hover { border-color: #4a7c2f; }

        .project-card .forest-tag {
            font-size: 0.75rem;
            font-weight: 600;
            color: #4a7c2f;
            text-transform: uppercase;
            letter-spacing: 0.4px;
            margin-bottom: 6px;
        }

        .project-card h2 {
            font-size: 1rem;
            font-weight: 600;
            margin-bottom: 6px;
        }

        .project-card h2 a {
            color: #1a3f6f;
            text-decoration: none;
        }

        .project-card h2 a:hover { text-decoration: underline; }

        .project-card .description {
            font-size: 0.88rem;
            color: #555;
            line-height: 1.5;
        }

        .project-card .meta {
            font-size: 0.78rem;
            color: #999;
            margin-top: 8px;
        }

        .no-results {
            text-align: center;
            padding: 60px 20px;
            color: #888;
        }

        .no-results p { font-size: 1.1rem; }

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
    <p>Washington & Alaska National Forests — {{ total_in_db }} active projects tracked</p>
</header>

<div class="container">

    <!-- Search and filter form -->
    <form class="search-bar" method="GET" action="/">
        <div>
            <label for="q">Search projects</label>
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
            <button type="submit">Search</button>
            {% if search or selected_forest %}
            <a class="clear" href="/">Clear</a>
            {% endif %}
        </div>
    </form>

    <!-- Results count -->
    <div class="results-header">
        {% if search or selected_forest %}
            Showing <strong>{{ projects|length }}</strong> result{% if projects|length != 1 %}s{% endif %}
            {% if search %} for "<strong>{{ search }}</strong>"{% endif %}
            {% if selected_forest %} in <strong>{{ selected_forest_name }}</strong>{% endif %}
        {% else %}
            Showing all <strong>{{ projects|length }}</strong> active projects
        {% endif %}
    </div>

    <!-- Project cards -->
    {% if projects %}
        {% for p in projects %}
        <div class="project-card">
            <div class="forest-tag">{{ p.forest_name }}</div>
            <h2><a href="{{ p.project_url }}" target="_blank">{{ p.project_name }}</a></h2>
            {% if p.description %}
            <div class="description">{{ p.description }}</div>
            {% endif %}
            <div class="meta">First tracked: {{ p.first_seen[:10] }}</div>
        </div>
        {% endfor %}
    {% else %}
        <div class="no-results">
            <p>No projects found matching your search.</p>
        </div>
    {% endif %}

</div>

<footer>
    Data scraped from fs.usda.gov &nbsp;·&nbsp;
    Last updated: {{ last_scraped }}
</footer>

</body>
</html>
"""


# ── Routes ────────────────────────────────────────────────────────

@app.route("/")
def index():
    search = request.args.get("q", "").strip()
    selected_forest = request.args.get("forest", "").strip()

    # Get projects from database with optional filters
    projects = get_all_projects(search=search, forest_code=selected_forest)

    # Total count in db (unfiltered)
    conn = get_connection()
    total_in_db = conn.execute(
        "SELECT COUNT(*) FROM projects WHERE is_active = 1"
    ).fetchone()[0]
    last_scraped_row = conn.execute(
        "SELECT MAX(last_seen) FROM projects"
    ).fetchone()[0]
    conn.close()

    last_scraped = last_scraped_row[:10] if last_scraped_row else "never"

    # Find the display name for the selected forest filter
    selected_forest_name = ""
    if selected_forest:
        for f in FORESTS:
            if f["code"] == selected_forest:
                selected_forest_name = f["name"]
                break

    return render_template_string(
        PAGE_TEMPLATE,
        projects=projects,
        forests=FORESTS,
        search=search,
        selected_forest=selected_forest,
        selected_forest_name=selected_forest_name,
        total_in_db=total_in_db,
        last_scraped=last_scraped,
    )


# ── Start the server ──────────────────────────────────────────────

if __name__ == "__main__":
    print("Starting USFS NEPA Project Tracker...")
    print("Open your browser and go to: http://localhost:5000")
    print("Press Ctrl+C to stop the server.")
    app.run(debug=False, port=5000)
