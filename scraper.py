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
import hashlib
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

def get_scrape_flags() -> dict:
    """
    Returns flags controlling what this run does:
      fetch_milestones - True on Tue/Fri or manual full trigger or local
      include_completed - True on Monday or manual full trigger or local
      use_hash_cache   - True always (skip unchanged forests)
    """
    manual_full = os.environ.get("SCRAPE_MODE") == "full"
    is_local    = not os.environ.get("CI")
    weekday     = datetime.datetime.now(datetime.timezone.utc).weekday()
    # 0=Mon 1=Tue 2=Wed 3=Thu 4=Fri 5=Sat 6=Sun

    return {
        "fetch_milestones":   manual_full or is_local or weekday in (1, 4),
        "include_completed":  manual_full or is_local or weekday == 0,
        "use_hash_cache":     not (manual_full or is_local),
    }


def should_fetch_milestones() -> bool:
    return get_scrape_flags()["fetch_milestones"]


def parse_comment_period(session: requests.Session, project_id: str) -> dict:
    """
    Fetch the CARA comment submission page and check if comments are
    currently being accepted. Returns:
      accepting_comments: bool
      comment_deadline:   str (e.g. "6/8/2026 11:59:59 PM")
    """
    url = f"https://cara.fs2c.usda.gov/Public/CommentInput?Project={project_id}"
    result = {"accepting_comments": False, "comment_deadline": ""}
    try:
        r = session.get(url, timeout=20)
        if r.status_code != 200:
            return result
        text = r.text
        # Key phrase that appears when comments are open
        if "Your comments are requested through" in text:
            result["accepting_comments"] = True
            # Extract the deadline date
            import re
            match = re.search(
                "Your comments are requested through" + r"\s+([^<\n.]+)",
                text
            )
            if match:
                deadline = match.group(1).strip()
                # Clean up any trailing punctuation/whitespace
                deadline = deadline.rstrip(" .")
                result["comment_deadline"] = deadline
    except Exception:
        pass
    return result


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


HASH_CACHE_FILE = "page_hashes.json"


def load_hash_cache() -> dict:
    """Load the stored page hashes from the last run."""
    try:
        with open(HASH_CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_hash_cache(cache: dict):
    """Save updated page hashes for the next run."""
    with open(HASH_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


def page_hash(html: str) -> str:
    """Return a short fingerprint of a page's content."""
    return hashlib.md5(html.encode("utf-8")).hexdigest()


DEAD_PAGE_PHRASES = [
    "we're sorry, but the project listings are not available at this time",
    "project listings are not available",
]


def is_dead_page(html: str) -> bool:
    """Return True if the page shows the USFS unavailable message."""
    lower = html.lower()
    return any(phrase in lower for phrase in DEAD_PAGE_PHRASES)


def fetch_detail(session: requests.Session, project_url: str) -> dict:
    """Fetch a project detail page and return milestones, analysis type, and comment status.
    Returns None if the page is dead (USFS unavailable message)."""
    result = {"milestones": [], "analysis_type": "", "accepting_comments": False, "comment_deadline": ""}
    try:
        r = session.get(project_url, timeout=30)
        r.raise_for_status()

        # Check for dead page before doing anything else
        if is_dead_page(r.text):
            print(f"    💀 DEAD PAGE — will be excluded")
            return None

        detail = parse_detail_page(r.text)
        result.update(detail)
    except requests.RequestException as e:
        print(f"    !! Could not fetch detail from {project_url}: {e}")
        return result

    # Extract project ID and check comment period
    project_id = project_url.rstrip("/").split("/")[-1]
    if project_id.isdigit():
        comment_info = parse_comment_period(session, project_id)
        result.update(comment_info)
        if comment_info["accepting_comments"]:
            print(f"    💬 COMMENTS OPEN until {comment_info['comment_deadline']}")

    return result


def scrape_forest(session: requests.Session, forest: dict,
                  flags: dict, hash_cache: dict) -> list[dict] | None:
    """
    Fetch one forest's projects page and return a list of projects.
    Returns None if the page is unchanged (hash cache hit).
    """
    url = forest["projects_url"]
    print(f"  Fetching: {url}")

    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"  !! ERROR fetching {url}: {e}")
        return []

    # Hash check — skip if page content hasn't changed since last run
    if flags.get("use_hash_cache"):
        current_hash = page_hash(response.text)
        if hash_cache.get(url) == current_hash:
            print(f"  Unchanged since last run — skipping")
            return None
        hash_cache[url] = current_hash

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

    # Fetch milestones and detail for ALL projects
    milestone_projects = [
        p for p in projects
        if flags.get("include_completed") or p["status"] != "Completed"
    ]
    do_full = should_fetch_milestones()

    # Load existing milestone/detail data to identify new projects
    existing_milestones = {}
    try:
        with open("projects.json", encoding="utf-8") as _f:
            _existing = json.load(_f)
        for _p in _existing.get("projects", []):
            if _p.get("milestones") or _p.get("accepting_comments") or _p.get("analysis_type"):
                existing_milestones[_p["project_url"]] = {
                    "milestones":         _p.get("milestones", []),
                    "analysis_type":      _p.get("analysis_type", ""),
                    "accepting_comments": _p.get("accepting_comments", False),
                    "comment_deadline":   _p.get("comment_deadline", ""),
                }
    except Exception:
        pass

    to_fetch = []
    for p in milestone_projects:
        is_new = p["project_url"] not in existing_milestones
        if do_full or is_new:
            to_fetch.append(p)
        else:
            cached = existing_milestones[p["project_url"]]
            if isinstance(cached, dict):
                p["milestones"]         = cached.get("milestones", [])
                p["analysis_type"]      = cached.get("analysis_type", "")
                p["accepting_comments"] = cached.get("accepting_comments", False)
                p["comment_deadline"]   = cached.get("comment_deadline", "")
            else:
                p["milestones"]         = cached
                p["analysis_type"]      = ""
                p["accepting_comments"] = False
                p["comment_deadline"]   = ""

    if to_fetch:
        reason = "full refresh" if do_full else "new projects only"
        print(f"  Fetching details for {len(to_fetch)} projects ({reason})...")
        dead_urls = set()
        for p in to_fetch:
            time.sleep(DELAY_BETWEEN_REQUESTS)
            detail = fetch_detail(session, p["project_url"])
            if detail is None:
                dead_urls.add(p["project_url"])
                continue
            p["milestones"]          = detail["milestones"]
            p["analysis_type"]       = detail["analysis_type"]
            p["accepting_comments"]  = detail["accepting_comments"]
            p["comment_deadline"]    = detail["comment_deadline"]
            if detail["milestones"]:
                print(f"    ✓ {p['project_name'][:50]} — {len(detail['milestones'])} milestones, type: {detail['analysis_type'] or 'n/a'}")
            elif detail["analysis_type"]:
                print(f"    ✓ {p['project_name'][:50]} — type: {detail['analysis_type']}")
        # Remove dead projects from this forest's list
        if dead_urls:
            projects[:] = [p for p in projects if p["project_url"] not in dead_urls]
            print(f"  Removed {len(dead_urls)} dead page(s)")
    else:
        print(f"  Details: using cached data ({len(milestone_projects)} projects, non-refresh day)")

    return projects


# Status priority for dedup merging (lower = higher priority)
STATUS_PRIORITY = {
    "In Progress":         0,
    "Developing Proposal": 1,
    "On Hold":             2,
    "Completed":           3,
}

# Forest abbreviations for display
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
    "Klamath National Forest":              "Klamath",
    "Tongass National Forest":               "Tongass",
}


def deduplicate_projects(projects: list[dict]) -> list[dict]:
    """
    Merge projects with identical names into a single card.
    - Merges forest names into a combined list
    - Keeps the highest-priority status
    - Keeps the longest description
    - Keeps milestones from whichever entry has them
    - Keeps the earliest first_seen date
    """
    groups = {}
    for p in projects:
        # Use project ID (from URL) as dedup key — same project appears across forests with same ID
        project_id = p.get("project_url", "").rstrip("/").split("/")[-1]
        key = project_id if project_id.isdigit() else p["project_name"].strip().lower()
        if key not in groups:
            groups[key] = []
        groups[key].append(p)

    merged = []
    dupes = 0
    for key, group in groups.items():
        if len(group) == 1:
            p = group[0]
        else:
            dupes += 1
            # Sort by status priority
            group.sort(key=lambda x: STATUS_PRIORITY.get(x.get("status", ""), 99))
            base = group[0].copy()

            # Merge forest names — collect all unique forests
            all_forests = []
            seen_codes = set()
            for g in group:
                if g["forest_code"] not in seen_codes:
                    seen_codes.add(g["forest_code"])
                    all_forests.append(g["forest_name"])

            if len(all_forests) > 1:
                base["forest_name"] = ", ".join(
                    FOREST_ABBREVS.get(f, f) for f in all_forests
                )
                base["forest_code"] = "multi"
                base["is_multi_forest"] = True
            # else: keep original forest_name and forest_code from base

            # Keep longest description
            base["description"] = max(
                (g.get("description", "") for g in group),
                key=len
            )

            # Keep milestones from first entry that has them
            for g in group:
                if g.get("milestones"):
                    base["milestones"] = g["milestones"]
                    base["analysis_type"] = g.get("analysis_type", "")
                    break

            # Keep earliest first_seen
            first_seens = [g.get("first_seen", "") for g in group if g.get("first_seen")]
            if first_seens:
                base["first_seen"] = min(first_seens)

            p = base

        merged.append(p)

    if dupes:
        print(f"  Deduplicated {dupes} multi-forest projects")
    return merged


def run_scraper():
    print("=" * 60)
    print("USFS NEPA Project Scraper")
    print(f"Started: {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Forests to scrape: {len(FORESTS)}")
    print("=" * 60)

    create_tables()

    flags = get_scrape_flags()
    print(f"\nRun flags:")
    print(f"  fetch_milestones:  {flags['fetch_milestones']}")
    print(f"  include_completed: {flags['include_completed']}")
    print(f"  use_hash_cache:    {flags['use_hash_cache']}")

    hash_cache = load_hash_cache()

    session = requests.Session()
    session.headers.update(HEADERS)

    print("\nInitializing session with USFS homepage...")
    try:
        session.get("https://www.fs.usda.gov/", timeout=15)
        time.sleep(2)
    except requests.RequestException:
        pass

    all_projects = []
    skipped_forests = 0

    for i, forest in enumerate(FORESTS):
        print(f"\n[{i+1}/{len(FORESTS)}] {forest['name']}")
        projects = scrape_forest(session, forest, flags, hash_cache)

        if projects is None:
            # Page unchanged — load existing projects from JSON for this forest
            skipped_forests += 1
            try:
                with open("projects.json", encoding="utf-8") as f:
                    existing = json.load(f)
                forest_projects = [
                    p for p in existing.get("projects", [])
                    if p.get("forest_code") == forest["code"]
                ]
                all_projects.extend(forest_projects)
                print(f"  Using {len(forest_projects)} cached projects")
            except Exception:
                pass
            continue

        if projects:
            for p in projects:
                # Skip re-processing completed projects on non-Monday runs
                if not flags["include_completed"] and p.get("status") == "Completed":
                    # Still include in output but don't re-upsert
                    continue
                upsert_project(p)
            active_urls = [p["project_url"] for p in projects]
            mark_inactive_projects(active_urls, forest["code"])

        all_projects.extend(projects)

        if i < len(FORESTS) - 1:
            print(f"  Waiting {DELAY_BETWEEN_REQUESTS}s before next forest...")
            time.sleep(DELAY_BETWEEN_REQUESTS)

    save_hash_cache(hash_cache)
    print(f"\nForests skipped (unchanged): {skipped_forests}/{len(FORESTS)}")

    # Deduplicate multi-forest projects
    all_projects = deduplicate_projects(all_projects)
    print(f"  After dedup: {len(all_projects)} unique projects")

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
