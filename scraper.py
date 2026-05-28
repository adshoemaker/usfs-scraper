# scraper.py
# ---------------------------------------------------------------
# Visits each forest's NEPA projects page, pulls every listed
# project, and saves the results to a JSON file.
#
# What it collects per project:
#   - Project name
#   - Project URL (link to the full project page)
#   - Description (from the usa-card__body div)
#   - Which forest it belongs to
#   - Date/time it was scraped
#
# To run:
#   python3 scraper.py
#
# Output:
#   projects.json  (created or overwritten each run)
# ---------------------------------------------------------------

import json
import time
import datetime
import requests
from bs4 import BeautifulSoup

from forests import FORESTS

# ── Settings ─────────────────────────────────────────────────────

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


# ── Core scraping function ────────────────────────────────────────

def scrape_forest(session: requests.Session, forest: dict) -> list[dict]:
    """
    Fetch one forest's projects page and return a list of projects.
    Returns an empty list if anything goes wrong.
    """
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

    # Each project is a card with this structure:
    #
    #   <div class="usa-card__container margin-0">
    #     <div class="usa-card__header">
    #       <h3 class="margin-0">
    #         <a href="/r06/mbs/projects/12345">Project Name</a>
    #       </h3>
    #     </div>
    #     <div class="usa-card__body">
    #       <p>Description text here.</p>
    #     </div>
    #   </div>

    for card in soup.find_all("div", class_="usa-card__container"):

        # Get the link and name from the header
        link_tag = card.find("a", href=True)
        if not link_tag:
            continue

        name = link_tag.get_text(strip=True)
        href = link_tag["href"]

        # Only keep actual project page links
        if "/projects/" not in href:
            continue

        if not name or len(name) < 5:
            continue

        # Build full URL if relative
        if href.startswith("/"):
            project_url = "https://www.fs.usda.gov" + href
        else:
            project_url = href

        # Get description from the card body
        description = ""
        body = card.find("div", class_="usa-card__body")
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
            "scraped_at":   datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })

    print(f"  Found {len(projects)} projects")
    return projects


# ── Main runner ───────────────────────────────────────────────────

def run_scraper():
    print("=" * 60)
    print("USFS NEPA Project Scraper")
    print(f"Started: {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Forests to scrape: {len(FORESTS)}")
    print("=" * 60)

    session = requests.Session()
    session.headers.update(HEADERS)

    # Hit the homepage first to pick up session cookies
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
        all_projects.extend(projects)

        if i < len(FORESTS) - 1:
            print(f"  Waiting {DELAY_BETWEEN_REQUESTS}s before next forest...")
            time.sleep(DELAY_BETWEEN_REQUESTS)

    # ── Save results ──────────────────────────────────────────────
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
    print(f"Results saved to: projects.json")
    print("=" * 60)


if __name__ == "__main__":
    run_scraper()
