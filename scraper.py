# scraper.py
# ---------------------------------------------------------------
# Visits each forest's NEPA projects page, pulls every listed
# project, and saves results into projects.db and projects.json.
#
# For projects with status "In Progress" or "Developing Proposal",
# also fetches the individual project page to pull milestone dates.
#
# To run:
#   python3 scraper.py
# ---------------------------------------------------------------

import json
import os
import time
import datetime
import requests
from bs4 import BeautifulSoup

from forests import FORESTS
from database import (create_tables, upsert_project, mark_inactive_projects,
                      print_summary, get_connection)

DELAY_BETWEEN_REQUESTS = 2

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

# Only fetch detail pages for these statuses
MILESTONE_STATUSES = {"In Progress", "Developing Proposal"}

def should_fetch_milestones() -> bool:
    """
    Returns True if we should do a full milestone fetch this run.
    True on Tuesdays (weekday=1) and Fridays (weekday=4),
    or when SCRAPE_MODE=full is set (manual GitHub Actions trigger),
    or when running locally (not in CI).
    """
    # Manual override via environment variable
    if os.environ.get("SCRAPE_MODE") == "full":
        return True
    # Running locally (no CI environment variable)
    if not os.environ.get("CI"):
        return True
    # Tuesday or Friday
    weekday = datetime.datetime.now(datetime.timezone.utc).weekday()
    return weekday in (1, 4)  # 1=Tuesday, 4=Friday


def parse_detail_page(html: str) -> dict:
    """
    Parse a project detail page.
    Returns milestones list and analysis_type string.
    """
    soup = BeautifulSoup(html, "html.parser")
    milestones = []
    analysis_type = ""

    # Find the milestone table — it follows a <b>Project Milestones:</b>
    for strong in soup.find_all(["strong", "b"]):
        if "Project Milestones" in strong.get_text():
            table = strong.find_next("table")
            if not table:
                break
            for row in table.find_all("tr")[1:]:  # skip header row
                cols = row.find_all("td")
                if len(cols) < 2:
                    continue
                milestone = cols[0].get_text(strip=True)
                date      = cols[1].get_text(strip=True)
                estimated = "(Estimated)" in date or "(estimated)" in date
                if milestone:  # include rows even if date is blank
                    milestones.append({
                        "milestone": milestone,
                        "date":      date,
                        "estimated": estimated,
                    })
            break

    # Find Expected Analysis Type
    for strong in soup.find_all(["strong", "b"]):
        if "Expected Analysis Type" in strong.get_text():
            # Value is in the next sibling text or parent's next sibling
            parent = strong.parent
            full_text = parent.get_text(strip=True)
            # Strip the label to get just the value
            value = full_text.replace("Expected Analysis Type:", "").strip()
            if value:
                analysis_type = value
            break

    return {"milestones": milestones, "analysis_type": analysis_type}


def most_recent_activity(milestones: list[dict]) -> list[dict]:
    """
    Returns up to 3 items:
    - The single most recent confirmed (non-estimated) milestone
    - The 2 soonest estimated milestones (milestones preserve page order,
      so earliest estimated = first in list)
    """
    confirmed = [m for m in milestones if not m["estimated"]]
    estimated = [m for m in milestones if m["estimated"]]

    most_recent_confirmed = confirmed[-1] if confirmed else None
    soonest_estimated = estimated[:2]  # page order = chronological

    summary = []
    if most_recent_confirmed:
        summary.append(most_recent_confirmed)
    summary.extend(soonest_estimated)
    return summary


def fetch_detail(session: requests.Session, project_url: str) -> dict:
    """Fetch a project detail page and return milestones and analysis type."""
    try:
        r = session.get(project_url, timeout=30)
        r.raise_for_status()
        return parse_detail_page(r.text)
    except requests.RequestException as e:
        print(f"    !! Could not fetch detail from {project_url}: {e}")
        return {"milestones": [], "analysis_type": ""}


def scrape_forest(session: requests.Session, forest: dict) -> list[dict]:
    """Fetch one forest's projects page and return a list of projects."""
    url = forest["projects_url"]
    print(f"  Fetching: {url}")

    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"  !! ERROR fetching {url}: {e}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    projects = []

    for wrapper in soup.find_all("div", class_="wfs-project__teaser"):
        status  = wrapper.get("data-status", "").strip()
        unit    = wrapper.get("data-unit", "").strip()
        purpose = wrapper.get("data-purposeid", "").strip()

        link_tag = wrapper.find("a", href=True)
        if not link_tag:
            continue

        name = link_tag.get_text(strip=True)
        href = link_tag["href"]

        if "/projects/" not in href:
            continue
        if not name or len(name) < 5:
            continue

        project_url = (
            "https://www.fs.usda.gov" + href
            if href.startswith("/") else href
        )

        description = ""
        body = wrapper.find("div", class_="usa-card__body")
        if body:
            p = body.find("p")
            if p:
                description = p.get_text(strip=True)

        projects.append({
            "forest_name":  forest["name"],
            "forest_code":  forest["code"],
            "region":       forest["region"],
            "state":        forest["state"],
            "project_name": name,
            "project_url":  project_url,
            "description":  description,
            "status":       status,
            "unit":         unit,
            "purpose":      purpose,
            "scraped_at":   datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "milestones":   [],  # filled in below for active projects
        })

    print(f"  Found {len(projects)} projects")

    # Decide whether to fetch milestones this run.
    # Full fetch: Tuesdays, Fridays, manual trigger, or local run.
    # New projects always get milestones fetched regardless of day.
    milestone_projects = [p for p in projects if p["status"] in MILESTONE_STATUSES]
    do_full = should_fetch_milestones()

    # Load existing milestone data to identify new projects
    existing_milestones = {}
    try:
        with open("projects.json", encoding="utf-8") as _f:
            _existing = json.load(_f)
        for _p in _existing.get("projects", []):
            if _p.get("milestones"):
                existing_milestones[_p["project_url"]] = {
                    "milestones": _p.get("milestones", []),
                    "analysis_type": _p.get("analysis_type", ""),
                }
    except Exception:
        pass

    to_fetch = []
    for p in milestone_projects:
        is_new = p["project_url"] not in existing_milestones
        if do_full or is_new:
            to_fetch.append(p)
        else:
            # Re-use cached data for existing projects on non-full days
            cached = existing_milestones[p["project_url"]]
            if isinstance(cached, dict):
                p["milestones"] = cached.get("milestones", [])
                p["analysis_type"] = cached.get("analysis_type", "")
            else:
                p["milestones"] = cached
                p["analysis_type"] = ""

    if to_fetch:
        reason = "full refresh" if do_full else "new projects only"
        print(f"  Fetching milestones for {len(to_fetch)} projects ({reason})...")
        for p in to_fetch:
            time.sleep(DELAY_BETWEEN_REQUESTS)
            detail = fetch_detail(session, p["project_url"])
            p["milestones"] = detail["milestones"]
            p["analysis_type"] = detail["analysis_type"]
            if detail["milestones"]:
                print(f"    ✓ {p['project_name'][:50]} — {len(detail['milestones'])} milestones, type: {detail['analysis_type'] or 'n/a'}")
    else:
        print(f"  Milestones: using cached data ({len(milestone_projects)} projects, non-refresh day)")

    return projects


def run_scraper():
    print("=" * 60)
    print("USFS NEPA Project Scraper")
    print(f"Started: {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Forests to scrape: {len(FORESTS)}")
    print("=" * 60)

    create_tables()

    session = requests.Session()
    session.headers.update(HEADERS)

    print("\nInitializing session with USFS homepage...")
    try:
        session.get("https://www.fs.usda.gov/", timeout=15)
        time.sleep(2)
    except requests.RequestException:
        pass

    all_projects = []

    for i, forest in enumerate(FORESTS):
        print(f"\n[{i+1}/{len(FORESTS)}] {forest['name']}")
        projects = scrape_forest(session, forest)

        if projects:
            for p in projects:
                upsert_project(p)
            active_urls = [p["project_url"] for p in projects]
            mark_inactive_projects(active_urls, forest["code"])

        all_projects.extend(projects)

        if i < len(FORESTS) - 1:
            print(f"  Waiting {DELAY_BETWEEN_REQUESTS}s before next forest...")
            time.sleep(DELAY_BETWEEN_REQUESTS)

    # Build JSON including first_seen and milestones
    conn = get_connection()
    url_to_first_seen = {}
    rows = conn.execute("SELECT project_url, first_seen FROM projects").fetchall()
    for row in rows:
        url_to_first_seen[row[0]] = row[1]
    conn.close()

    for p in all_projects:
        p["first_seen"] = url_to_first_seen.get(p["project_url"], p["scraped_at"])
        # Add computed most_recent_activity summary
        p["most_recent_activity"] = most_recent_activity(p.get("milestones", []))

    output = {
        "scraped_at":      datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "total_projects":  len(all_projects),
        "forests_scraped": len(FORESTS),
        "projects":        all_projects,
    }
    with open("projects.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 60)
    print(f"Done. {len(all_projects)} total projects collected.")
    print_summary()
    print("=" * 60)


if __name__ == "__main__":
    run_scraper()
