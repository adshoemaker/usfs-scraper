# database.py
# ---------------------------------------------------------------
# Creates the SQLite database and provides functions to save
# scraped projects into it.
#
# SQLite is built into Python — nothing extra to install.
# The database is stored as a single file: projects.db
#
# To set up the database for the first time:
#   python3 database.py
#
# After that, run scraper.py normally and it will save into
# the database automatically.
# ---------------------------------------------------------------

import sqlite3
import datetime

DB_FILE = "projects.db"


def get_connection():
    """Open a connection to the database file."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row  # lets us access columns by name
    return conn


def create_tables():
    """
    Create the projects table if it doesn't already exist.
    Safe to run multiple times — won't overwrite existing data.
    """
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            project_url     TEXT UNIQUE,        -- unique ID for each project
            forest_name     TEXT,
            forest_code     TEXT,
            region          TEXT,
            state           TEXT,
            project_name    TEXT,
            description     TEXT,
            first_seen      TEXT,               -- date we first found this project
            last_seen       TEXT,               -- date we most recently found it
            is_active       INTEGER DEFAULT 1   -- 1 = still on the site, 0 = removed
        )
    """)
    conn.commit()
    conn.close()
    print(f"Database ready: {DB_FILE}")


def upsert_project(project: dict):
    """
    Insert a new project or update it if we've seen it before.
    'Upsert' = update + insert combined.

    - New project: inserts a full record with today as first_seen
    - Existing project: updates last_seen and description (in case it changed)
    """
    conn = get_connection()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    existing = conn.execute(
        "SELECT id FROM projects WHERE project_url = ?",
        (project["project_url"],)
    ).fetchone()

    if existing:
        # Project already in database — just update last_seen
        conn.execute("""
            UPDATE projects
            SET last_seen   = ?,
                description = ?,
                is_active   = 1
            WHERE project_url = ?
        """, (now, project["description"], project["project_url"]))
    else:
        # Brand new project — insert full record
        conn.execute("""
            INSERT INTO projects
                (project_url, forest_name, forest_code, region, state,
                 project_name, description, first_seen, last_seen, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (
            project["project_url"],
            project["forest_name"],
            project["forest_code"],
            project["region"],
            project["state"],
            project["project_name"],
            project["description"],
            now,
            now,
        ))

    conn.commit()
    conn.close()


def mark_inactive_projects(active_urls: list[str], forest_code: str):
    """
    Any project for this forest that wasn't in today's scrape
    gets marked inactive — it's been removed from the USFS site.
    """
    conn = get_connection()
    if not active_urls:
        conn.close()
        return

    placeholders = ",".join("?" * len(active_urls))
    conn.execute(f"""
        UPDATE projects
        SET is_active = 0
        WHERE forest_code = ?
        AND project_url NOT IN ({placeholders})
    """, [forest_code] + active_urls)

    conn.commit()
    conn.close()


def get_all_projects(search: str = "", forest_code: str = "") -> list:
    """
    Retrieve projects from the database.
    Optionally filter by search term and/or forest.
    Only returns active projects by default.
    """
    conn = get_connection()
    query = "SELECT * FROM projects WHERE is_active = 1"
    params = []

    if search:
        query += " AND (project_name LIKE ? OR description LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]

    if forest_code:
        query += " AND forest_code = ?"
        params.append(forest_code)

    query += " ORDER BY forest_name, project_name"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def print_summary():
    """Print a quick summary of what's in the database."""
    conn = get_connection()

    total = conn.execute("SELECT COUNT(*) FROM projects WHERE is_active = 1").fetchone()[0]
    print(f"\nActive projects in database: {total}")

    print("\nBy forest:")
    rows = conn.execute("""
        SELECT forest_name, COUNT(*) as count
        FROM projects
        WHERE is_active = 1
        GROUP BY forest_name
        ORDER BY forest_name
    """).fetchall()
    for row in rows:
        print(f"  {row[0]:<45} {row[1]} projects")

    conn.close()


# ── Run directly to initialize the database ───────────────────────

if __name__ == "__main__":
    create_tables()
    print_summary()
