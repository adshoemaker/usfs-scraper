# database.py
# ---------------------------------------------------------------
# Creates the SQLite database and provides functions to save
# and retrieve scraped projects.
#
# To set up the database for the first time:
#   python3 database.py
# ---------------------------------------------------------------

import sqlite3
import datetime

DB_FILE = "projects.db"


def get_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def create_tables():
    """Create the projects table if it doesn't already exist."""
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            project_url     TEXT UNIQUE,
            forest_name     TEXT,
            forest_code     TEXT,
            region          TEXT,
            state           TEXT,
            project_name    TEXT,
            description     TEXT,
            status          TEXT,
            unit            TEXT,
            purpose         TEXT,
            first_seen      TEXT,
            last_seen       TEXT,
            is_active       INTEGER DEFAULT 1
        )
    """)
    # Add new columns if upgrading from an older version of the database
    for col, coltype in [("status", "TEXT"), ("unit", "TEXT"), ("purpose", "TEXT")]:
        try:
            conn.execute(f"ALTER TABLE projects ADD COLUMN {col} {coltype}")
        except Exception:
            pass  # Column already exists — that's fine
    conn.commit()
    conn.close()
    print(f"Database ready: {DB_FILE}")


def upsert_project(project: dict):
    """Insert a new project or update it if we've seen it before."""
    conn = get_connection()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    existing = conn.execute(
        "SELECT id FROM projects WHERE project_url = ?",
        (project["project_url"],)
    ).fetchone()

    if existing:
        conn.execute("""
            UPDATE projects
            SET last_seen   = ?,
                description = ?,
                status      = ?,
                unit        = ?,
                purpose     = ?,
                is_active   = 1
            WHERE project_url = ?
        """, (
            now,
            project.get("description", ""),
            project.get("status", ""),
            project.get("unit", ""),
            project.get("purpose", ""),
            project["project_url"],
        ))
    else:
        conn.execute("""
            INSERT INTO projects
                (project_url, forest_name, forest_code, region, state,
                 project_name, description, status, unit, purpose,
                 first_seen, last_seen, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (
            project["project_url"],
            project["forest_name"],
            project["forest_code"],
            project["region"],
            project["state"],
            project["project_name"],
            project.get("description", ""),
            project.get("status", ""),
            project.get("unit", ""),
            project.get("purpose", ""),
            now,
            now,
        ))

    conn.commit()
    conn.close()


def mark_inactive_projects(active_urls: list, forest_code: str):
    """Mark projects not seen in today's scrape as inactive."""
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


def get_all_projects(search: str = "", forest_code: str = "", status: str = "") -> list:
    """Retrieve active projects with optional filters."""
    conn = get_connection()
    query = "SELECT * FROM projects WHERE is_active = 1"
    params = []

    if search:
        query += " AND (project_name LIKE ? OR description LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]

    if forest_code:
        query += " AND forest_code = ?"
        params.append(forest_code)

    if status:
        query += " AND status = ?"
        params.append(status)

    query += " ORDER BY forest_name, project_name"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_status_list() -> list:
    """Return all distinct status values currently in the database."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT DISTINCT status FROM projects
        WHERE is_active = 1 AND status != ''
        ORDER BY status
    """).fetchall()
    conn.close()
    return [row[0] for row in rows]


def print_summary():
    conn = get_connection()
    total = conn.execute(
        "SELECT COUNT(*) FROM projects WHERE is_active = 1"
    ).fetchone()[0]
    print(f"\nActive projects in database: {total}")
    print("\nBy forest:")
    rows = conn.execute("""
        SELECT forest_name, COUNT(*) as count
        FROM projects WHERE is_active = 1
        GROUP BY forest_name ORDER BY forest_name
    """).fetchall()
    for row in rows:
        print(f"  {row[0]:<45} {row[1]} projects")
    conn.close()


if __name__ == "__main__":
    create_tables()
    print_summary()
