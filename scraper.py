# scraper.py
# ---------------------------------------------------------------
# Visits each forest's NEPA projects page, pulls every listed
# project, and saves results into projects.db (SQLite database).
# Also saves a projects.json backup file each run.
#
# To run:
#   python3 scraper.py
# ---------------------------------------------------------------

import json
import time
import datetime
import requests
from bs4 import BeautifulSoup

from forests import FORESTS
from database import create_tables, upsert_project, mark_inactive_projects, print_summary

DELAY_BETWEEN_REQUESTS = 3

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

    # Each project is a div with class "wfs-project__teaser".
    # Status, unit, and purpose are stored as data-* attributes
    # on that div. The card container inside holds the link and description.
    #
    # Structure:
    #   <div class="wfs-project__teaser"
    #        data-status="In Progress"
    #        data-unit="Skykomish Ranger District"
    #        data-purposeid="Recreation management">
    #     <div class="usa-card__container">
    #       <div class="usa-card__header">
    #         <h3><a href="/r06/mbs/projects/12345">Project Name</a></h3>
    #       </div>
    #       <div class="usa-card__body">
    #         <p>Description text.</p>
    #       </div>
    #     </div>
    #   </div>

    for wrapper in soup.find_all("div", class_="wfs-project__teaser"):

        # Pull data attributes from the wrapper
        status  = wrapper.get("data-status", "").strip()
        unit    = wrapper.get("data-unit", "").strip()
        purpose = wrapper.get("data-purposeid", "").strip()

        # Get the link and name
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

        # Get description from card body
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
        })

    print(f"  Found {len(projects)} projects")
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
