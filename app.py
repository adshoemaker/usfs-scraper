# app.py
# ---------------------------------------------------------------
# Web interface for browsing and searching USFS NEPA projects.
# Reads from projects.json, updated by GitHub Actions.
#
# To run locally:
#   python3 app.py
# Then open: http://localhost:5000
# ---------------------------------------------------------------

import json
import os
import datetime
import base64
import urllib.request
import urllib.error
import urllib.parse
from flask import Flask, request, render_template_string, session, redirect, url_for

def format_deadline(deadline_str):
    """Convert deadline string to short Pacific time format."""
    import re
    import datetime
    if not deadline_str:
        return deadline_str
    # Timezone offsets to Pacific
    tz_offsets = {
        "Pacific Standard Time": 0, "PST": 0,
        "Pacific Daylight Time": 0, "PDT": 0,
        "Mountain Standard Time": 1, "MST": 1,
        "Mountain Daylight Time": 1, "MDT": 1,
        "Central Standard Time": 2, "CST": 2,
        "Central Daylight Time": 2, "CDT": 2,
        "Eastern Standard Time": 3, "EST": 3,
        "Eastern Daylight Time": 3, "EDT": 3,
        "Alaskan Standard Time": -1, "AKST": -1,
        "Alaska Standard Time": -1,
        "Alaskan Daylight Time": 0, "AKDT": 0,
        "Alaska Daylight Time": 0,
        "Hawaii-Aleutian Standard Time": -2, "HST": -2,
    }
    # Detect timezone
    tz_name = ""
    hours_diff = 0
    for tz, diff in tz_offsets.items():
        if tz.lower() in deadline_str.lower():
            tz_name = tz
            hours_diff = diff
            break
    # Determine if currently PDT or PST (rough: Mar-Nov = PDT)
    now = datetime.datetime.now()
    is_pdt = 3 <= now.month <= 11
    pt_abbr = "PDT" if is_pdt else "PST"
    # Parse datetime
    # Format: M/D/YYYY H:MM:SS AM/PM
    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})\s*(AM|PM)', deadline_str, re.I)
    if m:
        mon, day, yr, hr, mn, sc, ampm = m.groups()
        hr = int(hr)
        if ampm.upper() == 'PM' and hr != 12:
            hr += 12
        elif ampm.upper() == 'AM' and hr == 12:
            hr = 0
        dt = datetime.datetime(int(yr), int(mon), int(day), hr, int(mn))
        # Adjust to Pacific
        dt = dt + datetime.timedelta(hours=hours_diff)
        # Format short: M/D/YY H:MM AM/PM TZ
        short_ampm = "PM" if dt.hour >= 12 else "AM"
        short_hr = dt.hour % 12 or 12
        short_yr = str(dt.year)[2:]
        return f"{dt.month}/{dt.day}/{short_yr} {short_hr}:{dt.minute:02d} {short_ampm} {pt_abbr}"
    return deadline_str


def days_left_to_comment(deadline_str):
    """Return days left to comment, or None if unparseable."""
    import re, datetime
    if not deadline_str:
        return None
    tz_offsets = {
        "Pacific Standard Time": 0, "PST": 0, "Pacific Daylight Time": 0, "PDT": 0,
        "Alaskan Standard Time": -1, "AKST": -1, "Alaska Standard Time": -1,
        "Alaskan Daylight Time": 0, "AKDT": 0, "Alaska Daylight Time": 0,
        "Mountain Standard Time": 1, "MST": 1, "Mountain Daylight Time": 1, "MDT": 1,
        "Central Standard Time": 2, "CST": 2, "Central Daylight Time": 2, "CDT": 2,
        "Eastern Standard Time": 3, "EST": 3, "Eastern Daylight Time": 3, "EDT": 3,
    }
    hours_diff = 0
    for tz, diff in tz_offsets.items():
        if tz.lower() in deadline_str.lower():
            hours_diff = diff
            break
    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})\s*(AM|PM)', deadline_str, re.I)
    if not m:
        return None
    mon, day, yr, hr, mn, sc, ampm = m.groups()
    hr = int(hr)
    if ampm.upper() == 'PM' and hr != 12: hr += 12
    elif ampm.upper() == 'AM' and hr == 12: hr = 0
    deadline_dt = datetime.datetime(int(yr), int(mon), int(day), hr, int(mn))
    deadline_dt += datetime.timedelta(hours=hours_diff)
    now = datetime.datetime.now()
    delta = (deadline_dt.date() - now.date()).days
    return delta


app = Flask(__name__, static_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static'))
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-in-production")

STATUS_COLORS = {
    "Developing Proposal": "#9b72d8",
    "In Progress":         "#4a90d9",
    "On Hold":             "#e08848",
    "Completed":           "#5aaa48",
}

ANALYSIS_COLORS = {
    "Categorical Exclusion":          "#a83030",
    "Environmental Assessment":       "#c46a30",
    "Environmental Impact Statement": "#2d7a1f",
    "Uncategorized":                        "#999",
}

FORESTS = [
    {"name": "Mt. Baker-Snoqualmie National Forest", "code": "mbs",              "state": "WA"},
    {"name": "Olympic National Forest",              "code": "olympic",           "state": "WA"},
    {"name": "Okanogan-Wenatchee National Forest",   "code": "okanogan-wenatchee","state": "WA"},
    {"name": "Gifford Pinchot National Forest",      "code": "giffordpinchot",   "state": "WA"},
    {"name": "Colville National Forest",             "code": "colville",          "state": "WA"},
    {"name": "Rogue River-Siskiyou National Forest", "code": "rogue-siskiyou",   "state": "CA+OR"},
    {"name": "Wallowa-Whitman National Forest",      "code": "wallowa-whitman",  "state": "OR"},
    {"name": "Fremont-Winema National Forest",       "code": "fremont-winema",   "state": "OR"},
    {"name": "Deschutes National Forest",             "code": "deschutes",        "state": "OR"},
    {"name": "Mt. Hood National Forest",              "code": "mthood",           "state": "OR"},
    {"name": "Ochoco National Forest", "code": "ochoco", "state": "OR"},
    {"name": "Umatilla National Forest", "code": "umatilla", "state": "OR"},
    {"name": "Willamette National Forest", "code": "willamette", "state": "OR"},
    {"name": "Malheur National Forest", "code": "malheur", "state": "OR"},
    {"name": "Siuslaw National Forest", "code": "siuslaw", "state": "OR"},
    {"name": "Shasta-Trinity National Forest",       "code": "shasta-trinity",   "state": "CA"},
    {"name": "Inyo National Forest",                 "code": "inyo",              "state": "CA"},
    {"name": "Los Padres National Forest",           "code": "lospadres",         "state": "CA"},
    {"name": "Klamath National Forest",              "code": "klamath",           "state": "CA+OR"},
    {"name": "Chugach National Forest",              "code": "chugach",           "state": "AK"},
    {"name": "Tongass National Forest",              "code": "tongass",           "state": "AK"},
]

# Column order for forest summary
STATE_COLUMNS = ["CA", "CA+OR", "OR", "OR+WA", "WA", "AK"]

# Map forest code -> state for color lookup
FOREST_STATE_MAP = {f["code"]: f["state"] for f in FORESTS}

# Map forest code -> abbreviation used in multi-forest project names
FOREST_CODE_TO_ABBREV = {
    "mbs":                "MBS",
    "olympic":            "ONF",
    "okanogan-wenatchee": "Okan-Wen",
    "giffordpinchot":     "GPNF",
    "colville":           "Colville",
    "rogue-siskiyou":     "RRS",
    "wallowa-whitman":    "Wallowa-Whitman",
    "fremont-winema":     "Fremont-Winema",
    "deschutes":          "Deschutes",
    "mthood":             "Mt. Hood",
    "ochoco": "Ochoco",
    "umatilla": "Umatilla",
    "willamette": "Willamette",
    "malheur": "Malheur",
    "siuslaw": "Siuslaw",
    "shasta-trinity":     "Shasta-Trinity",
    "inyo":               "Inyo",
    "lospadres":          "Los Padres",
    "klamath":            "Klamath",
    "tongass":            "Tongass",
}

# Colors for each state column
STATE_COLORS = {
    "CA":    {"pill": "#cc3333", "label": "#8b1a1a"},
    "CA+OR": {"pill": "#c96a00", "label": "#7a3e00"},
    "OR":    {"pill": "#d4bc00", "label": "#6b5f00"},
    "OR+WA": {"pill": "#7a9a2f", "label": "#445a18"},
    "WA":    {"pill": "#2d7a1f", "label": "#1a4f0f"},
    "AK":    {"pill": "#5b4fa8", "label": "#352d6e"},
}


DATE_RANGES = [
    ("7",  "Last 7 days"),
    ("30", "Last 30 days"),
    ("90", "Last 90 days"),
]

STATUS_SORT_ORDER = {
    "Developing Proposal": 0,
    "In Progress":         1,
    "On Hold":             2,
    "Completed":           3,
}

CATEGORY_SORT_ORDER = {
    "extractive":  0,
    "mixed":       1,
    "restorative": 2,
}

IMPACT_SORT_ORDER = {
    "extractive":  0,
    "mixed":       1,
    "restorative": 2,
    None:          3,
}

ANALYSIS_SORT_ORDER = {
    "Environmental Impact Statement": 0,
    "Environmental Assessment":       1,
    "Categorical Exclusion":          2,
}

# These match against the purpose tag field (pipe-separated values from USFS)
EXTRACTIVE_KEYWORDS = [
    "forest products",
    "fuels management",
    "grazing management",
    "minerals and geology",
    "vegetation management (other than forest products)",
    "land management planning",
]

RESTORATIVE_KEYWORDS = [
    "climate change adaptation",
    "watershed management",
    "wildlife, fish, rare plants",
    "special area management",
]

# Road management appears in both lists so it always resolves to mixed
MIXED_KEYWORDS = [
    "road management",
]


def has_thinning_badge(project):
    purpose = (project.get("purpose") or "").lower()
    return "forest products" in purpose or "fuels management" in purpose


def has_wildfire_badge(project):
    purpose = (project.get("purpose") or "").lower()
    return "fuels management" in purpose or "vegetation management" in purpose


def classify_project(project):
    # Match against individual purpose tags (pipe-separated)
    purpose_tags = [
        t.strip().lower()
        for t in (project.get("purpose") or "").split("|")
        if t.strip()
    ]
    # Forest products always wins as extractive
    if "forest products" in purpose_tags:
        return "extractive"
    # Road management forces mixed unless already extractive
    if any(kw in purpose_tags for kw in MIXED_KEYWORDS):
        return "mixed"
    has_extractive  = any(kw in purpose_tags for kw in EXTRACTIVE_KEYWORDS)
    has_restorative = any(kw in purpose_tags for kw in RESTORATIVE_KEYWORDS)
    if has_extractive and has_restorative:
        return "mixed"
    elif has_extractive:
        return "extractive"
    elif has_restorative:
        return "restorative"
    return None



def extract_resource_data(project: dict) -> list:
    """Extract acres and board feet mentions from project description and purpose.
    Returns list of {descriptor, value} dicts, deduped by value."""
    import re

    text = (project.get("description") or "") + " " + (project.get("purpose") or "") + " " + (project.get("location_summary") or "")

    NUM = r"([\d,]+(?:\.\d+)?)"

    # Terms that indicate the "of X" context is a place/land designation, not a treatment type
    SKIP_CONTEXTS = {
        "national forest system lands", "national forest system land",
        "forest system lands", "forest system land",
        "national forest lands", "national forest land",
        "nfs lands", "nfs land", "the project area", "project area",
        "the fire perimeter", "fire perimeter",
    }

    # Patterns: (regex, descriptor, priority)
    # Lower priority = preferred when values identical
    patterns = [
        # Board feet — specific first
        (r"(?i)" + NUM + r"\s*(?:million board feet|MMBF)\s+of\s+old.?growth",           "Million Board Feet of Old Growth",         1),
        (r"(?i)" + NUM + r"\s*(?:million board feet|MMBF)\s+of\s+(?:second|young).?growth","Million Board Feet of Young/Second Growth", 1),
        (r"(?i)" + NUM + r"\s*(?:million board feet|MMBF)(?:\s+of\s+timber)?",            "Million Board Feet",                       3),
        (r"(?i)" + NUM + r"\s*(?:thousand board feet|MBF)\b",                             "Thousand Board Feet",                      2),
        # Acres — specific first
        (r"(?i)" + NUM + r"\s*acres?\s+of\s+old.?growth(?:\s+live\s+trees?)?",            "Acres of Old Growth",                      1),
        (r"(?i)" + NUM + r"\s*acres?\s+of\s+(?:second|young).?growth",                    "Acres of Young/Second Growth",             1),
        (r"(?i)" + NUM + r"\s*acres?\s+of\s+(?:live\s+)?(?:timber|trees)",                "Acres of Timber",                          1),
        (r"(?i)" + NUM + r"\s*acres?\s+of\s+(?:fire\s+)?salvage",                         "Acres of Salvage",                         1),
        (r"(?i)" + NUM + r"\s*acres?\s+of\s+commercial\s+thinning",                       "Acres of Commercial Thinning",             1),
        (r"(?i)" + NUM + r"\s*acres?\s+of\s+thinning",                                    "Acres of Thinning",                        1),
        (r"(?i)" + NUM + r"\s*acres?\s+of\s+(?:prescribed.?burn|underburn)",              "Acres of Prescribed Burn",                 1),
        (r"(?i)" + NUM + r"\s*acres?\s+of\s+(?:forest\s+)?(?:health\s+)?treatments?",    "Acres Treated",                            1),
        (r"(?i)" + NUM + r"\s*acres?\s+(?:of\s+)?(?:forest\s+)?(?:health\s+)?treatments?","Acres Treated",                           1),
        # Context capture — grab what follows "X acres of [context]"
        # Lookahead stops at sentence boundary or common conjunctions
        (r"(?i)" + NUM + r"\s*acres?\s+of\s+([\w,\s/]+?)(?=\s*(?:\.|,\s*for\b|,\s*and\b|\s+for\b|\s+to\b|$))", None, 2),
        # Reverse pattern: "X acres proposed for Y" or "X acres for Y"
        (r"(?i)" + NUM + r"\s*acres?\s+proposed\s+for\s+([\w,\s/]+?)(?=\.|$)", None, 2),
        # Approx before generic
        (r"(?i)(?:approximately|about|up to|approx\.?)\s+" + NUM + r"\s*acres?",          "Acres (approx.)",                          2),
        # Generic fallback
        (r"(?i)" + NUM + r"\s*acres?",                                                     "Acres",                                    4),
    ]

    hits = {}  # norm_value -> (descriptor, priority, raw_value)

    for pat, descriptor, priority in patterns:
        for m in re.finditer(pat, text):
            raw_value = m.group(1)
            norm = raw_value.replace(",", "")

            if descriptor is None:
                # Dynamic context
                try:
                    context = m.group(2).strip().rstrip(".,;")
                    context_lower = context.lower().strip()
                    # Skip generic land designations
                    if any(skip in context_lower for skip in SKIP_CONTEXTS):
                        continue
                    if len(context) < 3 or len(context) > 60:
                        continue
                    desc = "Acres of " + context.title()
                except Exception:
                    continue
            else:
                desc = descriptor

            if norm not in hits or priority < hits[norm][1]:
                hits[norm] = (desc, priority, raw_value)

    # Build result sorted by value descending
    results = []
    seen = set()
    for norm, (desc, priority, raw_value) in sorted(
        hits.items(),
        key=lambda x: float(x[0].replace(",", "") or 0),
        reverse=True
    ):
        key = (desc.lower(), norm)
        if key not in seen:
            seen.add(key)
            results.append({"descriptor": desc, "value": raw_value})

    return results

def load_ledger():
    """Load ledger.json — maps project_url -> {name, first_seen}."""
    path = os.path.join(os.path.dirname(__file__), "ledger.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_projects():
    json_path = os.path.join(os.path.dirname(__file__), "projects.json")
    if not os.path.exists(json_path):
        return [], "never"
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    scraped_at = data.get("scraped_at", "")[:10]
    projects = data.get("projects", [])
    ledger = load_ledger()
    for p in projects:
        url = p.get("project_url", "")
        if url in ledger and ledger[url].get("first_seen"):
            p["first_seen"] = ledger[url]["first_seen"]
        p["category"] = classify_project(p)
        p["_scraped_resources"] = extract_resource_data(p)
        # Extract key milestone dates
        scoping = decision = implementation = ""
        for m in p.get("milestones", []):
            name = m.get("milestone", "").lower()
            date = m.get("date", "")
            if "scoping" in name and "start" in name:
                scoping = date
            elif "decision" in name and not decision:
                decision = date
            elif "implementation" in name and not implementation:
                implementation = date
        p["scoping_start"]    = scoping
        p["decision_date"]    = decision
        p["implementation_date"] = implementation
    return projects, scraped_at


def filter_projects(projects, search="", forest_code="", status="",
                    days="", categories=None, sort="", sort2="", recent_cutoff=""):
    if categories is None: categories = []
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
            if not p.get("is_multi_forest"):
                continue
            # For multi-forest projects, check if the forest name or code appears
            fn = p.get("forest_name", "")
            abbrev = FOREST_CODE_TO_ABBREV.get(forest_code, '')
            if not abbrev or abbrev not in fn:
                continue
        if status and p.get("status") != status:
            continue
        if categories:
            IMPACT_CATS = {"extractive", "mixed", "restorative", "unclassified"}
            UNIQUE_CATS = {"taking_comments", "active", "newly_added"}
            selected_impact = [c for c in categories if c in IMPACT_CATS]
            selected_unique = [c for c in categories if c in UNIQUE_CATS]

            # Impact: OR logic — must match at least one if any selected
            if selected_impact:
                impact_match = False
                for cat in selected_impact:
                    if cat == "unclassified" and not p.get("category"):
                        impact_match = True; break
                    elif p.get("category") == cat:
                        impact_match = True; break
                if not impact_match:
                    continue

            # Unique: AND logic — must match all selected
            for cat in selected_unique:
                if cat == "taking_comments" and not p.get("accepting_comments"):
                    continue  # outer loop handles skip
                if cat == "active" and p.get("status") not in ("In Progress", "Developing Proposal"):
                    break
                if cat == "newly_added" and not (p.get("first_seen", "")[:10] >= recent_cutoff):
                    break
            else:
                pass  # all unique filters passed

            # Re-check unique filters cleanly
            unique_match = True
            for cat in selected_unique:
                if cat == "taking_comments" and not p.get("accepting_comments"):
                    unique_match = False; break
                if cat == "active" and p.get("status") not in ("In Progress", "Developing Proposal"):
                    unique_match = False; break
                if cat == "newly_added" and not (p.get("first_seen", "")[:10] >= recent_cutoff):
                    unique_match = False; break
            if not unique_match:
                continue
        if cutoff:
            first_seen_str = p.get("first_seen", "")
            if not first_seen_str:
                continue
            try:
                fs = first_seen_str[:10]  # trim to YYYY-MM-DD
                first_seen_dt = datetime.datetime.strptime(fs, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
                if first_seen_dt < cutoff:
                    continue
            except ValueError:
                continue
        results.append(p)

    if sort == "cara_newest":
        results.sort(key=lambda p: (0 if p.get("accepting_comments") else 1, p.get("first_seen", "") + "z" if not p.get("accepting_comments") else ""), reverse=False)
        results.sort(key=lambda p: (0 if p.get("accepting_comments") else 1,))
        # stable secondary: newest within each group
        from operator import itemgetter
        cara = sorted([p for p in results if p.get("accepting_comments")], key=lambda p: p.get("first_seen",""), reverse=True)
        rest = sorted([p for p in results if not p.get("accepting_comments")], key=lambda p: p.get("first_seen",""), reverse=True)
        results[:] = cara + rest
    elif sort == "newest":
        results.sort(key=lambda p: p.get("first_seen", ""), reverse=True)
    elif sort == "oldest":
        results.sort(key=lambda p: p.get("first_seen", ""))
    elif sort == "name":
        results.sort(key=lambda p: p.get("project_name", "").lower())
    elif sort == "forest":
        results.sort(key=lambda p: p.get("forest_name", "").lower())
    elif sort == "analysis":
        results.sort(key=lambda p: ANALYSIS_SORT_ORDER.get(p.get("analysis_type", ""), 99))
    elif sort == "status":
        results.sort(key=lambda p: (
            STATUS_SORT_ORDER.get(p.get("status", ""), 99),
            CATEGORY_SORT_ORDER.get(p.get("category", ""), 3),
        ))

    def date_key(field, p, reverse=False):
        import re
        d = (p.get(field) or "").replace("\xa0", " ").replace("(Estimated)", "").strip()
        # Try MM/DD/YYYY
        m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', d)
        if m:
            return f"{m.group(3)}/{m.group(1).zfill(2)}/{m.group(2).zfill(2)}"
        # Try MM/YYYY — assign day 01
        m = re.match(r'(\d{1,2})/(\d{4})', d)
        if m:
            return f"{m.group(2)}/{m.group(1).zfill(2)}/01"
        return "0000/00/00" if reverse else "9999/99/99"

    if sort == "scoping_newest":
        results.sort(key=lambda p: date_key("scoping_start", p, True), reverse=True)
    elif sort == "scoping_oldest":
        results.sort(key=lambda p: date_key("scoping_start", p))
    elif sort == "decision_newest":
        results.sort(key=lambda p: date_key("decision_date", p, True), reverse=True)
    elif sort == "decision_oldest":
        results.sort(key=lambda p: date_key("decision_date", p))
    elif sort == "implementation_newest":
        results.sort(key=lambda p: date_key("implementation_date", p, True), reverse=True)
    elif sort == "implementation_oldest":
        results.sort(key=lambda p: date_key("implementation_date", p))
    elif sort == "impact":
        results.sort(key=lambda p: IMPACT_SORT_ORDER.get(p.get("category"), 3))

    # Secondary sort
    if sort2:
        if sort2 == "newest":
            results.sort(key=lambda p: p.get("first_seen", ""), reverse=True)
        elif sort2 == "oldest":
            results.sort(key=lambda p: p.get("first_seen", ""))
        elif sort2 == "name":
            results.sort(key=lambda p: p.get("project_name", "").lower())
        elif sort2 == "forest":
            results.sort(key=lambda p: p.get("forest_name", "").lower())
        elif sort2 == "status":
            results.sort(key=lambda p: STATUS_SORT_ORDER.get(p.get("status", ""), 99))
        elif sort2 == "impact":
            results.sort(key=lambda p: IMPACT_SORT_ORDER.get(p.get("category"), 3))
        elif sort2 == "analysis":
            results.sort(key=lambda p: ANALYSIS_SORT_ORDER.get(p.get("analysis_type", ""), 99))
        elif sort2 == "scoping_newest":
            results.sort(key=lambda p: date_key("scoping_start", p, True), reverse=True)
        elif sort2 == "scoping_oldest":
            results.sort(key=lambda p: date_key("scoping_start", p))
        elif sort2 == "decision_newest":
            results.sort(key=lambda p: date_key("decision_date", p, True), reverse=True)
        elif sort2 == "decision_oldest":
            results.sort(key=lambda p: date_key("decision_date", p))
        elif sort2 == "implementation_newest":
            results.sort(key=lambda p: date_key("implementation_date", p, True), reverse=True)
        elif sort2 == "implementation_oldest":
            results.sort(key=lambda p: date_key("implementation_date", p))

    return results


PAGE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LFDC NEPA Tracker</title>
    <link rel="icon" type="image/png" href="/static/LFDC_Logo.png">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Lexend:wght@400;500;600;700&family=Outfit:wght@400;500;600&family=Poppins:wght@300;400;500;600&display=swap" rel="stylesheet">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }

        :root {
            --bg:       #e8ede3;
            --bg2:      #ffffff;
            --bg3:      #f2f5ee;
            --border:   #d0d0c8;
            --border2:  #b8b8b0;
            --text:     #111111;
            --text-muted: #444444;
            --text-dim: #777777;
            --accent:   #2d7a1f;
            --green:    #2d7a1f;
            --red:      #a83030;
            --orange:   #c46a30;
        }

        body { font-family: 'Poppins', sans-serif; background: var(--bg); color: var(--text); font-size: 14px; line-height: 1.6; }

        /* ── Search bar ── */
        .top-search-bar { background: var(--bg3); border-bottom: 1px solid var(--border); padding: 8px 20px; }
        .top-search-inner { max-width: 1150px; margin: 0 auto; display: flex; justify-content: flex-end; }
        .header-search { display: flex; align-items: center; gap: 0; }
        .header-search input[type="text"] { padding: 7px 14px; border: 1px solid #ccc; border-radius: 0; font-family: 'Poppins', sans-serif; font-size: 0.88rem; background: white; color: #1a1a1a; outline: none; flex: 1; }
        .header-search input[type="text"]::placeholder { color: #aaa; }
        .header-search input[type="text"]:focus { border-color: #888; }
        .header-search button { padding: 7px 18px; background: #e05a2b; color: white; border: none; border-radius: 0; font-family: 'Poppins', sans-serif; font-size: 0.88rem; font-weight: 400; cursor: pointer; white-space: nowrap; }
        .header-search button:hover { background: #c44d22; }

        /* ── Forest summary ── */
        .forest-summary { background: #f7f7f0; border-bottom: 1px solid var(--border); padding: 10px 20px; }
        .forest-summary-inner { max-width: 1150px; margin: 0 auto; display: flex; flex-direction: column; gap: 6px; }
        .forest-cols-row { display: flex; gap: 0; justify-content: center; width: 100%; }
        .forest-totals-row { display: flex; flex-direction: row; align-items: center; justify-content: flex-end; gap: 12px; width: 100%; }
        .forest-reset-btn { display: inline-block; padding: 5px 12px; background: #e05a2b; color: white; font-family: 'Poppins', sans-serif; font-size: 0.62rem; font-weight: 400; border: none; cursor: pointer; text-decoration: none; white-space: nowrap; }
        .forest-reset-btn:hover { background: #c44d22; }
        .forest-col { display: flex; flex-direction: column; gap: 4px; flex: 1; min-width: 0; padding: 0 8px; }
        .forest-col-label { font-size: 0.6rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.6px; color: var(--text-dim); margin-bottom: 2px; }
        .forest-pill { display: flex; align-items: center; justify-content: space-between; gap: 5px; background: var(--accent); border-radius: 20px; padding: 3px 10px; font-size: 0.7rem; font-weight: 400; color: white; white-space: nowrap; width: 100%; box-sizing: border-box; text-decoration: none; transition: opacity 0.15s, box-shadow 0.15s; }
        .forest-pill.pill-selected { box-shadow: 0 0 0 2px white, 0 0 0 3px currentColor; }
        .forest-pill-count { background: rgba(255,255,255,0.25); border-radius: 10px; padding: 0 5px; font-size: 0.62rem; font-weight: 700; color: white; }
        .summary-totals { color: var(--text-muted); font-size: 0.72rem; text-align: right; }
        .summary-totals strong { color: var(--text); font-weight: 700; }

        /* ── Container ── */
        .container { max-width: 1150px; margin: 0 auto; padding: 20px; }

        /* ── Filter bar ── */
        .filters-wrapper { display: flex; justify-content: flex-end; margin-bottom: 10px; }
        .filters { background: var(--bg2); border: 1px solid var(--border); padding: 8px 12px; display: inline-flex; gap: 10px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
        .filters label { display: block; font-size: 0.58rem; font-weight: 600; color: var(--text-dim); margin-bottom: 5px; text-transform: uppercase; letter-spacing: 0.8px; }
        .filters select { padding: 5px 8px; border: 1px solid var(--border2); font-family: 'Poppins', sans-serif; font-size: 0.82rem; font-weight: 500; background: var(--bg3); color: var(--text); width: 170px; cursor: pointer; }
        .filters select:focus { outline: none; border-color: var(--accent); }
        .filters a.clear { padding: 7px 12px; color: var(--text-muted); font-size: 0.8rem; font-weight: 600; text-decoration: none; }
        .filters a.clear:hover { color: var(--text); }

        /* ── Category filter buttons ── */
        .category-filters { display: flex; gap: 10px; padding: 0 0 0 24px; align-items: center; flex-wrap: wrap; justify-content: flex-end; margin-bottom: 14px; }
        .category-filters span { font-size: 0.62rem; font-weight: 700; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.8px; }
        .cat-btn { display: inline-flex; align-items: center; gap: 7px; padding: 5px 14px; border-radius: 20px; border: 1.5px solid transparent; font-family: 'Poppins', sans-serif; font-size: 0.78rem; font-weight: 700; cursor: pointer; text-decoration: none; transition: all 0.15s; letter-spacing: 0.2px; }
        .cat-btn .dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
        .cat-btn.extractive  { border-color: var(--red);    color: var(--red);    background: rgba(168,48,48,0.07); }
        .cat-btn.extractive.active  { background: var(--red);    color: white; border-width: 3px; }
        .cat-btn .dot.extractive-dot  { background: var(--red); }
        .cat-btn.restorative { border-color: var(--green);  color: var(--green);  background: rgba(45,122,31,0.07); }
        .cat-btn.restorative.active { background: var(--green);  color: white; border-width: 3px; }
        .cat-btn .dot.restorative-dot { background: var(--green); }
        .cat-btn.mixed       { border-color: var(--orange); color: var(--orange); background: rgba(196,106,48,0.07); }
        .cat-btn.mixed.active       { background: var(--orange); color: white; border-width: 3px; }
        .cat-btn .dot.mixed-dot       { background: var(--orange); }
        .cat-btn.unclassified { border-color: #888; color: #555; background: rgba(128,128,128,0.07); }
        .cat-btn.unclassified.active { background: #888; color: white; border-width: 3px; }
        .cat-btn .dot.unclassified-dot { background: #888; }
        .cat-btn.newly-added { border-color: #6aabdf; border-width: 3px; color: #5599cc; background: rgba(106,171,223,0.1); padding: 6px 37px; }
        .cat-btn.newly-added.active { background: #6aabdf; color: white; }
        .cat-btn .dot.newly-added-dot { background: #6aabdf; }
        .cat-btn.taking-comments { border-color: #a83030; border-width: 3px; color: #a83030; background: #e8e8e4; padding: 6px 37px; }
        .cat-btn.taking-comments.active { background: #a83030; color: white; }
        .cat-btn .dot.taking-comments-dot { background: #a83030; }
        .cat-btn.active-filter { border-color: var(--green); border-width: 3px; color: #1a4f0f; background: rgba(45,122,31,0.15); padding: 6px 37px; }
        .cat-btn.active-filter.active { background: var(--green); color: white; }
        .cat-btn .dot.active-filter-dot { background: var(--green); }
        .cat-btn.active .dot { background: currentColor; }
        .category-disclaimer { font-size: 0.62rem; color: var(--text-dim); font-style: italic; }
        .category-disclaimer-row { display: flex; justify-content: flex-end; padding: 3px 0 6px 0; }

        /* ── Results header ── */
        .results-header { font-size: 0.78rem; color: var(--text-muted); margin-bottom: 12px; margin-top: 4px; font-weight: 500; }
        .results-header strong { color: var(--text); font-weight: 700; }

        /* ── Annotation (Suggested Comment) ── */
        .annotation-box { margin: 0; display: inline-block; width: auto; padding-bottom: 25px; }
        .annotation-box.expanded { width: 100%; }
        .annotation-toggle { background: #6aabdf; color: white; border: none; padding: 5px 14px; font-size: 0.918rem; font-family: 'Poppins', sans-serif; cursor: pointer; font-weight: 200; width: auto; text-align: left; display: flex; align-items: center; gap: 8px; white-space: nowrap; letter-spacing: 0.8px; }
        .annotation-toggle:hover { background: #5599cc; }
        .ann-arrow { display: inline-block; transition: transform 0.2s; font-style: normal; }
        .annotation-content { border: 2px solid #6aabdf; border-top: none; background: #f0f4ff; padding: 10px 14px; width: 100%; box-sizing: border-box; }
        .annotation-intro { font-size: 0.82rem; color: #1a1a1a; line-height: 1.5; font-weight: 700; margin-bottom: 8px; }
        .annotation-text { font-size: 0.82rem; color: #1a1a1a; line-height: 1.5; white-space: pre-wrap; margin-bottom: 8px; }
        .annotation-copy { background: #6aabdf; color: white; border: none; padding: 4px 12px; font-size: 0.75rem; cursor: pointer; font-family: 'Poppins', sans-serif; }
        .annotation-copy:hover { background: #5599cc; }

        /* ── Project card ── */
        .project-card { font-family: 'Poppins', sans-serif; background: var(--bg2); border: 1px solid var(--border); border-radius: 0; padding: 0 0 0 28px; margin-bottom: 10px; transition: border-color 0.15s, box-shadow 0.15s; position: relative; overflow: hidden; }
        .project-card:hover { border-color: var(--border2); box-shadow: 2px 2px 0 rgba(0,0,0,0.08); }

        /* Impact bar (vertical left strip) */
        .card-category-bar { position: absolute; left: 0; top: 0; bottom: 0; width: 28px; display: flex; align-items: center; justify-content: center; }
        .card-category-label { writing-mode: vertical-rl; transform: rotate(180deg); font-size: 0.65rem; font-weight: 400; color: white; letter-spacing: 1.5px; text-transform: uppercase; white-space: nowrap; user-select: none; }
        .card-category-top { display: none; font-size: 0.62rem; font-weight: 400; color: white; letter-spacing: 1.5px; text-transform: uppercase; padding: 3px 12px; }

        /* Card body: center + right columns */
        .card-body { display: flex; flex-direction: row; gap: 0; align-items: stretch; }

        /* Center column */
        .card-body-left { flex: 1; display: flex; flex-direction: column; min-width: 0; border-right: 1px solid var(--border); padding-left: 25px; }
        .card-body-left .description { font-size: 0.82rem; color: var(--text-muted); line-height: 1.6; font-weight: 400; flex: 1; padding-top: 25px; padding-bottom: 25px; padding-right: 25px; }
        .card-body-left .left-bottom { margin-top: auto; display: flex; flex-direction: column; gap: 0; }

        /* Right column */
        .card-body-right { display: flex; flex-direction: column; align-items: center; justify-content: flex-start; gap: 6px; flex-shrink: 0; width: 305px; background: #f4f4f0; padding: 25px 0; box-sizing: border-box; align-self: stretch; margin: 0; overflow: hidden; }
        .card-body-right-top { display: flex; flex-direction: column; align-items: center; gap: 6px; width: 255px; flex: 1; }

        /* Forest + project name */
        .forest-tag { font-size: 1.3rem; font-weight: 700; color: var(--accent); text-transform: uppercase; letter-spacing: 0.8px; margin: 0; }
        .btn-title-wrap { display: flex; align-items: center; gap: 8px; margin: 0; }
        .project-title-text { font-family: 'Poppins', sans-serif; font-size: 1.3rem; font-weight: 400; color: #1a1a1a; letter-spacing: 0.8px; line-height: 1.3; display: block; }

        /* Status badge */
        .status-badge { display: block; padding: 3px 10px; border-radius: 0; font-size: 0.918rem; font-weight: 200; font-family: 'Poppins', sans-serif; color: white; white-space: nowrap; letter-spacing: 0.8px; text-align: center; width: 255px; box-sizing: border-box; }
        .ce-badge { display: block; padding: 3px 10px; border-radius: 0; font-size: 0.918rem; font-weight: 200; font-family: 'Poppins', sans-serif; color: white; white-space: nowrap; letter-spacing: 0.8px; text-align: center; width: 255px; box-sizing: border-box; background: #d4b800; margin-top: 6px; }

        /* NEW badge */
        .new-badge { display: inline-block; background: rgba(106,171,223,0.1); color: #6aabdf; border: 2px solid #6aabdf; border-radius: 0; font-size: 0.78rem; font-weight: 700; padding: 3px 8px; vertical-align: middle; margin-left: 6px; letter-spacing: 0.3px; }

        /* Taking Comments Now badge */
        .comment-open-badge { display: inline-flex; flex-direction: column; align-items: center; padding: 2px 6px; border-radius: 0; background: #e8e8e4; border: 3px solid #a83030; color: #a83030; font-weight: 700; font-size: 0.82rem; line-height: 1.2; text-align: center; animation: pulse-yellow 2.5s ease-in-out infinite; flex-shrink: 0; box-shadow: 0 2px 8px rgba(168,48,48,0.2); width: 255px; box-sizing: border-box; }
        .comment-open-badge .badge-title { font-size: 0.88rem; font-weight: 800; letter-spacing: 0.4px; }
        .comment-open-badge .badge-deadline { font-family: 'Poppins', sans-serif; font-size: 0.72rem; font-weight: 200; opacity: 0.9; margin-top: 2px; }
        @keyframes pulse-yellow { 0%, 100% { opacity: 1; box-shadow: 0 2px 8px rgba(168,48,48,0.2); } 50% { opacity: 0.8; box-shadow: 0 2px 16px rgba(168,48,48,0.4); } }

        /* Learn About badges */
        .wildfire-badge { display: flex; align-items: center; justify-content: center; gap: 8px; background: #8fa68e; color: white; border: none; border-radius: 0; font-family: 'Poppins', sans-serif; font-size: 0.918rem; font-weight: 200; text-transform: none; letter-spacing: 0.8px; padding: 2px 4px; width: 230px; box-sizing: border-box; cursor: pointer; text-decoration: none; }
        .wildfire-badge:hover { background: #7a9079; }

        /* LFDC Commented badge */
        .lfdc-commented-badge { display: flex; align-items: center; justify-content: center; gap: 6px; background: #8fa68e; color: white; border: none; border-radius: 0; font-family: 'Poppins', sans-serif; font-size: 0.918rem; font-weight: 200; text-transform: uppercase; letter-spacing: 0.8px; padding: 4px 6px; width: 255px; box-sizing: border-box; cursor: pointer; text-decoration: none; }
        .lfdc-commented-badge:hover { background: #7a9079; }

        /* Milestone table */
        .milestone-section { width: 255px; border: 1px solid var(--border2); border-radius: 0; overflow: hidden; background: #e8e8e4; flex-shrink: 0; }
        .card-body-right .milestone-section { width: 255px; box-sizing: border-box; margin: 0; }
        .milestone-table { width: 100%; border-collapse: collapse; font-size: 0.918rem; font-family: 'Poppins', sans-serif; font-weight: 200; letter-spacing: 0.8px; }
        .milestone-table th { text-align: left; padding: 4px 10px; background: #d8d8d4; color: #555; font-weight: 400; font-size: 0.6rem; text-transform: uppercase; letter-spacing: 0.8px; border-bottom: 1px solid #c0c0bc; }
        .resource-table { width: 100%; border-collapse: collapse; font-size: 0.6rem; font-family: 'Poppins', sans-serif; font-weight: 200; letter-spacing: 0.8px; }
        .resource-table th { text-align: left; padding: 4px 10px; background: #8fa68e; color: white; font-weight: 400; font-size: 0.6rem; text-transform: uppercase; letter-spacing: 0.8px; border-bottom: 1px solid #7a9479; }
        .resource-table td { padding: 4px 10px; border-bottom: 1px solid #b0c4af; color: #2a3a2a; font-weight: 200; background: #c5d4c4; }
        .resource-table tr:last-child td { border-bottom: none; }
        .resource-table td.amount-cell { white-space: nowrap; text-align: right; }
        .milestone-table td { padding: 4px 10px; border-bottom: 1px solid var(--border); color: var(--text); font-weight: 200; font-size: 0.6rem; }
        .milestone-table tr:last-child td { border-bottom: none; }
        .milestone-table td.date-cell { white-space: nowrap; color: var(--text-muted); text-align: right; }
        .milestone-table td.date-cell.estimated { color: var(--text-dim); font-style: italic; }

        /* Comment buttons */
        .comment-buttons { display: flex; gap: 12px; margin: 0; flex-wrap: wrap; padding-bottom: 12px; }
        .btn-comment { display: inline-block; padding: 5px 12px; border-radius: 0; font-family: 'Poppins', sans-serif; font-size: 0.918rem; font-weight: 200; text-decoration: none; transition: opacity 0.15s; white-space: nowrap; letter-spacing: 0.8px; }
        .btn-comment:hover { opacity: 0.82; }
        .btn-comment.project-link { background: white; color: #c94f1a; border: 1px solid #c94f1a; }
        .btn-comment.project-link:hover { background: #fff4ef; color: #a33d12; }
        .btn-comment.primary { background: white; color: #6aabdf; border: 1px solid #6aabdf; }
        .btn-comment.primary:hover { background: #e8f4fd !important; color: #3a7aad !important; opacity: 1; }
        .btn-comment.secondary { background: white; color: #d4b800; border: 1px solid #d4b800; }
        .btn-comment.secondary:hover { background: #fffde6; color: #a38e00; }
        .btn-comment.primary-inactive { background: white; color: #999; border: 1px solid #b8b8b4; cursor: pointer; }
        .btn-comment.primary-inactive:hover { background: #f8f8f8; color: #777; }
        @keyframes pulse-blue { 0%, 100% { box-shadow: 0 0 0 0 rgba(106,171,223,0.7); } 50% { box-shadow: 0 0 0 10px rgba(106,171,223,0); } }
        .btn-comment.primary.pulsing { animation: pulse-blue 2s ease-in-out infinite; background: transparent !important; color: #6aabdf !important; border: 1px solid #6aabdf !important; }
        .btn-comment.primary.pulsing:hover { background: #6aabdf !important; color: white !important; }

        /* Meta */
        .meta { font-size: 0.68rem; color: var(--text-dim); display: flex; flex-wrap: wrap; gap: 6px; margin: 0; padding-bottom: 25px; }

        /* Desktop/mobile visibility */
        .desktop-only { display: flex; }
        .mobile-only  { display: none; }

        /* ── Mobile ── */
        @media (max-width: 680px) {
            html { font-size: 85%; }
            html, body { max-width: 100%; overflow-x: hidden; }
            .forest-cols-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; width: 100%; }
            .forest-col { width: 100% !important; flex: unset !important; padding: 0 !important; }
            .forest-pill { width: 100%; box-sizing: border-box; }
            .forest-col-group { display: flex; flex-direction: column; gap: 4px; }
            .filters { gap: 8px; }
            .filters select { width: 100%; }
            .container { padding: 10px; }
            .desktop-only { display: none !important; }
            .mobile-only  { display: flex !important; }
            div.mobile-only { display: flex !important; }
            .forest-col-group.mobile-only { display: flex !important; flex-direction: column; gap: 8px; }
            .forest-col.desktop-only { display: none !important; visibility: hidden !important; pointer-events: none !important; }
            .card-category-bar { display: none !important; }
            .card-category-top { display: block !important; }
            .project-card { display: flex !important; flex-direction: column !important; padding: 0 !important; }
            .card-body { flex-direction: column; width: 100%; }
            .card-body-left { width: 100%; padding: 12px; }
            .card-body-right { display: none !important; }
            .milestone-section { width: 100% !important; padding-bottom: 12px; }
            .comment-buttons { flex-direction: column; gap: 6px; }
            .btn-comment { width: 100%; text-align: center; justify-content: center; }
            .comment-open-badge { width: 100% !important; box-sizing: border-box; font-size: 0.72rem !important; padding: 5px 10px !important; margin-bottom: 6px; }
            .comment-open-badge .badge-title { font-size: 0.76rem !important; }
            .comment-open-badge .badge-deadline { font-size: 0.65rem !important; }
            .meta { margin-top: 10px; padding-bottom: 8px; }
            .milestone-table tr:last-child td { padding-bottom: 2px; }
        }
    </style>
</head>
<body>

<div class="top-search-bar">
    <div class="top-search-inner">
        <form class="header-search" method="GET" action="/" id="searchform" style="position:relative;">
            <input type="hidden" name="forest"   value="{{ selected_forest }}">
            <input type="hidden" name="status"   value="{{ selected_status }}">
            <input type="hidden" name="days"     value="{{ selected_days }}">
            <input type="hidden" name="sort"     value="{{ selected_sort }}">
            <input type="hidden" name="sort2"    value="{{ selected_sort2 }}">
            <input type="hidden" name="category" value="{{ selected_category_str }}">
            <input type="hidden" name="show_inactive" value="{{ '1' if show_inactive else '0' }}">
            <input type="hidden" name="forests"  value="{{ selected_forests_str }}">
            <div style="position:relative; display:inline-block;">
                <input type="text" name="q" id="search-q"
                       placeholder="Search projects..."
                       value="{{ search }}"
                       autocomplete="off">
                <button type="button" id="search-clear"
                        onclick="document.getElementById('search-q').value=''; document.getElementById('search-q').dispatchEvent(new Event('input')); this.style.display='none';"
                        style="position:absolute; right:6px; top:50%; transform:translateY(-50%); background:none; border:none; color:#aaa; font-size:1rem; cursor:pointer; padding:0; line-height:1; display:{{ 'flex' if search else 'none' }};">✕</button>
            </div>
            <button type="submit">Search</button>
        </form>
    </div>
</div>

<!-- Forest summary bar -->
<div class="forest-summary">
    <div class="forest-summary-inner">
        <div class="forest-cols-row">
            <!-- Desktop: individual columns. Mobile: left group (CA/CA+OR/OR), right group (WA/AK) -->
            {% set left_states = ['CA', 'CA+OR', 'OR'] %}
            {% set right_states = ['WA', 'AK'] %}

            <!-- Left mobile group -->
            <div class="forest-col-group mobile-only" style="display:none;">
                {% for state in left_states %}
                {% set col_forests = forests|selectattr('state','eq',state)|sort(attribute='name')|list %}
                {% if col_forests %}
                {% set sc = state_colors.get(state, {}) %}
                <div class="forest-col">
                    <div class="forest-col-label" style="color:{{ sc.get('label','var(--text-dim)') }};">{{ state }}</div>
                    {% for f in col_forests %}
                    {% set is_sel = f.code in selected_forests %}
                    <a href="{{ toggle_forest_url(f.code, selected_forests_str) }}"
                       class="forest-pill {{ 'pill-selected' if is_sel else '' }}"
                       style="background:{{ sc.get('pill','var(--accent)') }}; opacity:{{ '1' if (not selected_forests or is_sel) else '0.4' }}; text-decoration:none;">
                        {{ f.name.replace('National Forest', 'NF') }}
                        <span class="forest-pill-count">{{ forest_counts[f.code].total }}</span>
                    </a>
                    {% endfor %}
                </div>
                {% endif %}
                {% endfor %}
            </div>

            <!-- Right mobile group -->
            <div class="forest-col-group mobile-only" style="display:none;">
                {% for state in right_states %}
                {% set col_forests = forests|selectattr('state','eq',state)|sort(attribute='name')|list %}
                {% if col_forests %}
                {% set sc = state_colors.get(state, {}) %}
                <div class="forest-col">
                    <div class="forest-col-label" style="color:{{ sc.get('label','var(--text-dim)') }};">{{ state }}</div>
                    {% for f in col_forests %}
                    {% set is_sel = f.code in selected_forests %}
                    <a href="{{ toggle_forest_url(f.code, selected_forests_str) }}"
                       class="forest-pill {{ 'pill-selected' if is_sel else '' }}"
                       style="background:{{ sc.get('pill','var(--accent)') }}; opacity:{{ '1' if (not selected_forests or is_sel) else '0.4' }}; text-decoration:none;">
                        {{ f.name.replace('National Forest', 'NF') }}
                        <span class="forest-pill-count">{{ forest_counts[f.code].total }}</span>
                    </a>
                    {% endfor %}
                </div>
                {% endif %}
                {% endfor %}
            </div>

            <!-- Desktop: individual columns (hidden on mobile) -->
            {% for state in state_columns %}
            {% set col_forests = forests|selectattr('state','eq',state)|sort(attribute='name')|list %}
            {% if col_forests %}
            {% set sc = state_colors.get(state, {}) %}
            <div class="forest-col desktop-only" style="display:flex;">
                <div class="forest-col-label" style="color:{{ sc.get('label','var(--text-dim)') }};">{{ state }}</div>
                {% for f in col_forests %}
                {% set is_sel = f.code in selected_forests %}
                <a href="{{ toggle_forest_url(f.code, selected_forests_str) }}"
                   class="forest-pill {{ 'pill-selected' if is_sel else '' }}"
                   style="background:{{ sc.get('pill','var(--accent)') }}; opacity:{{ '1' if (not selected_forests or is_sel) else '0.4' }}; text-decoration:none;">
                    {{ f.name.replace('National Forest', 'NF') }}
                    <span class="forest-pill-count">{{ forest_counts[f.code].total }}</span>
                </a>
                {% endfor %}
            </div>
            {% endif %}
            {% endfor %}
        </div>
        <div class="forest-totals-row">
            <span class="summary-totals">
                <strong>{{ forest_counts.values()|sum(attribute='total') + multi_count }}</strong> total
            </span>
            <a href="/" class="forest-reset-btn">Reset</a>
        </div>
    </div>
</div>

<div class="container">

    <div class="filters-wrapper">
    <form class="filters" method="GET" action="/">
        <input type="hidden" name="q"        value="{{ search }}">
        <input type="hidden" name="category" value="{{ selected_category_str }}">
        <input type="hidden" name="forests"  value="{{ selected_forests_str }}">
        <input type="hidden" name="forest"   value="{{ selected_forest }}">
        <div>
            <label for="status">Status</label>
            <select id="status" name="status" onchange="this.form.submit()">
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
            <select id="days" name="days" onchange="this.form.submit()">
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
            <label for="sort">Sort by</label>
            <select id="sort" name="sort" onchange="this.form.submit()">
                <option value="cara_newest" {% if selected_sort == "cara_newest" %}selected{% endif %}>Default</option>
                <option value="newest"   {% if selected_sort == "newest"   %}selected{% endif %}>Newest first</option>
                <option value="oldest"   {% if selected_sort == "oldest"   %}selected{% endif %}>Oldest first</option>
                <option value="name"     {% if selected_sort == "name"     %}selected{% endif %}>Project name A–Z</option>
                <option value="forest"   {% if selected_sort == "forest"   %}selected{% endif %}>Forest</option>
                <option value="status"   {% if selected_sort == "status"   %}selected{% endif %}>Status</option>
                <option value="impact"        {% if selected_sort == "impact"        %}selected{% endif %}>Impact category</option>
                <option value="scoping_newest"        {% if selected_sort == "scoping_newest"        %}selected{% endif %}>Scoping date newest</option>
                <option value="decision_newest"       {% if selected_sort == "decision_newest"       %}selected{% endif %}>Decision date newest</option>
                <option value="implementation_newest" {% if selected_sort == "implementation_newest" %}selected{% endif %}>Implementation newest</option>
            </select>
        </div>
        <div>
            <label for="sort2">Then sort by</label>
            <select id="sort2" name="sort2" onchange="this.form.submit()">
                <option value="">None</option>
                <option value="newest"   {% if selected_sort2 == "newest"   %}selected{% endif %}>Newest first</option>
                <option value="oldest"   {% if selected_sort2 == "oldest"   %}selected{% endif %}>Oldest first</option>
                <option value="name"     {% if selected_sort2 == "name"     %}selected{% endif %}>Project name A–Z</option>
                <option value="forest"   {% if selected_sort2 == "forest"   %}selected{% endif %}>Forest</option>
                <option value="status"   {% if selected_sort2 == "status"   %}selected{% endif %}>Status</option>
                <option value="impact"   {% if selected_sort2 == "impact"   %}selected{% endif %}>Impact category</option>
                <option value="scoping_newest"        {% if selected_sort2 == "scoping_newest"        %}selected{% endif %}>Scoping date newest</option>
                <option value="decision_newest"       {% if selected_sort2 == "decision_newest"       %}selected{% endif %}>Decision date newest</option>
                <option value="implementation_newest" {% if selected_sort2 == "implementation_newest" %}selected{% endif %}>Implementation newest</option>
            </select>
        </div>
        {% if search or selected_forest or selected_status or selected_days or selected_category_str or selected_sort or selected_sort2 %}
        <a class="clear" href="/">Clear all</a>
        {% endif %}
    </form>
    </div>
    <div class="category-filters">
        <span>Show only:</span>
        <a href="{{ url_with_category('extractive') }}"
           class="cat-btn extractive {{ 'active' if 'extractive' in selected_categories else '' }}">
            <span class="dot extractive-dot"></span>
            Significant Effect
        </a>
        <a href="{{ url_with_category('mixed') }}"
           class="cat-btn mixed {{ 'active' if 'mixed' in selected_categories else '' }}">
            <span class="dot mixed-dot"></span>
            Mixed Impact
        </a>
        <a href="{{ url_with_category('restorative') }}"
           class="cat-btn restorative {{ 'active' if 'restorative' in selected_categories else '' }}">
            <span class="dot restorative-dot"></span>
            Restorative Impact
        </a>
        <a href="{{ url_with_category('unclassified') }}"
           class="cat-btn unclassified {{ 'active' if 'unclassified' in selected_categories else '' }}">
            <span class="dot unclassified-dot"></span>
            Uncategorized
        </a>
    </div>
    <div class="category-filters" style="margin-top:6px;">
        <span style="visibility:hidden;">Show only:</span>
        <a href="{{ url_with_category('newly_added') }}"
           class="cat-btn newly-added {{ 'active' if 'newly_added' in selected_categories else '' }}">
            <span class="dot newly-added-dot"></span>
            Newly Added
        </a>
        <a href="{{ url_with_category('taking_comments') }}"
           class="cat-btn taking-comments {{ 'active' if 'taking_comments' in selected_categories else '' }}">
            <span class="dot taking-comments-dot"></span>
            Taking Comments Now
        </a>
        <a href="{{ url_with_show_inactive }}"
           class="cat-btn active-filter {{ 'active' if show_inactive else '' }}">
            Show Inactive Projects
        </a>
    </div>
    <div class="category-disclaimer-row">
        <span class="category-disclaimer">*Impact level assigned automatically, based on keywords and is intended as a general guide only</span>
    </div>

    <div class="results-header">
        {% set cat_labels = {'extractive': 'Significant Effect', 'mixed': 'Mixed Impact', 'restorative': 'Restorative Impact', 'unclassified': 'Uncategorized', 'taking_comments': 'Taking Comments Now', 'active': 'Active Projects', 'newly_added': 'Newly Added'} %}
        {% if show_inactive and not (search or selected_forest or selected_status or selected_days or selected_category_str) %}
            <strong>{{ projects|length }}</strong> of <strong>{{ total }}</strong>
        {% elif selected_categories %}
            Showing: <strong>{% for cat in selected_categories %}{{ cat_labels.get(cat, cat) }}{% if not loop.last %} · {% endif %}{% endfor %}</strong>
            {% if selected_forest %} · <strong>{{ selected_forest_name }}</strong>{% endif %}
            {% if selected_days %} · added in the last <strong>{{ selected_days }} days</strong>{% endif %}
            {% if search %} · matching "<strong>{{ search }}</strong>"{% endif %}
            {% if selected_status %} · status: <strong>{{ selected_status }}</strong>{% endif %}
        {% elif search or selected_forest or selected_status or selected_days %}
            <strong>{{ projects|length }}</strong> of <strong>{{ active_total }}</strong>
            {% if selected_days %} added in the last <strong>{{ selected_days }} days</strong>{% endif %}
            {% if search %} matching "<strong>{{ search }}</strong>"{% endif %}
            {% if selected_status %} · status: <strong>{{ selected_status }}</strong>{% endif %}
            {% if selected_forest %} · <strong>{{ selected_forest_name }}</strong>{% endif %}
        {% else %}
            <strong>{{ projects|length }}</strong> of <strong>{{ active_total }}</strong> active projects
        {% endif %}
    </div>

    {% if projects %}
        {% for p in projects %}
        {% set has_milestones = p.get('milestones') and p['milestones']|length > 0 %}
        {% set status_color = status_colors.get(p.status, '#d0d0c8') %}
        {% set cat_bg = {'extractive': 'rgba(168,48,48,0.18)', 'restorative': 'rgba(45,122,31,0.15)', 'mixed': 'rgba(196,106,48,0.16)'}.get(p.category or '', 'white') %}
        {% set cat_border = {'extractive': '#a83030', 'restorative': '#2d7a1f', 'mixed': '#c46a30'}.get(p.category or '', '#d0d0c8') %}
        {% set cat_label = {'extractive': 'Significant Effect', 'restorative': 'Restorative Impact', 'mixed': 'Mixed Impact', '': 'Uncategorized', None: 'Uncategorized'}.get(p.category or '', 'Uncategorized') %}
        {% set is_tcn = p.get('accepting_comments') %}
        <div class="project-card {{ p.category or '' }}"
             style="background: {{ cat_bg }};
                    border: {{ '2px' if is_tcn else '1px' }} solid {{ cat_border }};">
            <div class="card-category-bar" style="background: {{ cat_border }};">
                {% if cat_label %}
                <span class="card-category-label">{{ cat_label }}</span>
                {% endif %}
            </div>

            <!-- Mobile: horizontal category top bar -->
            {% if cat_label %}
            <div class="card-category-top" style="background: {{ cat_border }};">
                {{ cat_label }}
            </div>
            {% endif %}

            <!-- 3-COLUMN CARD BODY -->
            <div class="card-body">

                <!-- CENTER: main content -->
                <div class="card-body-left">
                    {% set _fstate = forest_state_map.get(p.forest_code, '') %}
                    {% set _fcolor = state_colors.get(_fstate, {}).get('pill', '#2d7a1f') %}

                    <!-- Forest name + NEW badge + share button -->
                    <div style="display:flex; align-items:flex-start; justify-content:space-between; flex-wrap:wrap; margin-bottom:8px;">
                        <div style="display:flex; align-items:center; gap:8px; padding-top:25px;">
                            <div class="forest-tag" style="color: {{ _fcolor }}; margin:0;">{{ p.forest_name }}</div>
                            {% if new_badge_enabled and p.get('first_seen') and p['first_seen'][:10] >= recent_cutoff %}
                            <span class="new-badge">NEW</span>
                            {% endif %}
                        </div>
                        {% if p.get('accepting_comments') %}
                        <button onclick="
                            var url = 'https://web-production-295ec.up.railway.app/?sort=cara_newest&amp;category=taking_comments';
                            navigator.clipboard.writeText(url).then(function() {
                                var btn = document.activeElement;
                                btn.innerText = '✓ Link Copied!';
                                btn.style.background = '#2d7a1f';
                                setTimeout(function() { btn.innerText = 'Share'; btn.style.background = '#e05a2b'; }, 2500);
                            });
                        " style="margin-top:25px; margin-right:25px; padding:5px 14px; background:#e05a2b; border:none; color:white; font-family:'Poppins',sans-serif; font-size:0.78rem; font-weight:400; cursor:pointer; white-space:nowrap; flex-shrink:0; letter-spacing:0.5px;">Share</button>
                        {% endif %}
                    </div>

                    <!-- Project name -->
                    <div class="btn-title-wrap" style="margin-bottom:0; padding-top:0;">
                        <span class="project-title-text">{{ p.project_name }}</span>
                    </div>

                    <!-- Status badge -->

                    <!-- Description -->
                    {% if p.description %}
                    <div class="description">{{ p.description }}</div>
                    {% endif %}


                    <!-- Badges row: LFDC Commented · Learn About Wildfire · Learn About Thinning -->
                    {% if p.project_url in commented_urls or p.project_url in wildfire_urls or p.project_url in thinning_urls %}
                    <div style="display:flex; flex-direction:row; gap:12px; flex-wrap:wrap; padding-top:12px; padding-bottom:12px;">
                        {% if p.project_url in commented_urls %}
                        {% set comment_link = commented_urls_map.get(p.project_url, '') %}
                        {% if comment_link %}
                        <a href="{{ comment_link }}" target="_blank" rel="noopener" class="lfdc-commented-badge" style="text-decoration:none; width:auto;">
                            <img src="/static/LFDC_Logo.png" style="height:24px; width:24px; object-fit:contain; vertical-align:middle;"> LFDC Commented <svg style="width:12px;height:12px;flex-shrink:0;margin-left:4px;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
                        </a>
                        {% else %}
                        <div class="lfdc-commented-badge" style="width:auto;">
                            <img src="/static/LFDC_Logo.png" style="height:24px; width:24px; object-fit:contain; vertical-align:middle;"> LFDC Commented <svg style="width:12px;height:12px;flex-shrink:0;margin-left:4px;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
                        </div>
                        {% endif %}
                        {% endif %}
                        {% if p.project_url in wildfire_urls %}
                        <a href="{{ wildfire_url }}" target="_blank" rel="noopener" class="wildfire-badge" style="text-decoration:none;">
                            Learn About Wildfire <svg style="width:12px;height:12px;flex-shrink:0;margin-left:4px;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
                        </a>
                        {% endif %}
                        {% if p.project_url in thinning_urls %}
                        <a href="{{ thinning_url }}" target="_blank" rel="noopener" class="wildfire-badge" style="text-decoration:none;">
                            Learn About Thinning <svg style="width:12px;height:12px;flex-shrink:0;margin-left:4px;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
                        </a>
                        {% endif %}
                    </div>
                    {% endif %}
                    <div class="left-bottom">
                        <!-- Mobile milestone table -->
                        {% set resources = annotations.get(p.project_url, {}).get('resources') or p.get('_scraped_resources', []) %}
                        {% if resources %}
                        <div class="milestone-section mobile-only" style="width:100%; margin-bottom:6px;">
                            <table class="resource-table">
                                <tbody>
                                    {% for r in resources %}
                                    <tr>
                                        <td>{{ r.descriptor }}</td>
                                        <td class="amount-cell">{{ r.value }}</td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                        {% endif %}
                        {% if has_milestones %}
                        <div class="milestone-section mobile-only" style="width:100%; margin-bottom:12px;">
                            <table class="milestone-table">
                                <thead><tr><th>Milestone</th><th>Date</th></tr></thead>
                                <tbody>
                                    {% for m in p['milestones'] %}
                                    <tr>
                                        <td>{{ m.milestone }}</td>
                                        <td class="date-cell {{ 'estimated' if m.estimated else '' }}">{{ m.date if m.date else '—' }}</td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                        {% endif %}

                        <!-- Comment buttons -->
                        {% set project_id = p.project_url.rstrip('/').split('/')[-1] %}
                        <div class="comment-buttons">
                            <a class="btn-comment project-link"
                               href="{{ p.project_url }}"
                               target="_blank" rel="noopener">View Project Page</a>
                            {% if has_milestones %}
                            <a class="btn-comment {{ 'primary' if p.get('accepting_comments') else 'primary-inactive' }}"
                               href="https://cara.fs2c.usda.gov/Public/CommentInput?Project={{ project_id }}"
                               target="_blank" rel="noopener">Submit New Comments</a>
                            <a class="btn-comment secondary"
                               href="https://cara.fs2c.usda.gov/Public/ReadingRoom?Project={{ project_id }}"
                               target="_blank" rel="noopener">Read Prior Comments</a>
                            {% endif %}
                        </div>

                        {% set ann = annotations.get(p.project_url, {}) %}
                        {% if ann.get('annotation') or ann.get('intro') %}
                        <div class="annotation-box">
                            <button class="annotation-toggle" onclick="
                                var box = this.nextElementSibling;
                                var wrapper = this.closest('.annotation-box');
                                var isHidden = box.style.display === 'none' || box.style.display === '';
                                box.style.display = isHidden ? 'block' : 'none';
                                if (wrapper) wrapper.classList.toggle('expanded', isHidden);
                                var arrow = this.querySelector('.ann-arrow');
                                if (arrow) arrow.style.transform = isHidden ? 'rotate(90deg)' : 'rotate(0deg)';
                                var card = this.closest('.project-card');
                                var submitBtn = card ? card.querySelector('.btn-comment.primary') : null;
                                if (submitBtn) submitBtn.classList.toggle('pulsing', isHidden);
                            "><i class="ann-arrow">▶</i> Read and Copy Suggested Comment</button>
                            <div class="annotation-content" style="display:none;">
                                        {% if ann.get('intro') %}
                                <div class="annotation-intro">{{ ann.intro }}</div>
                                {% endif %}
                                <div class="annotation-text" id="ann-text-{{ loop.index }}">{{ ann.annotation }}</div>
                                <button class="annotation-copy" onclick="navigator.clipboard.writeText(document.getElementById('ann-text-{{ loop.index }}').innerText); this.innerText='Copied!'; setTimeout(()=>this.innerText='Copy to clipboard',2000)">Copy to clipboard</button>
                            </div>
                        </div>
                        {% endif %}

                        <!-- Meta -->
                        <div class="meta">
                            {% if p.unit %}<span>📍 {{ p.unit }}</span>{% endif %}
                            {% if p.purpose %}<span>🏷 {{ p.purpose.replace('|', ' · ') }}</span>{% endif %}
                            {% if p.first_seen %}<span>Added: {{ p.first_seen[:10] }}</span>{% endif %}
                        </div>
                    </div>
                </div><!-- card-body-left -->

                <!-- RIGHT COLUMN (desktop only) -->
                <div class="card-body-right desktop-only">
                    <div class="card-body-right-top">
                    {% if p.get('accepting_comments') %}
                    {% if p.get('comment_deadline') %}
                    {% set _days = days_left_to_comment(p.comment_deadline) %}
                    {% if _days is not none %}
                    <div style="font-size:0.7rem; font-weight:600; color:#a83030; text-align:center; width:255px; padding-bottom:4px; font-family:'Poppins',sans-serif; letter-spacing:0.5px;">
                        {% if _days == 0 %}Last Day to Comment{% elif _days == 1 %}1 Day Left to Comment{% elif _days > 0 %}{{ _days }} Days Left to Comment{% endif %}
                    </div>
                    {% endif %}
                    {% endif %}
                    <div class="comment-open-badge">
                        <span class="badge-title">Taking Comments Now!</span>
                        {% if p.get('comment_deadline') %}
                        <span class="badge-deadline">{{ format_deadline(p.comment_deadline) }}</span>
                        {% endif %}
                    </div>
                    {% endif %}
                    {% if p.status %}
                    <span class="status-badge" style="background: {{ status_colors.get(p.status, '#b4b2a9') }};">{{ p.status }}</span>
                    {% endif %}
                    {% if p.get('analysis_type') in ('Categorical Exclusion', 'Decision Memo') %}
                    <span class="ce-badge">Categorical Exclusion</span>
                    {% endif %}
                    </div><!-- card-body-right-top -->
                    {% set resources = annotations.get(p.project_url, {}).get('resources') or p.get('_scraped_resources', []) %}
                    {% if resources %}
                    <div class="milestone-section" style="margin-bottom:6px;">
                        <table class="resource-table">
                            <tbody>
                                {% for r in resources %}
                                <tr>
                                    <td>{{ r.descriptor }}</td>
                                    <td class="amount-cell">{{ r.value }}</td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                    {% endif %}
                    {% if has_milestones %}
                    <div class="milestone-section">
                        <table class="milestone-table">
                            <thead><tr><th>Milestone</th><th>Date</th></tr></thead>
                            <tbody>
                                {% for m in p['milestones'] %}
                                <tr>
                                    <td>{{ m.milestone }}</td>
                                    <td class="date-cell {{ 'estimated' if m.estimated else '' }}">{{ m.date if m.date else '—' }}</td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                    {% endif %}
                </div><!-- card-body-right -->
            </div><!-- card-body -->
        </div>
        {% endfor %}
    {% else %}
        <div class="no-results">No projects found matching your search.</div>
    {% endif %}

</div>

<footer>
    Data scraped from fs.usda.gov &nbsp;·&nbsp; Last updated: {{ last_scraped }}
</footer>

<script>
// Client-side instant search — filters cards without page reload
(function() {
    var input = document.querySelector('#searchform input[name="q"]');
    if (!input) return;

    // Give each card a searchable text attribute on load
    document.querySelectorAll('.project-card').forEach(function(card) {
        var text = card.innerText.toLowerCase();
        card.dataset.searchText = text;
    });

    function doFilter() {
        var term = input.value.toLowerCase().trim();
        var cards = document.querySelectorAll('.project-card');
        var visible = 0;
        cards.forEach(function(card) {
            var match = !term || card.dataset.searchText.indexOf(term) !== -1;
            card.style.display = match ? '' : 'none';
            if (match) visible++;
        });
        // Update results count if element exists
        var countEl = document.querySelector('.results-header strong');
        if (countEl && term) countEl.innerText = visible;
    }

    input.addEventListener('input', function() {
        var clearBtn = document.getElementById('search-clear');
        if (clearBtn) clearBtn.style.display = input.value ? 'flex' : 'none';
        doFilter();
    });

    // Still allow form submit (e.g. hitting Enter) for full server filter
    // but intercept if it's just a search with no other filters active
})();
</script>
</body>
<script>

</script>
</html>
"""


def toggle_forest_url_fn(code, current_str):
    """Return URL with the given forest code toggled in the forests param."""
    from flask import request as req
    from urllib.parse import urlencode
    current = [f.strip() for f in current_str.split(",") if f.strip()]
    if code in current:
        new = [c for c in current if c != code]
    else:
        new = current + [code]
    args = {}
    if req.args.get("q"):         args["q"]        = req.args.get("q")
    if req.args.get("status"):    args["status"]   = req.args.get("status")
    if req.args.get("days"):      args["days"]     = req.args.get("days")
    if req.args.get("sort"):      args["sort"]     = req.args.get("sort")
    if req.args.get("sort2"):     args["sort2"]    = req.args.get("sort2")
    # Don't carry category when toggling forest — avoids zero results
    if new:                       args["forests"]  = ",".join(new)
    return "/?" + urlencode(args) if args else "/"


@app.route("/")
def index():
    search            = request.args.get("q", "").strip()
    selected_forests_str = request.args.get("forests", "").strip()
    selected_forests     = [f.strip() for f in selected_forests_str.split(",") if f.strip()]
    selected_forest   = request.args.get("forest", "").strip()
    selected_status   = request.args.get("status", "").strip()
    selected_days     = request.args.get("days", "").strip()
    selected_category_str = request.args.get("category", "").strip()
    selected_categories = [c.strip() for c in selected_category_str.split(",") if c.strip()]
    show_inactive = request.args.get("show_inactive", "0") == "1"
    selected_sort     = request.args.get("sort", "cara_newest").strip()
    selected_sort2    = request.args.get("sort2", "").strip()

    all_projects, last_scraped = load_projects()
    annotations = load_annotations()
    commented_urls = set(annotations.get("_commented", []))
    wildfire_urls_manual = set(annotations.get("_wildfire", []))
    thinning_urls_manual = set(annotations.get("_thinning", []))
    wildfire_suppress = set(annotations.get("_wildfire_suppress", []))
    thinning_suppress = set(annotations.get("_thinning_suppress", []))
    wildfire_urls = (wildfire_urls_manual | {p["project_url"] for p in all_projects if has_wildfire_badge(p)}) - wildfire_suppress
    thinning_urls = (thinning_urls_manual | {p["project_url"] for p in all_projects if has_thinning_badge(p)}) - thinning_suppress

    recent_cutoff = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=72)
    ).strftime("%Y-%m-%d")

    # Per-forest project counts for the summary bar
    forest_counts = {}
    for f in FORESTS:
        forest_projects = [p for p in all_projects if p.get("forest_code") == f["code"]]
        active_forest_projects = [p for p in forest_projects if p.get("status") not in {"On Hold", "Completed"}]
        forest_counts[f["code"]] = {
            "total": len(forest_projects) if show_inactive else len(active_forest_projects),
        }
    multi_projects = [p for p in all_projects if p.get("forest_code") == "multi"]
    active_multi = [p for p in multi_projects if p.get("status") not in {"On Hold", "Completed"}]
    multi_count = len(multi_projects) if show_inactive else len(active_multi)

    # Active = In Progress + Developing Proposal
    active_count = sum(
        1 for p in all_projects
        if p.get("status") in ("In Progress", "Developing Proposal")
    )

    if selected_forests:
        def matches_forest_filter(p, selected):
            if p.get('forest_code') in selected:
                return True
            if p.get('is_multi_forest') or p.get('forest_code') == 'multi':
                fn = p.get('forest_name', '')
                return any(
                    FOREST_CODE_TO_ABBREV.get(code, '') in fn
                    for code in selected
                )
            return False
        forest_visible = [p for p in all_projects if matches_forest_filter(p, selected_forests)]
    else:
        forest_visible = all_projects

    # Filtered counts based on forest selection, before category filter
    # Capture totals before filtering
    grand_total = len(all_projects)
    INACTIVE_STATUSES = {"On Hold", "Completed"}
    active_total = sum(1 for p in all_projects if p.get("status") not in INACTIVE_STATUSES)

    # Filter out inactive unless show_inactive is set
    if not show_inactive:
        all_projects = [p for p in all_projects if p.get("status") not in INACTIVE_STATUSES]
        forest_visible = [p for p in forest_visible if p.get("status") not in INACTIVE_STATUSES]

    # Both counts use already-filtered lists
    filtered_counts = {
        "extractive":      sum(1 for p in forest_visible if p.get("category") == "extractive"),
        "restorative":     sum(1 for p in forest_visible if p.get("category") == "restorative"),
        "mixed":           sum(1 for p in forest_visible if p.get("category") == "mixed"),
        "unclassified":    sum(1 for p in forest_visible if not p.get("category")),
        "taking_comments": sum(1 for p in forest_visible if p.get("accepting_comments")),
        "active":          sum(1 for p in forest_visible if p.get("status") in ("In Progress", "Developing Proposal")),
        "newly_added":     sum(1 for p in forest_visible if p.get("first_seen", "")[:10] >= recent_cutoff),
    }
    selected_category = selected_categories[0] if len(selected_categories) == 1 else ""

    # Counts use the already-filtered all_projects as denominator
    counts = {
        "extractive":      sum(1 for p in all_projects if p.get("category") == "extractive"),
        "restorative":     sum(1 for p in all_projects if p.get("category") == "restorative"),
        "mixed":           sum(1 for p in all_projects if p.get("category") == "mixed"),
        "unclassified":    sum(1 for p in all_projects if not p.get("category")),
        "taking_comments": sum(1 for p in all_projects if p.get("accepting_comments")),
        "active":          sum(1 for p in all_projects if p.get("status") in ("In Progress", "Developing Proposal")),
        "newly_added":     sum(1 for p in all_projects if p.get("first_seen", "")[:10] >= recent_cutoff),
    }

    projects = filter_projects(
        forest_visible,
        search=search,
        forest_code=selected_forest,
        status=selected_status,
        days=selected_days,
        categories=selected_categories,
        recent_cutoff=recent_cutoff,
        sort=selected_sort,
        sort2=selected_sort2,
    )

    status_list = sorted(set(p["status"] for p in all_projects if p.get("status")))

    selected_forest_name = ""
    for f in FORESTS:
        if f["code"] == selected_forest:
            selected_forest_name = f["name"]
            break

    def url_with_show_inactive_fn():
        from urllib.parse import urlencode
        args = {}
        if search:                args["q"]       = search
        if selected_forest:       args["forest"]  = selected_forest
        if selected_status:       args["status"]  = selected_status
        if selected_days:         args["days"]    = selected_days
        if selected_sort:         args["sort"]    = selected_sort
        if selected_sort2:        args["sort2"]   = selected_sort2
        if selected_forests_str:  args["forests"] = selected_forests_str
        if selected_category_str: args["category"] = selected_category_str
        if not show_inactive:     args["show_inactive"] = "1"
        qs = urlencode(args)
        return f"/?{qs}" if qs else "/"

    def url_with_category(cat):
        from urllib.parse import urlencode
        cats = list(selected_categories)
        if cat in cats:
            cats.remove(cat)
        else:
            cats.append(cat)
        args = {}
        if search:                args["q"]       = search
        if selected_forest:       args["forest"]  = selected_forest
        if selected_status:       args["status"]  = selected_status
        if selected_days:         args["days"]    = selected_days
        if selected_sort:         args["sort"]    = selected_sort
        if selected_sort2:        args["sort2"]   = selected_sort2
        if selected_forests_str:  args["forests"] = selected_forests_str
        if cats:                  args["category"] = ",".join(cats)
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
        selected_categories=selected_categories,
        selected_category_str=selected_category_str,
        show_inactive=show_inactive,
        url_with_show_inactive=url_with_show_inactive_fn(),
        selected_sort=selected_sort,
        selected_sort2=selected_sort2,
        status_colors=STATUS_COLORS,
        format_deadline=format_deadline,
        days_left_to_comment=days_left_to_comment,
        analysis_colors=ANALYSIS_COLORS,
        analysis_tooltips={
            "Categorical Exclusion": "Lowest rigor of analysis",
            "Environmental Assessment": "Medium rigor of analysis",
            "Environmental Impact Statement": "Highest rigor of analysis",
        },
        total=grand_total,
        active_total=active_total,
        last_scraped=last_scraped,
        recent_cutoff=recent_cutoff,
        counts=counts,
        filtered_counts=filtered_counts,
        forest_counts=forest_counts,
        multi_count=multi_count,
        state_columns=STATE_COLUMNS,
        state_colors=STATE_COLORS,
        forest_state_map=FOREST_STATE_MAP,
        selected_forests=selected_forests,
        selected_forests_str=selected_forests_str,
        toggle_forest_url=toggle_forest_url_fn,
        active_count=active_count,
        url_with_category=url_with_category,
        annotations=annotations,
        new_badge_enabled=annotations.get("_new_badge_enabled", True),
        commented_urls=commented_urls,
        commented_urls_map=annotations.get("_commented_urls", {}),
        wildfire_urls=wildfire_urls,
        thinning_urls=thinning_urls,
        thinning_url="https://johnmuirproject.org/wp-content/uploads/2024/12/JMP-fact-sheet-thinning-and-fire-29Nov24.pdf",
        wildfire_url="https://www.forestclimatealliance.org/s/Final-Wildfire-in-the-Age-of-Climate-Change-compressed.pdf",
    )


# ── Annotations ──────────────────────────────────────────────

ANNOTATIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "annotations.json")


def load_annotations() -> dict:
    try:
        with open(ANNOTATIONS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_annotations_local(annotations: dict):
    with open(ANNOTATIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(annotations, f, indent=2, ensure_ascii=False)


def save_annotations_github(annotations: dict) -> bool:
    """Commit annotations.json to GitHub via the API. Returns True on success."""
    token  = os.environ.get("GITHUB_TOKEN")
    repo   = os.environ.get("GITHUB_REPO")   # e.g. "username/usfs-scraper"
    if not token or not repo:
        return False  # fall back to local only

    content = json.dumps(annotations, indent=2, ensure_ascii=False).encode("utf-8")
    encoded = base64.b64encode(content).decode("utf-8")

    api_url = f"https://api.github.com/repos/{repo}/contents/annotations.json"

    # Get current SHA (needed for update)
    sha = None
    try:
        req = urllib.request.Request(api_url, headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
        })
        with urllib.request.urlopen(req) as resp:
            sha = json.loads(resp.read())["sha"]
    except Exception:
        pass  # file doesn't exist yet — create it

    payload = {
        "message": f"Update annotations {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M')}",
        "content": encoded,
    }
    if sha:
        payload["sha"] = sha

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(api_url, data=data, method="PUT", headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(req):
            pass
        return True
    except Exception as e:
        print(f"GitHub commit failed: {e}")
        return False


ADMIN_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>LFDC Tracker Admin</title>
<style>
  body { font-family: 'Segoe UI', sans-serif; background: #f0f0ea; margin: 0; padding: 20px; color: #1a1a1a; }
  h1 { font-size: 1.3rem; font-weight: 600; margin-bottom: 6px; }
  h2 { font-size: 1.05rem; font-weight: 600; margin: 28px 0 10px 0; border-bottom: 2px solid #ccc; padding-bottom: 6px; max-width: 900px; }
  .subtitle { font-size: 0.8rem; color: #666; margin-bottom: 24px; }
  .project-list { display: flex; flex-direction: column; gap: 16px; max-width: 1400px; }
  .project-card { background: white; border: 2px solid #e0c040; border-radius: 0; padding: 16px; }
  .project-name { font-weight: 600; font-size: 1rem; margin-bottom: 4px; }
  .forest-name { font-size: 0.78rem; color: #666; margin-bottom: 12px; }
  .deadline { font-size: 0.78rem; color: #a83030; font-weight: 600; margin-bottom: 12px; }
  label { font-size: 0.72rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; color: #555; display: block; margin-bottom: 4px; }
  textarea { width: 100%; box-sizing: border-box; padding: 8px; font-family: inherit; font-size: 0.85rem; border: 1px solid #ccc; resize: vertical; min-height: 80px; }
  .save-btn { margin-top: 8px; padding: 6px 18px; background: #2d7a1f; color: white; border: none; font-size: 0.82rem; cursor: pointer; }
  .save-btn:hover { background: #1e5a12; }
  .no-tcn { color: #888; font-size: 0.9rem; margin-top: 20px; }
  .logout { float: right; font-size: 0.75rem; color: #888; text-decoration: none; }
  .logout:hover { color: #333; }
  .flash { background: #d4edda; border: 1px solid #2d7a1f; padding: 8px 14px; margin-bottom: 16px; font-size: 0.85rem; color: #1a4f0f; max-width: 900px; }
  .flash.error { background: #fde8e8; border-color: #a83030; color: #7c0000; }

  /* LFDC Commented section */
  .commented-section { max-width: 1400px; }
  .forest-accordion { margin-bottom: 6px; border: 1px solid #ddd; }
  .forest-accordion-header { width: 100%; text-align: left; background: #f0ede4; border: none; padding: 10px 14px; font-size: 0.88rem; font-weight: 600; cursor: pointer; display: flex; align-items: center; gap: 10px; font-family: inherit; color: #1a1a1a; }
  .forest-accordion-header:hover { background: #e8e4d8; }
  .acc-arrow { font-size: 0.7rem; color: #888; }
  .acc-count { margin-left: auto; font-size: 0.72rem; color: #888; font-weight: 400; }
  .forest-accordion-body { padding: 0; overflow-x: auto; }
  .project-table { width: 100%; border-collapse: collapse; font-size: 0.78rem; min-width: 1600px; }
  .project-table th { background: #f7f7f0; padding: 7px 10px; text-align: left; border-bottom: 2px solid #ddd; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.5px; color: #555; white-space: nowrap; resize: horizontal; overflow: hidden; }
  .project-table th.sortable { cursor: pointer; user-select: none; }
  .project-table th.sortable:hover { background: #ededde; }
  .sort-icon { font-size: 0.65rem; margin-left: 3px; }
  .project-table td { padding: 6px 10px; border-bottom: 1px solid #eee; vertical-align: middle; }
  .project-table tr:hover td { background: #faf9f4; }
  .project-table tr.new-project td { background: #fff8e6; }
  .project-table tr.new-project:hover td { background: #fff0cc; }
  .proj-name-cell { color: #1a1a1a; width: 200px; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .proj-date-cell { color: #666; white-space: nowrap; }
  .proj-check-cell { text-align: center; width: 60px; }
  .proj-check-cell input[type=checkbox] { width: 16px; height: 16px; cursor: pointer; accent-color: #c94f1a; }
  .proj-url-cell { min-width: 200px; }
  .comment-url-input { width: 100%; padding: 4px 6px; font-size: 0.72rem; border: 1px solid #ccc; box-sizing: border-box; font-family: inherit; }
  .auto-check { color: #2d7a1f; font-weight: 700; font-size: 1rem; cursor: default; }
  .save-commented-btn { margin-top: 16px; padding: 8px 24px; background: #c94f1a; color: white; border: none; font-size: 0.88rem; cursor: pointer; font-family: inherit; font-weight: 600; }
  .save-commented-btn:hover { background: #a33d12; }
</style>
</head>
<body>
<a href="/admin/logout" class="logout">Log out</a>
<h1>LFDC Tracker — Admin</h1>

<div style="background:#f7f7f0; border:1px solid #ddd; padding:12px 18px; margin-bottom:24px; display:flex; align-items:center; gap:20px; flex-wrap:wrap;">
  <strong style="font-size:0.82rem; color:#555; letter-spacing:0.3px;">TOOLS</strong>
  <div style="display:flex; align-items:center; gap:8px;">
    <span style="font-size:0.78rem; color:#444; font-weight:600;">NEW Badge</span>
    <form method="POST" action="/admin/save-new-badge" style="margin:0;">
      <input type="hidden" name="new_badge_enabled" value="off">
      <label style="display:flex; align-items:center; gap:6px; cursor:pointer; font-size:0.75rem;">
        <input type="checkbox" name="new_badge_enabled" value="on" {{ 'checked' if new_badge_enabled else '' }}
               onchange="this.form.submit()">
        {{ 'On' if new_badge_enabled else 'Off' }}
      </label>
    </form>
  </div>
  <div style="width:1px; height:20px; background:#ddd;"></div>
  <a href="/admin/ledger" style="font-size:0.78rem; font-weight:600; color:#3a7aad; text-decoration:none; padding:4px 12px; border:1px solid #3a7aad; background:white;">📋 Ledger Audit</a>
</div>

{% if flash %}
<div class="flash {{ 'error' if flash_type == 'error' else '' }}">{{ flash }}</div>
{% endif %}

<!-- ── Section 1: Suggested Comments ── -->
<h2>💬 Suggested Comments (Projects Taking Comments Now)</h2>
<p class="subtitle">Add suggested comment text to projects currently accepting comments.</p>

{% if tcn_projects %}
<div class="project-list">
{% for p in tcn_projects %}
{% set has_annotation = annotations.get(p.project_url, {}).get('annotation', '') %}
<div class="project-card" style="border: 2px solid {{ '#2d7a1f' if has_annotation else '#a83030' }}; margin-bottom:6px;">
  <button type="button" class="project-card-header" onclick="
    var body = this.nextElementSibling;
    var isOpen = body.style.display !== 'none';
    body.style.display = isOpen ? 'none' : 'block';
    this.querySelector('.acc-arrow').innerText = isOpen ? '▶' : '▼';
  " style="width:100%; text-align:left; background:none; border:none; padding:10px 14px; cursor:pointer; display:flex; align-items:center; gap:10px; font-family:inherit;">
    <span class="acc-arrow">▶</span>
    <span style="font-weight:600; font-size:0.88rem;">{{ p.project_name }}</span>
    <span style="font-size:0.75rem; color:#888; margin-left:6px;">{{ p.forest_name }}</span>
    <span style="margin-left:auto; font-size:0.7rem; color:{{ '#2d7a1f' if has_annotation else '#a83030' }}; font-weight:600;">{{ '✓ Has comment' if has_annotation else '✗ No comment' }}</span>
  </button>
  <div class="project-card-body" style="display:none; padding:0 14px 14px 14px;">
    {% if p.comment_deadline %}<div class="deadline" style="margin-bottom:8px;">Comments due: {{ p.comment_deadline }}</div>{% endif %}
    <form method="POST" action="/admin/save">
      <input type="hidden" name="project_url" value="{{ p.project_url }}">
      <label>Intro Paragraph (bold, shown above comment, not copyable)</label>
      <textarea name="intro" placeholder="Enter bold intro text shown above the suggested comment...">{{ annotations.get(p.project_url, {}).get('intro', '') }}</textarea>
      <br>
      <label style="margin-top:10px;">Suggested Comment Text (copyable)</label>
      <textarea name="annotation" placeholder="Enter suggested comment text for users to copy...">{{ annotations.get(p.project_url, {}).get('annotation', '') }}</textarea>
      <br>
      <label style="margin-top:10px;">Internal Notes (not shown to public)</label>
      <textarea name="notes" placeholder="Internal notes for LFDC staff only...">{{ annotations.get(p.project_url, {}).get('notes', '') }}</textarea>
      <br>
      <button type="submit" class="save-btn">Save</button>
    </form>
  </div>
</div>
{% endfor %}
</div>
{% else %}
<p class="no-tcn">No projects are currently accepting comments.</p>
{% endif %}

<!-- ── Section 2: LFDC Commented ── -->
<h2>🟠 LFDC Commented</h2>
<p class="subtitle">Check projects where LFDC has submitted formal comments. Projects highlighted in amber were added in the last 72 hours.</p>

<form method="POST" action="/admin/save-commented">
<div style="margin-bottom:10px;">
  <button type="button" onclick="
    var bodies = document.querySelectorAll('.forest-accordion-body');
    var arrows = document.querySelectorAll('.acc-arrow');
    var allOpen = Array.from(bodies).every(b => b.style.display !== 'none');
    bodies.forEach(function(b) { b.style.display = allOpen ? 'none' : 'block'; });
    arrows.forEach(function(a) { a.innerText = allOpen ? '▶' : '▼'; });
    this.innerText = allOpen ? 'Expand All' : 'Collapse All';
  " style="padding:6px 16px; background:#f0ede4; border:1px solid #ccc; font-family:inherit; font-size:0.82rem; cursor:pointer;">Collapse All</button>
</div>
<div class="commented-section">
{% for forest_name, forest_projects in all_projects_by_forest %}
<div class="forest-accordion">
  <button type="button" class="forest-accordion-header" onclick="
    var body = this.nextElementSibling;
    var isOpen = body.style.display !== 'none';
    body.style.display = isOpen ? 'none' : 'block';
    this.querySelector('.acc-arrow').innerText = isOpen ? '▶' : '▼';
  ">
    <span class="acc-arrow">▼</span>
    {{ forest_name }}
    <span class="acc-count">{{ forest_projects|length }} projects</span>
  </button>
  <div class="forest-accordion-body admin-table-wrap" style="display:block;">
    <table class="project-table" data-sort-col="1" data-sort-dir="desc">
      <thead>
        <tr>
          <th class="sortable" onclick="sortTable(this, 0)">Project <span class="sort-icon">↕</span></th>
          <th class="sortable" onclick="sortTable(this, 1)">Date Added <span class="sort-icon">↓</span></th>
          <th>Thinning Factsheet</th>
          <th>Wildfire Factsheet</th>
          <th>LFDC Commented</th>
          <th>Comment URL</th>
          <th>Resource Descriptor</th>
          <th>Amount</th>
        </tr>
      </thead>
      <tbody>
        {% for p in forest_projects %}
        <tr class="{{ 'new-project' if p.get('first_seen','')[:10] >= recent_cutoff else '' }}">
          <td class="proj-name-cell">{{ p.project_name }}</td>
          <td class="proj-date-cell" data-date="{{ p.get('first_seen','')[:10] }}">{{ p.get('first_seen','')[:10] }}</td>
          <td class="proj-check-cell">
            {% set thinning_auto = has_thinning_badge(p) %}
            {% set thinning_checked = p.project_url in thinning_urls %}
            {% if thinning_auto %}
            <input type="checkbox" name="thinning" value="{{ p.project_url }}"
                   {{ 'checked' if thinning_checked else '' }}
                   title="Auto-assigned (uncheck to suppress)"
                   style="accent-color: #2d7a1f;"
                   onchange="var h=this.nextElementSibling; h.disabled=this.checked;">
            <input type="hidden" name="thinning_suppress" value="{{ p.project_url }}" {{ '' if not thinning_checked else 'disabled' }}>
            {% else %}
            <input type="checkbox" name="thinning" value="{{ p.project_url }}"
                   {{ 'checked' if thinning_checked else '' }}
                   title="Manual override"
                   style="accent-color: #c94f1a;">
            {% endif %}
          </td>
          <td class="proj-check-cell">
            {% set wildfire_auto = has_wildfire_badge(p) %}
            {% set wildfire_checked = p.project_url in wildfire_urls %}
            {% if wildfire_auto %}
            <input type="checkbox" name="wildfire" value="{{ p.project_url }}"
                   {{ 'checked' if wildfire_checked else '' }}
                   title="Auto-assigned (uncheck to suppress)"
                   style="accent-color: #2d7a1f;"
                   onchange="var h=this.nextElementSibling; h.disabled=this.checked;">
            <input type="hidden" name="wildfire_suppress" value="{{ p.project_url }}" {{ '' if not wildfire_checked else 'disabled' }}>
            {% else %}
            <input type="checkbox" name="wildfire" value="{{ p.project_url }}"
                   {{ 'checked' if wildfire_checked else '' }}
                   title="Manual override"
                   style="accent-color: #c94f1a;">
            {% endif %}
          </td>
          <td class="proj-check-cell">
            <input type="checkbox" name="commented" value="{{ p.project_url }}"
                   {{ 'checked' if p.project_url in commented_urls else '' }}>
          </td>
          <td class="proj-url-cell">
            <form method="POST" action="/admin/save-url" style="display:flex; gap:4px; align-items:center;">
              <input type="hidden" name="project_url" value="{{ p.project_url }}">
              <input type="text" name="comment_url"
                     class="comment-url-input"
                     placeholder="https://..."
                     value="{{ commented_urls_map.get(p.project_url, '') }}">
              <button type="submit" style="padding:3px 8px; background:#2d7a1f; color:white; border:none; font-size:0.7rem; cursor:pointer; white-space:nowrap;">Save</button>
            </form>
          </td>
          <td class="proj-url-cell" style="min-width:160px;">
            {% set ann_resources = annotations.get(p.project_url, {}).get('resources', []) %}
            {% set scraped_resources = p.get('_scraped_resources', []) %}
            {% set display_resources = ann_resources if ann_resources else scraped_resources %}
            <form method="POST" action="/admin/save-resources" style="margin:0;">
              <input type="hidden" name="project_url" value="{{ p.project_url }}">
              {% for r in display_resources %}
              <div style="display:flex; gap:4px; margin-bottom:3px;">
                <input type="text" name="res_descriptor" value="{{ r.descriptor }}" placeholder="e.g. Acres of Old Growth" style="width:130px; padding:2px 4px; font-size:0.65rem; border:1px solid #ccc; font-family:inherit;">
                <input type="text" name="res_value" value="{{ r.value }}" placeholder="e.g. 1,655" style="width:60px; padding:2px 4px; font-size:0.65rem; border:1px solid #ccc; font-family:inherit;">
              </div>
              {% else %}
              <div style="display:flex; gap:4px; margin-bottom:3px;">
                <input type="text" name="res_descriptor" value="" placeholder="e.g. Acres of Old Growth" style="width:130px; padding:2px 4px; font-size:0.65rem; border:1px solid #ccc; font-family:inherit;">
                <input type="text" name="res_value" value="" placeholder="e.g. 1,655" style="width:60px; padding:2px 4px; font-size:0.65rem; border:1px solid #ccc; font-family:inherit;">
              </div>
              {% endfor %}
              <div style="display:flex; gap:4px; margin-bottom:3px;" id="extra-row-{{ loop.index }}">
                <input type="text" name="res_descriptor" value="" placeholder="+ add row" style="width:130px; padding:2px 4px; font-size:0.65rem; border:1px solid #ccc; font-family:inherit; color:#aaa;">
                <input type="text" name="res_value" value="" placeholder="" style="width:60px; padding:2px 4px; font-size:0.65rem; border:1px solid #ccc; font-family:inherit;">
              </div>
              <button type="submit" style="padding:2px 8px; background:#3a7aad; color:white; border:none; font-size:0.65rem; cursor:pointer; margin-top:2px;">Save</button>
            </form>
          </td>
          <td></td>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
{% endfor %}
</div>
<button type="submit" class="save-commented-btn">Save LFDC Commented List</button>
</form>

<script>
function sortTable(th, colIndex) {
  var table = th.closest('table');
  var tbody = table.querySelector('tbody');
  var rows = Array.from(tbody.querySelectorAll('tr'));
  var currentDir = table.dataset.sortDir === 'asc' && table.dataset.sortCol == colIndex ? 'desc' : 'asc';
  table.dataset.sortDir = currentDir;
  table.dataset.sortCol = colIndex;
  rows.sort(function(a, b) {
    var aVal = a.querySelectorAll('td')[colIndex].dataset.date || a.querySelectorAll('td')[colIndex].innerText.trim().toLowerCase();
    var bVal = b.querySelectorAll('td')[colIndex].dataset.date || b.querySelectorAll('td')[colIndex].innerText.trim().toLowerCase();
    return currentDir === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
  });
  rows.forEach(function(r) { tbody.appendChild(r); });
  table.querySelectorAll('th .sort-icon').forEach(function(icon) { icon.innerText = '↕'; });
  th.querySelector('.sort-icon').innerText = currentDir === 'asc' ? '↑' : '↓';
}

// Column resize
(function() {
  function makeResizable(table) {
    var ths = table.querySelectorAll('th');
    ths.forEach(function(th) {
      var handle = document.createElement('div');
      handle.style.cssText = 'position:absolute;right:0;top:0;width:6px;height:100%;cursor:col-resize;user-select:none;z-index:10;';
      th.style.position = 'relative';
      th.appendChild(handle);
      var startX, startW;
      handle.addEventListener('mousedown', function(e) {
        startX = e.pageX;
        startW = th.offsetWidth;
        e.preventDefault();
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
      });
      function onMove(e) { th.style.width = Math.max(40, startW + e.pageX - startX) + 'px'; }
      function onUp() {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
      }
    });
  }
  document.querySelectorAll('.project-table').forEach(makeResizable);
})();
</script>

</body>
</html>
"""

ADMIN_LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>LFDC Admin Login</title>
<style>
  body { font-family: 'Segoe UI', sans-serif; background: #f0f0ea; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
  .box { background: white; padding: 32px; border: 1px solid #ccc; max-width: 320px; width: 100%; }
  h1 { font-size: 1.1rem; margin-bottom: 20px; }
  input[type=password] { width: 100%; box-sizing: border-box; padding: 8px; font-size: 0.9rem; border: 1px solid #ccc; margin-bottom: 12px; }
  button { padding: 8px 20px; background: #2d7a1f; color: white; border: none; font-size: 0.9rem; cursor: pointer; }
  .error { color: #a83030; font-size: 0.82rem; margin-bottom: 10px; }
</style>
</head>
<body>
<div class="box">
  <h1>LFDC Tracker Admin</h1>
  {% if error %}<div class="error">Incorrect password.</div>{% endif %}
  <form method="POST">
    <input type="password" name="password" placeholder="Password" autofocus>
    <button type="submit">Log in</button>
  </form>
</div>
</body>
</html>
"""


@app.route("/admin", methods=["GET"])
def admin():
    if not session.get("admin_authed"):
        return redirect(url_for("admin_login"))
    projects, _ = load_projects()
    tcn_projects = [p for p in projects if p.get("accepting_comments")]
    annotations  = load_annotations()
    commented_urls = set(annotations.get("_commented", []))
    wildfire_urls_manual = set(annotations.get("_wildfire", []))
    thinning_urls_manual = set(annotations.get("_thinning", []))
    # Combine auto + manual
    wildfire_urls = wildfire_urls_manual | {p["project_url"] for p in projects if has_wildfire_badge(p)}
    thinning_urls = thinning_urls_manual | {p["project_url"] for p in projects if has_thinning_badge(p)}

    # Organize all projects by forest (in state order), then alphabetically by project name
    STATE_ORDER = ["WA", "OR", "CA+OR", "CA", "AK"]
    forests_in_order = []
    seen_forests = set()
    for state in STATE_ORDER:
        for p in projects:
            fn = p.get("forest_name", "")
            fs = FOREST_STATE_MAP.get(p.get("forest_code", ""), "")
            if fs == state and fn not in seen_forests:
                seen_forests.add(fn)
                forests_in_order.append(fn)

    by_forest = {}
    for p in projects:
        fn = p.get("forest_name", "")
        if fn not in by_forest:
            by_forest[fn] = []
        by_forest[fn].append(p)
    for fn in by_forest:
        by_forest[fn].sort(key=lambda p: p.get("project_name", "").lower())

    all_projects_by_forest = [(fn, by_forest[fn]) for fn in forests_in_order if fn in by_forest]

    # Add multi-forest projects as a separate group
    multi_projects = [p for p in projects if p.get("forest_code") == "multi"]
    if multi_projects:
        multi_projects.sort(key=lambda p: p.get("project_name", "").lower())
        all_projects_by_forest.append(("Multi-Forest Projects", multi_projects))

    admin_cutoff = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=72)
    ).strftime("%Y-%m-%d")

    # Also keep by_state for state labels
    STATE_ORDER_FULL = ["WA", "OR", "CA+OR", "CA", "AK"]
    by_state = {s: [] for s in STATE_ORDER_FULL}
    for p in projects:
        state = FOREST_STATE_MAP.get(p.get("forest_code", ""), "")
        if state in by_state:
            by_state[state].append(p)
    for state in by_state:
        by_state[state].sort(key=lambda p: (p.get("forest_name",""), p.get("project_name","").lower()))

    flash = request.args.get("flash", "")
    flash_type = request.args.get("flash_type", "")
    new_badge_enabled = annotations.get("_new_badge_enabled", True)
    commented_urls_map = annotations.get("_commented_urls", {})
    wildfire_urls_manual = set(annotations.get("_wildfire", []))
    wildfire_urls_manual = set(annotations.get("_wildfire", []))
    thinning_urls_manual = set(annotations.get("_thinning", []))
    wildfire_suppress = set(annotations.get("_wildfire_suppress", []))
    thinning_suppress = set(annotations.get("_thinning_suppress", []))
    wildfire_urls = (wildfire_urls_manual | {p["project_url"] for p in projects if has_wildfire_badge(p)}) - wildfire_suppress
    thinning_urls = (thinning_urls_manual | {p["project_url"] for p in projects if has_thinning_badge(p)}) - thinning_suppress
    return render_template_string(ADMIN_TEMPLATE,
        tcn_projects=tcn_projects,
        annotations=annotations,
        flash=flash,
        flash_type=flash_type,
        all_projects_by_state=by_state,
        all_projects_by_forest=all_projects_by_forest,
        commented_urls=commented_urls,
        new_badge_enabled=new_badge_enabled,
        commented_urls_map=commented_urls_map,
        wildfire_urls=wildfire_urls,
        thinning_urls=thinning_urls,
        wildfire_urls_manual=wildfire_urls_manual,
        thinning_urls_manual=thinning_urls_manual,
        wildfire_suppress=wildfire_suppress,
        thinning_suppress=thinning_suppress,
        has_thinning_badge=has_thinning_badge,
        has_wildfire_badge=has_wildfire_badge,
        thinning_url="https://johnmuirproject.org/wp-content/uploads/2024/12/JMP-fact-sheet-thinning-and-fire-29Nov24.pdf",
        wildfire_url="https://www.forestclimatealliance.org/s/Final-Wildfire-in-the-Age-of-Climate-Change-compressed.pdf",
        recent_cutoff=admin_cutoff,
    )


@app.route("/admin/save-resources", methods=["POST"])
def admin_save_resources():
    if not session.get("admin_authed"):
        return redirect(url_for("admin_login"))
    project_url  = request.form.get("project_url", "").strip()
    descriptors  = request.form.getlist("res_descriptor")
    values       = request.form.getlist("res_value")
    if project_url:
        resources = [
            {"descriptor": d.strip(), "value": v.strip()}
            for d, v in zip(descriptors, values)
            if d.strip() and v.strip()
        ]
        annotations = load_annotations()
        if project_url not in annotations:
            annotations[project_url] = {}
        if resources:
            annotations[project_url]["resources"] = resources
        else:
            annotations[project_url].pop("resources", None)
        save_annotations_local(annotations)
        save_annotations_github(annotations)
    return redirect(url_for("admin") + "?flash=Resources+saved+✓")


@app.route("/admin/save-new-badge", methods=["POST"])
def admin_save_new_badge():
    if not session.get("admin_authed"):
        return redirect(url_for("admin_login"))
    new_badge_enabled = request.form.get("new_badge_enabled", "off") == "on"
    annotations = load_annotations()
    annotations["_new_badge_enabled"] = new_badge_enabled
    save_annotations_local(annotations)
    save_annotations_github(annotations)
    return redirect(url_for("admin"))


@app.route("/admin/save-url", methods=["POST"])
def admin_save_url():
    if not session.get("admin_authed"):
        return redirect(url_for("admin_login"))
    project_url = request.form.get("project_url", "").strip()
    comment_url = request.form.get("comment_url", "").strip()
    if project_url:
        annotations = load_annotations()
        urls_map = annotations.get("_commented_urls", {})
        if comment_url:
            urls_map[project_url] = comment_url
        else:
            urls_map.pop(project_url, None)
        annotations["_commented_urls"] = urls_map
        save_annotations_local(annotations)
        save_annotations_github(annotations)
    return redirect(url_for("admin") + "?flash=URL+saved+%E2%9C%93")


@app.route("/admin/save-commented", methods=["POST"])
def admin_save_commented():
    if not session.get("admin_authed"):
        return redirect(url_for("admin_login"))
    new_badge_enabled = request.form.get("new_badge_enabled", "off") == "on"
    commented = request.form.getlist("commented")
    wildfire = request.form.getlist("wildfire")
    thinning = request.form.getlist("thinning")
    # Projects that were auto-qualified but explicitly unchecked = suppressed
    wildfire_suppress = request.form.getlist("wildfire_suppress")
    thinning_suppress = request.form.getlist("thinning_suppress")
    annotations = load_annotations()
    annotations["_new_badge_enabled"] = new_badge_enabled
    annotations["_commented"] = commented
    annotations["_wildfire"] = wildfire
    annotations["_thinning"] = thinning
    annotations["_wildfire_suppress"] = wildfire_suppress
    annotations["_thinning_suppress"] = thinning_suppress

    # Build URL map: purl_N -> project URL, commented_url_N -> the URL to link to
    # Start with existing map so URLs for projects not in the form are preserved
    existing_urls_map = annotations.get("_commented_urls", {})
    commented_urls_map = dict(existing_urls_map)

    # Track which project URLs were actually submitted in this form
    submitted_purls = set()
    for key, project_url in request.form.items():
        if key.startswith("purl_") and project_url.strip():
            submitted_purls.add(project_url)
            idx = key[5:]
            link_url = request.form.get(f"commented_url_{idx}", "").strip()
            if link_url:
                commented_urls_map[project_url] = link_url
            else:
                # URL was cleared for this project
                commented_urls_map.pop(project_url, None)

    annotations["_commented_urls"] = commented_urls_map
    save_annotations_local(annotations)
    github_ok = save_annotations_github(annotations)
    flash = "LFDC Commented list saved and committed to GitHub ✓" if github_ok else "Saved locally (GitHub token not configured)"
    return redirect(url_for("admin") + f"?flash={urllib.parse.quote(flash)}")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        password = request.form.get("password", "")
        admin_pw = os.environ.get("ADMIN_PASSWORD", "lfdc-admin")
        if password == admin_pw:
            session["admin_authed"] = True
            return redirect(url_for("admin"))
        return render_template_string(ADMIN_LOGIN_TEMPLATE, error=True)
    return render_template_string(ADMIN_LOGIN_TEMPLATE, error=False)


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_authed", None)
    return redirect(url_for("admin_login"))


@app.route("/admin/save", methods=["POST"])
def admin_save():
    if not session.get("admin_authed"):
        return redirect(url_for("admin_login"))

    project_url = request.form.get("project_url", "").strip()
    annotation  = request.form.get("annotation", "").strip()
    intro       = request.form.get("intro", "").strip()
    notes       = request.form.get("notes", "").strip()

    if not project_url:
        return redirect(url_for("admin"))

    annotations = load_annotations()
    if annotation or intro or notes:
        annotations[project_url] = {
            "intro":      intro,
            "annotation": annotation,
            "notes":      notes,
            "updated":    datetime.datetime.utcnow().isoformat(),
        }
    elif project_url in annotations:
        del annotations[project_url]

    save_annotations_local(annotations)
    github_ok = save_annotations_github(annotations)

    flash = "Saved and committed to GitHub ✓" if github_ok else "Saved locally (GitHub token not configured)"
    return redirect(url_for("admin") + f"?flash={urllib.parse.quote(flash)}")


    save_annotations_local(annotations)
    github_ok = save_annotations_github(annotations)

    flash = "Saved and committed to GitHub ✓" if github_ok else "Saved locally (GitHub token not configured)"
    return redirect(url_for("admin") + f"?flash={urllib.parse.quote(flash)}")


@app.route("/admin/ledger")
def admin_ledger():
    if not session.get("admin_authed"):
        return redirect(url_for("admin_login"))

    ledger = load_ledger()
    projects, _ = load_projects()
    current_urls = {p["project_url"] for p in projects}
    project_map  = {p["project_url"]: p for p in projects}

    # Multi-forest project URLs
    multi_urls = {p["project_url"] for p in projects if p.get("forest_code") == "multi"}

    # Detect duplicates by project ID
    id_map = {}
    for url in ledger:
        pid = url.rstrip("/").split("/")[-1]
        id_map.setdefault(pid, []).append(url)
    dupe_ids = {pid for pid, urls in id_map.items() if len(urls) > 1}
    dupe_urls = {url for url in ledger for pid in [url.rstrip("/").split("/")[-1]] if pid in dupe_ids}

    # 1. All ledger entries
    all_entries = sorted(ledger.items(), key=lambda x: x[1].get("first_seen", ""), reverse=True)

    # 2. In ledger but not in current projects.json
    missing_from_projects = [(url, data) for url, data in all_entries if url not in current_urls]

    # 3. In projects.json but not in ledger
    missing_from_ledger = [p for p in projects if p["project_url"] not in ledger]

    # 4. Suspected duplicates
    suspected_dupes = [(pid, urls) for pid, urls in id_map.items() if len(urls) > 1]

    flash = request.args.get("flash", "")

    AUDIT_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<title>Ledger Audit — LFDC Admin</title>
<style>
  body { font-family: 'Poppins', sans-serif; background: #e8ede3; padding: 24px; font-size: 0.82rem; }
  h1 { font-size: 1.2rem; font-weight: 600; margin-bottom: 4px; }
  h2 { font-size: 0.95rem; font-weight: 600; margin: 24px 0 8px; border-bottom: 2px solid #ccc; padding-bottom: 4px; }
  .back { display: inline-block; margin-bottom: 16px; color: #c94f1a; font-size: 0.78rem; }
  .flash { background: #d4edda; border: 1px solid #2d7a1f; color: #2d7a1f; padding: 8px 14px; margin-bottom: 16px; font-size: 0.78rem; }
  table { width: 100%; border-collapse: collapse; background: white; margin-bottom: 16px; }
  th { background: #d8d8d4; padding: 6px 10px; text-align: left; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.5px; }
  td { padding: 5px 10px; border-bottom: 1px solid #eee; vertical-align: middle; }
  tr:hover td { background: #f9f9f6; }
  .tag { display: inline-block; padding: 1px 6px; font-size: 0.65rem; border-radius: 2px; margin-right: 3px; }
  .tag.active { background: #d4edda; color: #2d7a1f; }
  .tag.inactive { background: #f8d7da; color: #a83030; }
  .tag.multi { background: #cce5ff; color: #3a7aad; }
  .tag.dupe { background: #fff3cd; color: #856404; }
  .count { font-size: 0.72rem; color: #888; margin-left: 6px; }
  form.delete-form { display: inline; }
  form.edit-form { display: inline-flex; gap: 4px; align-items: center; }
  input.date-input { padding: 2px 6px; font-size: 0.72rem; border: 1px solid #ccc; font-family: inherit; width: 110px; }
  button.del { background: #a83030; color: white; border: none; padding: 2px 8px; font-size: 0.65rem; cursor: pointer; }
  button.save { background: #2d7a1f; color: white; border: none; padding: 2px 8px; font-size: 0.65rem; cursor: pointer; }
  a { color: #3a7aad; }
  .none { color: #999; font-style: italic; padding: 10px; }
</style>
</head>
<body>
<a href="/admin" class="back">← Back to Admin</a>
<h1>Ledger Audit <span class="count">({{ all_entries|length }} total entries)</span></h1>
{% if flash %}<div class="flash">{{ flash }}</div>{% endif %}
<p style="color:#666; font-size:0.75rem;">Monthly audit tool — verify first_seen dates, check for missing or duplicate projects. Edit dates inline and click Save.</p>

<h2>1. All Ledger Entries <span class="count">{{ all_entries|length }}</span></h2>
<table>
  <tr><th>Project Name</th><th>First Seen</th><th></th><th>Flags</th><th>Status</th><th>ID</th><th></th></tr>
  {% for url, data in all_entries %}
  <tr>
    <td>{{ data.name }}</td>
    <td>
      <form class="edit-form" method="POST" action="/admin/ledger/edit">
        <input type="hidden" name="project_url" value="{{ url }}">
        <input type="date" class="date-input" name="first_seen" value="{{ data.first_seen }}">
        <button class="save" type="submit">Save</button>
      </form>
    </td>
    <td></td>
    <td>
      {% if url in multi_urls %}<span class="tag multi">Multi-forest</span>{% endif %}
      {% if url in dupe_urls %}<span class="tag dupe">Duplicate ID</span>{% endif %}
    </td>
    <td>
      {% if url in current_urls %}
        {% set p = project_map[url] %}
        <span class="tag active">{{ p.status or 'Active' }}</span>
      {% else %}
        <span class="tag inactive">Not in scrape</span>
      {% endif %}
    </td>
    <td><a href="{{ url }}" target="_blank">{{ url.split('/')[-1] }}</a></td>
    <td>
      <form class="delete-form" method="POST" action="/admin/ledger/delete" onsubmit="return confirm('Remove this entry?')">
        <input type="hidden" name="project_url" value="{{ url }}">
        <button class="del" type="submit">Remove</button>
      </form>
    </td>
  </tr>
  {% endfor %}
</table>

<h2>2. In Ledger but Missing from Current projects.json <span class="count">{{ missing_from_projects|length }}</span></h2>
{% if missing_from_projects %}
<table>
  <tr><th>Project Name</th><th>First Seen</th><th>URL</th><th></th></tr>
  {% for url, data in missing_from_projects %}
  <tr>
    <td>{{ data.name }}</td>
    <td>{{ data.first_seen }}</td>
    <td><a href="{{ url }}" target="_blank">{{ url }}</a></td>
    <td>
      <form class="delete-form" method="POST" action="/admin/ledger/delete" onsubmit="return confirm('Remove this entry?')">
        <input type="hidden" name="project_url" value="{{ url }}">
        <button class="del" type="submit">Remove</button>
      </form>
    </td>
  </tr>
  {% endfor %}
</table>
{% else %}<p class="none">None — ledger and projects.json are in sync.</p>{% endif %}

<h2>3. In projects.json but Missing from Ledger <span class="count">{{ missing_from_ledger|length }}</span></h2>
{% if missing_from_ledger %}
<table>
  <tr><th>Project Name</th><th>Forest</th><th>First Seen (from scraper)</th><th>URL</th></tr>
  {% for p in missing_from_ledger %}
  <tr>
    <td>{{ p.project_name }}</td>
    <td>{{ p.forest_name }}</td>
    <td>{{ p.first_seen or '—' }}</td>
    <td><a href="{{ p.project_url }}" target="_blank">{{ p.project_url.split('/')[-1] }}</a></td>
  </tr>
  {% endfor %}
</table>
{% else %}<p class="none">None — all current projects are in the ledger.</p>{% endif %}

<h2>4. Suspected Duplicate Project IDs <span class="count">{{ suspected_dupes|length }}</span></h2>
{% if suspected_dupes %}
<table>
  <tr><th>Project ID</th><th>URLs</th></tr>
  {% for pid, urls in suspected_dupes %}
  <tr>
    <td>{{ pid }}</td>
    <td>{% for u in urls %}<a href="{{ u }}" target="_blank">{{ u }}</a><br>{% endfor %}</td>
  </tr>
  {% endfor %}
</table>
{% else %}<p class="none">None found.</p>{% endif %}

<h2>5. Manual Notes</h2>
<p style="color:#666; font-size:0.75rem;">Edit any first_seen date using the date picker in Section 1 and click Save. To remove an entry entirely, use the Remove button — it will be re-added on the next scrape with today's date.</p>

</body>
</html>
"""
    return render_template_string(AUDIT_TEMPLATE,
        all_entries=all_entries,
        missing_from_projects=missing_from_projects,
        missing_from_ledger=missing_from_ledger,
        suspected_dupes=suspected_dupes,
        current_urls=current_urls,
        project_map=project_map,
        multi_urls=multi_urls,
        dupe_urls=dupe_urls,
        flash=flash,
    )


def _push_json_via_api(token: str, filename: str, message: str) -> bool:
    """Push any JSON file to GitHub root via API."""
    repo = "adshoemaker/usfs-scraper"
    api_url = f"https://api.github.com/repos/{repo}/contents/{filename}"
    with open(os.path.join(os.path.dirname(__file__), filename), "rb") as f:
        import base64
        content_b64 = base64.b64encode(f.read()).decode("utf-8")
    req = urllib.request.Request(api_url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    sha = None
    try:
        with urllib.request.urlopen(req) as resp:
            sha = json.loads(resp.read())["sha"]
    except urllib.error.HTTPError:
        pass
    payload_data = {"message": message, "content": content_b64}
    if sha:
        payload_data["sha"] = sha
    payload = json.dumps(payload_data).encode("utf-8")
    req2 = urllib.request.Request(api_url, data=payload, method="PUT", headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urllib.request.urlopen(req2) as resp:
            return True
    except urllib.error.HTTPError:
        return False



@app.route("/admin/ledger/edit", methods=["POST"])
def admin_ledger_edit():
    if not session.get("admin_authed"):
        return redirect(url_for("admin_login"))
    project_url = request.form.get("project_url", "").strip()
    first_seen  = request.form.get("first_seen", "").strip()
    if project_url and first_seen:
        ledger = load_ledger()
        if project_url in ledger:
            ledger[project_url]["first_seen"] = first_seen
            path = os.path.join(os.path.dirname(__file__), "ledger.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(ledger, f, indent=2, ensure_ascii=False, sort_keys=True)
            token = os.environ.get("GITHUB_TOKEN")
            if token:
                _push_json_via_api(token, "ledger.json", f"Ledger edit: {project_url.split('/')[-1]}")
    return redirect(url_for("admin_ledger") + "?flash=Date+updated+✓")


@app.route("/admin/ledger/delete", methods=["POST"])
def admin_ledger_delete():
    if not session.get("admin_authed"):
        return redirect(url_for("admin_login"))
    project_url = request.form.get("project_url", "").strip()
    if project_url:
        ledger = load_ledger()
        ledger.pop(project_url, None)
        path = os.path.join(os.path.dirname(__file__), "ledger.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(ledger, f, indent=2, ensure_ascii=False, sort_keys=True)
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            _push_json_via_api(token, "ledger.json", f"Ledger delete: {project_url.split('/')[-1]}")
    return redirect(url_for("admin_ledger") + "?flash=Entry+removed")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting USFS NEPA Project Tracker on port {port}...")
    if port == 5000:
        print("Open your browser and go to: http://localhost:5000")
    print("Press Ctrl+C to stop.")
    app.run(host="0.0.0.0", port=port, debug=False)
