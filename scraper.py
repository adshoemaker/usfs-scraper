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
import random
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup

from forests import FORESTS
from database import (create_tables, upsert_project, mark_inactive_projects,
                      print_summary, get_connection)

DELAY_MIN = 3.0   # minimum seconds between requests
DELAY_MAX = 6.0   # maximum seconds between requests


def polite_sleep(extra: float = 0):
    """Sleep for a randomized polite delay plus any extra backoff."""
    time.sleep(random.uniform(DELAY_MIN, DELAY_MAX) + extra)

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
        r = fetch_with_retry(session, url, timeout=20)
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

    # Find Location Summary
    location_summary = ""
    for strong in soup.find_all(["strong", "b"]):
        if "Location Summary" in strong.get_text():
            parent = strong.parent
            full_text = parent.get_text(strip=True)
            value = full_text.replace("Location Summary:", "").strip()
            if value:
                location_summary = value
            break

    return {"milestones": milestones, "analysis_type": analysis_type, "location_summary": location_summary}


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


def fetch_with_retry(session: requests.Session, url: str,
                     timeout: int = 60, max_retries: int = 3) -> requests.Response:
    """
    Fetch a URL with retry logic for 429 (rate limit) and timeout errors.
    Backs off progressively on 429: 30s, 60s, 120s.
    """
    backoff = 30
    for attempt in range(max_retries):
        try:
            r = session.get(url, timeout=timeout)
            if r.status_code == 429:
                if attempt < max_retries - 1:
                    print(f"    ⚠️  Rate limited (429) — waiting {backoff}s before retry...")
                    time.sleep(backoff)
                    backoff *= 2
                    continue
            r.raise_for_status()
            return r
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                print(f"    ⏱ Timeout — retrying in 5s...")
                time.sleep(5)
            else:
                raise
    raise requests.RequestException(f"Failed after {max_retries} attempts: {url}")


def fetch_detail(session: requests.Session, project_url: str, status: str = "") -> dict:
    """Fetch a project detail page and return milestones, analysis type, and comment status.
    Returns None if the page is dead (USFS unavailable message).
    Retries once on timeout."""
    result = {"milestones": [], "analysis_type": "", "accepting_comments": False, "comment_deadline": "", "_status": status}

    try:
        r = fetch_with_retry(session, project_url)

        # Check if we were redirected to a different project URL
        final_url = r.url.rstrip("/")
        stored_url = project_url.rstrip("/")
        if final_url != stored_url:
            print(f"    ↪ Redirected: {stored_url.split('/')[-1]} → {final_url.split('/')[-1]}")
            result["redirect_url"] = final_url

        # Check for dead page before doing anything else
        if is_dead_page(r.text):
            print(f"    💀 DEAD PAGE — will be excluded")
            return None

        detail = parse_detail_page(r.text)
        result.update(detail)
    except requests.RequestException as e:
        print(f"    !! Could not fetch detail from {project_url}: {e}")
        return result

    # Extract project ID from final URL (after any redirect) for CARA check
    # Skip CARA check for Completed and On Hold — they don't accept comments
    skip_cara = result.get("_status") in ("Completed", "On Hold")
    if not skip_cara:
        final_project_url = result.get("redirect_url", project_url)
        project_id = final_project_url.rstrip("/").split("/")[-1]
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
        response = fetch_with_retry(session, url)
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

    # Load project-level hash cache
    project_hash_cache = {}
    try:
        with open("project_hashes.json", encoding="utf-8") as _hf:
            project_hash_cache = json.load(_hf)
    except Exception:
        pass

    CARA_STATUSES = {"In Progress", "Developing Proposal"}
    to_fetch = []      # needs full detail page fetch
    to_cara  = []      # only needs CARA re-check, detail cached
    dead_urls = set()

    for p in milestone_projects:
        url = p["project_url"]
        # Hash the project's listing entry to detect changes
        entry_str = f"{p['project_name']}|{p['status']}|{p['description'][:100]}"
        entry_hash = hashlib.md5(entry_str.encode()).hexdigest()
        is_new      = url not in existing_milestones
        hash_changed = project_hash_cache.get(url) != entry_hash

        if is_new or hash_changed:
            to_fetch.append(p)
            project_hash_cache[url] = entry_hash
        else:
            # Restore from cache
            cached = existing_milestones[url]
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
            # Still re-check CARA daily for active projects
            if p["status"] in CARA_STATUSES:
                to_cara.append(p)

    print(f"  {len(to_fetch)} projects need detail fetch, {len(to_cara)} need CARA re-check only")

    if to_fetch:
        print(f"  Fetching details for {len(to_fetch)} projects with 2 workers...")

        def fetch_one(p):
            polite_sleep()
            detail = fetch_detail(session, p["project_url"], status=p.get("status", ""))
            return p, detail

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {executor.submit(fetch_one, p): p for p in to_fetch}
            for future in as_completed(futures):
                p, detail = future.result()
                if detail is None:
                    dead_urls.add(p["project_url"])
                    continue
                if detail.get("redirect_url"):
                    p["project_url"] = detail["redirect_url"]
                p["milestones"]          = detail["milestones"]
                p["analysis_type"]       = detail["analysis_type"]
                p["accepting_comments"]  = detail["accepting_comments"]
                p["comment_deadline"]    = detail["comment_deadline"]
                if detail["milestones"]:
                    print(f"    ✓ {p['project_name'][:50]} — {len(detail['milestones'])} milestones, type: {detail['analysis_type'] or 'n/a'}")
                elif detail["analysis_type"]:
                    print(f"    ✓ {p['project_name'][:50]} — type: {detail['analysis_type']}")

    if to_cara:
        print(f"  Re-checking CARA for {len(to_cara)} active projects...")
        def check_cara(p):
            polite_sleep()
            final_url = p.get("redirect_url", p["project_url"])
            project_id = final_url.rstrip("/").split("/")[-1]
            if project_id.isdigit():
                info = parse_comment_period(session, project_id)
                return p, info
            return p, {}

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {executor.submit(check_cara, p): p for p in to_cara}
            for future in as_completed(futures):
                p, info = future.result()
                if info:
                    p["accepting_comments"] = info.get("accepting_comments", False)
                    p["comment_deadline"]   = info.get("comment_deadline", "")
                    if info.get("accepting_comments"):
                        print(f"    💬 COMMENTS OPEN: {p['project_name'][:50]}")

    # Save updated project hash cache
    try:
        with open("project_hashes.json", "w", encoding="utf-8") as _hf:
            json.dump(project_hash_cache, _hf)
    except Exception:
        pass

    # Remove all dead projects found
    if dead_urls:
        projects[:] = [p for p in projects if p["project_url"] not in dead_urls]
        print(f"  Removed {len(dead_urls)} dead page(s)")

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
    "Deschutes National Forest":             "Deschutes",
    "Mt. Hood National Forest":              "Mt. Hood",
    "Shasta-Trinity National Forest":        "Shasta-Trinity",
    "Inyo National Forest":                  "Inyo",
    "Los Padres National Forest":            "Los Padres",
    "Klamath National Forest":              "Klamath",
    "Chugach National Forest":               "Chugach",
    "Tongass National Forest":               "Tongass",
}


def deduplicate_projects(projects: list[dict]) -> list[dict]:
    """
    Two-pass deduplication:
    Pass 1: Merge projects with identical project IDs (same project across multiple forests)
    Pass 2: Within same forest, keep only highest-numbered project ID for same-name projects
             (handles cases where old project redirects to new project with same name)
    """
    # Pass 1: group by project ID
    groups = {}
    for p in projects:
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
            group.sort(key=lambda x: STATUS_PRIORITY.get(x.get("status", ""), 99))
            base = group[0].copy()

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

            base["description"] = max(
                (g.get("description", "") for g in group),
                key=len
            )

            for g in group:
                if g.get("milestones"):
                    base["milestones"] = g["milestones"]
                    base["analysis_type"] = g.get("analysis_type", "")
                    base["location_summary"] = g.get("location_summary", "")
                    break

            first_seens = [g.get("first_seen", "") for g in group if g.get("first_seen")]
            if first_seens:
                base["first_seen"] = min(first_seens)

            p = base

        merged.append(p)

    if dupes:
        print(f"  Pass 1: merged {dupes} multi-forest projects")

    # Pass 2: within same forest, keep only highest project ID for same-name projects
    by_name_forest = {}
    for p in merged:
        name = p["project_name"].strip().lower()
        forest = p.get("forest_code", "")
        key = f"{name}|{forest}"
        if key not in by_name_forest:
            by_name_forest[key] = []
        by_name_forest[key].append(p)

    final = []
    redirect_dupes = 0
    for key, group in by_name_forest.items():
        if len(group) == 1:
            final.append(group[0])
        else:
            redirect_dupes += 1
            # Keep the project with the highest numeric project ID
            def get_id(p):
                pid = p.get("project_url", "").rstrip("/").split("/")[-1]
                return int(pid) if pid.isdigit() else 0
            group.sort(key=get_id, reverse=True)
            kept = group[0]
            # Preserve earliest first_seen across all versions
            first_seens = [g.get("first_seen", "") for g in group if g.get("first_seen")]
            if first_seens:
                kept["first_seen"] = min(first_seens)
            print(f"  Pass 2: kept ID {get_id(kept)} over {[get_id(g) for g in group[1:]]} for '{kept['project_name'][:40]}'")
            final.append(kept)

    if redirect_dupes:
        print(f"  Pass 2: removed {redirect_dupes} superseded projects")

    return final


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
    failed_forests = []

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

        if not projects:
            # Empty result likely means fetch error/timeout
            failed_forests.append(forest['name'])
            print(f"  !! No projects returned — possible fetch failure")
            # Fall back to cached data
            try:
                with open("projects.json", encoding="utf-8") as f:
                    existing = json.load(f)
                forest_projects = [
                    p for p in existing.get("projects", [])
                    if p.get("forest_code") == forest["code"]
                ]
                all_projects.extend(forest_projects)
                print(f"  Using {len(forest_projects)} cached projects as fallback")
            except Exception:
                pass
            if i < len(FORESTS) - 1:
                polite_sleep()
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
            polite_sleep()

    save_hash_cache(hash_cache)
    print(f"\nForests skipped (unchanged): {skipped_forests}/{len(FORESTS)}")
    if failed_forests:
        print(f"Forests with fetch errors: {len(failed_forests)}/{len(FORESTS)}")
        for f in failed_forests:
            print(f"  !! {f}")

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

    # Update ledger — permanent record of first_seen dates
    ledger = update_ledger(all_projects)
    save_ledger(ledger)
    print(f"  Ledger updated: {len(ledger)} total projects tracked")

    # Overwrite first_seen from ledger (authoritative source)
    for p in all_projects:
        url = p.get("project_url", "")
        if url in ledger:
            p["first_seen"] = ledger[url]["first_seen"]

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

    # Push projects.json and ledger.json to GitHub via API
    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token:
        push_ok = push_projects_json_via_api(github_token)
        if not push_ok:
            print("!! GitHub API push failed — projects.json saved locally only")
        ledger_ok = push_ledger_via_api(github_token)
        if not ledger_ok:
            print("!! GitHub API push failed — ledger.json saved locally only")

    return {"failed_forests": failed_forests, "total_forests": len(FORESTS)}


def push_projects_json_via_api(token: str) -> bool:
    """Push projects.json to GitHub using the REST API. Always succeeds regardless of git state."""
    import base64
    import urllib.request
    import urllib.error

    repo  = "adshoemaker/usfs-scraper"
    path  = "projects.json"
    url   = f"https://api.github.com/repos/{repo}/contents/{path}"
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    with open(path, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode("utf-8")

    # Get current SHA of the file (required for update)
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urllib.request.urlopen(req) as resp:
            current = json.loads(resp.read())
            sha = current["sha"]
    except urllib.error.HTTPError as e:
        print(f"!! GitHub API: could not get file SHA: {e}")
        return False

    # Push updated file
    payload = json.dumps({
        "message": f"Scrape: {today}",
        "content": content_b64,
        "sha":     sha,
    }).encode("utf-8")

    req2 = urllib.request.Request(url, data=payload, method="PUT", headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urllib.request.urlopen(req2) as resp:
            result = json.loads(resp.read())
            print(f"  ✓ projects.json pushed via GitHub API: {result['commit']['sha'][:7]}")
            return True
    except urllib.error.HTTPError as e:
        print(f"!! GitHub API push failed: {e} — {e.read().decode()}")
        return False




def load_ledger() -> dict:
    """Load ledger.json — maps project_url -> {name, first_seen}."""
    if os.path.exists("ledger.json"):
        with open("ledger.json", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_ledger(ledger: dict):
    """Save ledger.json locally."""
    with open("ledger.json", "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2, ensure_ascii=False, sort_keys=True)


def update_ledger(projects: list) -> dict:
    """Update ledger with current projects. Never removes entries. Returns updated ledger."""
    ledger = load_ledger()
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    for p in projects:
        url  = p.get("project_url", "")
        name = p.get("project_name", "")
        if not url:
            continue
        if url in ledger:
            # Update name if it changed, but never change first_seen
            ledger[url]["name"] = name
        else:
            # New project — use existing first_seen if available, else today
            first_seen = p.get("first_seen", "")
            if first_seen:
                first_seen = first_seen[:10]  # trim to YYYY-MM-DD
            else:
                first_seen = today
            ledger[url] = {
                "name":       name,
                "first_seen": first_seen,
            }

    return ledger


def push_ledger_via_api(token: str) -> bool:
    """Push ledger.json to GitHub using the REST API."""
    import base64
    import urllib.request
    import urllib.error

    repo  = "adshoemaker/usfs-scraper"
    path  = "ledger.json"
    url   = f"https://api.github.com/repos/{repo}/contents/{path}"
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    with open(path, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode("utf-8")

    # Get current SHA (None if file doesn't exist yet)
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    sha = None
    try:
        with urllib.request.urlopen(req) as resp:
            sha = json.loads(resp.read())["sha"]
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"!! GitHub API: could not get ledger SHA: {e}")
            return False

    payload_data = {"message": f"Ledger: {today}", "content": content_b64}
    if sha:
        payload_data["sha"] = sha

    payload = json.dumps(payload_data).encode("utf-8")
    req2 = urllib.request.Request(url, data=payload, method="PUT", headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urllib.request.urlopen(req2) as resp:
            result = json.loads(resp.read())
            print(f"  ✓ ledger.json pushed via GitHub API: {result['commit']['sha'][:7]}")
            return True
    except urllib.error.HTTPError as e:
        print(f"!! GitHub API ledger push failed: {e} — {e.read().decode()}")
        return False

if __name__ == "__main__":
    import sys
    result = run_scraper()
    if result and result.get("failed_forests"):
        failed = result["failed_forests"]
        total = result["total_forests"]
        # Fail if more than half the forests had errors
        if len(failed) > total // 2:
            print(f"\n!! Too many forests failed ({len(failed)}/{total}) — exiting with error")
            sys.exit(1)
