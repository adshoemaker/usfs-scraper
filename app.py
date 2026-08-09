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
from flask import Flask, request, render_template_string, session, redirect, url_for, Response
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    LIMITER_AVAILABLE = True
except ImportError:
    LIMITER_AVAILABLE = False

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

if LIMITER_AVAILABLE:
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=["60 per minute", "500 per hour"],
        storage_uri="memory://",
    )
else:
    # Dummy limiter so decorators don't break if package not installed
    class _NoopLimiter:
        def exempt(self, f): return f
        def limit(self, *a, **kw):
            def decorator(f): return f
            return decorator
    limiter = _NoopLimiter()

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
            UNIQUE_CATS = {"taking_comments", "active", "newly_added", "ce_only"}
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
                if cat == "ce_only" and p.get("analysis_type") != "Categorical Exclusion":
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
        .top-search-inner { max-width: 1150px; margin: 0 auto; display: flex; justify-content: space-between; align-items: center; }
        .header-search { display: flex; align-items: center; gap: 0; }
        .header-search input[type="text"] { padding: 7px 14px; border: 1px solid #ccc; border-radius: 0; font-family: 'Poppins', sans-serif; font-size: 0.88rem; background: white; color: #1a1a1a; outline: none; flex: 1; }
        .header-search input[type="text"]::placeholder { color: #aaa; }
        .header-search input[type="text"]:focus { border-color: #888; }
        .header-search button { padding: 7px 18px; background: transparent; color: #8fa68e; border: 1.5px solid #8fa68e; border-radius: 0; font-family: 'Poppins', sans-serif; font-size: 0.88rem; font-weight: 400; cursor: pointer; white-space: nowrap; }
        .header-search button:hover { background: #8fa68e; color: white; }

        /* ── Forest summary ── */
        .forest-summary { background: #f7f7f0; border-bottom: 1px solid var(--border); padding: 10px 20px; background-image: url("data:image/jpeg;base64,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"); background-size: cover; background-position: top center; position: relative; }
        .forest-summary-inner { position: relative; z-index: 1; }
        .forest-summary::before { content: ""; position: absolute; inset: 0; background: rgba(255,255,255,0.50); z-index: 0; }
        .forest-summary-inner { max-width: 1150px; margin: 0 auto; display: flex; flex-direction: column; gap: 6px; }
        .forest-cols-row { display: flex; gap: 0; justify-content: center; width: 100%; }
        .forest-totals-row { display: flex; flex-direction: row; align-items: center; justify-content: space-between; gap: 12px; width: 100%; }
        .forest-totals-right { display: flex; align-items: center; gap: 12px; }
        .forest-reset-btn { display: inline-block; padding: 7px 18px; background: transparent; color: #e05a2b; font-family: 'Poppins', sans-serif; font-size: 0.88rem; font-weight: 400; border: 1.5px solid #e05a2b; cursor: pointer; text-decoration: none; white-space: nowrap; }
        .forest-reset-btn:hover { background: #e05a2b; color: white; }
        .about-btn { display: inline-block; padding: 7px 18px; background: transparent; color: #8fa68e; font-family: 'Poppins', sans-serif; font-size: 0.88rem; font-weight: 400; border: 1.5px solid #8fa68e; cursor: pointer; white-space: nowrap; }
        .about-btn.open { background: #8fa68e; color: white; }
        .about-btn:hover { background: #8fa68e; color: white; }
        .about-panel { display: none; background: white; border: 1px solid #ddd; padding: 16px 20px; margin-top: 8px; font-size: 0.82rem; color: #1a1a1a; line-height: 1.6; width: 100%; box-sizing: border-box; }
        .forest-col { display: flex; flex-direction: column; gap: 4px; flex: 1; min-width: 0; padding: 0 8px; }
        .forest-col-label { font-size: 0.6rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.6px; color: var(--text-dim); margin-bottom: 2px; }
        .forest-pill { display: flex; align-items: center; justify-content: space-between; gap: 5px; background: transparent; border: 1.5px solid; border-radius: 20px; padding: 3px 10px; font-size: 0.7rem; font-weight: 400; white-space: nowrap; width: 100%; box-sizing: border-box; text-decoration: none; transition: opacity 0.15s, box-shadow 0.15s; }
        .forest-pill.pill-selected { box-shadow: 0 0 0 2px white, 0 0 0 3px currentColor; }
        .forest-pill-count { background: rgba(0,0,0,0.08); border-radius: 10px; padding: 0 5px; font-size: 0.62rem; font-weight: 700; }
        .forest-pill.pill-selected .forest-pill-count { background: rgba(255,255,255,0.25); color: white; }
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
        .cat-btn.extractive  { border-color: var(--red);    color: var(--red);    background: transparent; }
        .cat-btn.extractive.active  { background: var(--red);    color: white; border-width: 3px; }
        .cat-btn .dot.extractive-dot  { background: var(--red); }
        .cat-btn.restorative { border-color: var(--green);  color: var(--green);  background: transparent; }
        .cat-btn.restorative.active { background: var(--green);  color: white; border-width: 3px; }
        .cat-btn .dot.restorative-dot { background: var(--green); }
        .cat-btn.mixed       { border-color: var(--orange); color: var(--orange); background: transparent; }
        .cat-btn.mixed.active       { background: var(--orange); color: white; border-width: 3px; }
        .cat-btn .dot.mixed-dot       { background: var(--orange); }
        .cat-btn.unclassified { border-color: #888; color: #555; background: transparent; }
        .cat-btn.unclassified.active { background: #888; color: white; border-width: 3px; }
        .cat-btn .dot.unclassified-dot { background: #888; }
        .cat-btn.newly-added { border-color: #6aabdf; border-width: 3px; color: #5599cc; background: transparent; padding: 6px 37px; }
        .cat-btn.newly-added.active { background: #6aabdf; color: white; }
        .cat-btn .dot.newly-added-dot { background: #6aabdf; }
        .cat-btn.taking-comments { border-color: #a83030; border-width: 3px; color: #a83030; background: transparent; padding: 6px 37px; }
        .cat-btn.taking-comments.active { background: #a83030; color: white; }
        .cat-btn .dot.taking-comments-dot { background: #a83030; }
        .cat-btn.ce-only { border-color: #7a6a3a; border-width: 3px; color: #7a6a3a; background: transparent; padding: 6px 37px; }
        .cat-btn.ce-only.active { background: #7a6a3a; color: white; }
        .cat-btn .dot.ce-only-dot { background: #7a6a3a; }
        .cat-btn.active-filter { border-color: var(--green); border-width: 3px; color: var(--green); background: transparent; padding: 6px 37px; }
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
        .ea-badge { display: block; padding: 3px 10px; border-radius: 0; font-size: 0.918rem; font-weight: 200; font-family: 'Poppins', sans-serif; color: #555; white-space: nowrap; letter-spacing: 0.8px; text-align: center; width: 255px; box-sizing: border-box; background: #e0e0dc; margin-top: 6px; }
        .eis-badge { display: block; padding: 3px 10px; border-radius: 0; font-size: 0.918rem; font-weight: 200; font-family: 'Poppins', sans-serif; color: #555; white-space: nowrap; letter-spacing: 0.8px; text-align: center; width: 255px; box-sizing: border-box; background: #e0e0dc; margin-top: 6px; }

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
        <a href="mailto:andrew@wlfdc.org?subject=LFDC%20Tracker%20Feedback%20%2F%20Feature%20Suggestion" style="font-family:'Poppins',sans-serif; font-size:0.88rem; font-weight:400; color:#8fa68e; text-decoration:none; background:transparent; border:1.5px solid #8fa68e; padding:7px 18px; white-space:nowrap;" class="desktop-only">Submit Feedback — Suggest Features</a>
        <form class="header-search" method="GET" action="/" id="searchform" style="position:relative;">
            <input type="hidden" name="forest"   value="{{ selected_forest }}">
            <input type="hidden" name="status"   value="{{ selected_status }}">
            <input type="hidden" name="days"     value="{{ selected_days }}">
            <input type="hidden" name="sort"     value="{{ selected_sort }}">
            <input type="hidden" name="sort2"    value="{{ selected_sort2 }}">
            <input type="hidden" name="category" value="{{ selected_category_str }}">
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
                    {% if f.code == 'ochoco' %}
                    {% set combo_sel = ('ochoco' in selected_forests) and ('malheur' in selected_forests) %}
                    {% set combo_total = (forest_counts.get('ochoco', {}).get('total', 0) or 0) + (forest_counts.get('malheur', {}).get('total', 0) or 0) %}
                    <a href="{{ toggle_multi_forest_url(['ochoco','malheur'], selected_forests_str) }}"
                       class="forest-pill {{ 'pill-selected' if combo_sel else '' }}"
                       style="{{ 'background:' + sc.get('pill','var(--accent)') + '; color:white; border-color:' + sc.get('pill','var(--accent)') + ';' if (not selected_forests or combo_sel) else 'background:transparent; color:' + sc.get('pill','var(--accent)') + '; border-color:' + sc.get('pill','var(--accent)') + ';' }} opacity:{{ '1' if (not selected_forests or combo_sel) else '0.4' }}; text-decoration:none;">
                        Ochoco &amp; Malheur NF
                        <span class="forest-pill-count">{{ combo_total }}</span>
                    </a>
                    {% elif f.code == 'malheur' %}
                    {# Skip — combined with Ochoco #}
                    {% else %}
                    {% set is_sel = f.code in selected_forests %}
                    <a href="{{ toggle_forest_url(f.code, selected_forests_str) }}"
                       class="forest-pill {{ 'pill-selected' if is_sel else '' }}"
                       style="{{ 'background:' + sc.get('pill','var(--accent)') + '; color:white; border-color:' + sc.get('pill','var(--accent)') + ';' if (not selected_forests or is_sel) else 'background:transparent; color:' + sc.get('pill','var(--accent)') + '; border-color:' + sc.get('pill','var(--accent)') + ';' }} opacity:{{ '1' if (not selected_forests or is_sel) else '0.4' }}; text-decoration:none;">
                        {{ f.name.replace('National Forest', 'NF') }}
                        <span class="forest-pill-count">{{ forest_counts[f.code].total }}</span>
                    </a>
                    {% endif %}
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
                    {% if f.code == 'ochoco' %}
                    {% set combo_sel = ('ochoco' in selected_forests) and ('malheur' in selected_forests) %}
                    {% set combo_total = (forest_counts.get('ochoco', {}).get('total', 0) or 0) + (forest_counts.get('malheur', {}).get('total', 0) or 0) %}
                    <a href="{{ toggle_multi_forest_url(['ochoco','malheur'], selected_forests_str) }}"
                       class="forest-pill {{ 'pill-selected' if combo_sel else '' }}"
                       style="{{ 'background:' + sc.get('pill','var(--accent)') + '; color:white; border-color:' + sc.get('pill','var(--accent)') + ';' if (not selected_forests or combo_sel) else 'background:transparent; color:' + sc.get('pill','var(--accent)') + '; border-color:' + sc.get('pill','var(--accent)') + ';' }} opacity:{{ '1' if (not selected_forests or combo_sel) else '0.4' }}; text-decoration:none;">
                        Ochoco &amp; Malheur NF
                        <span class="forest-pill-count">{{ combo_total }}</span>
                    </a>
                    {% elif f.code == 'malheur' %}
                    {# Skip — combined with Ochoco #}
                    {% else %}
                    {% set is_sel = f.code in selected_forests %}
                    <a href="{{ toggle_forest_url(f.code, selected_forests_str) }}"
                       class="forest-pill {{ 'pill-selected' if is_sel else '' }}"
                       style="{{ 'background:' + sc.get('pill','var(--accent)') + '; color:white; border-color:' + sc.get('pill','var(--accent)') + ';' if (not selected_forests or is_sel) else 'background:transparent; color:' + sc.get('pill','var(--accent)') + '; border-color:' + sc.get('pill','var(--accent)') + ';' }} opacity:{{ '1' if (not selected_forests or is_sel) else '0.4' }}; text-decoration:none;">
                        {{ f.name.replace('National Forest', 'NF') }}
                        <span class="forest-pill-count">{{ forest_counts[f.code].total }}</span>
                    </a>
                    {% endif %}
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
                {% if f.code == 'ochoco' %}
                {% set combo_codes = ['ochoco','malheur'] %}
                {% set combo_sel = ('ochoco' in selected_forests) and ('malheur' in selected_forests) %}
                {% set combo_total = (forest_counts.get('ochoco', {}).get('total', 0) or 0) + (forest_counts.get('malheur', {}).get('total', 0) or 0) %}
                <a href="{{ toggle_multi_forest_url(['ochoco','malheur'], selected_forests_str) }}"
                   class="forest-pill {{ 'pill-selected' if combo_sel else '' }}"
                   style="{{ 'background:' + sc.get('pill','var(--accent)') + '; color:white; border-color:' + sc.get('pill','var(--accent)') + ';' if (not selected_forests or combo_sel) else 'background:transparent; color:' + sc.get('pill','var(--accent)') + '; border-color:' + sc.get('pill','var(--accent)') + ';' }} opacity:{{ '1' if (not selected_forests or combo_sel) else '0.4' }}; text-decoration:none;">
                    Ochoco &amp; Malheur NF
                    <span class="forest-pill-count">{{ combo_total }}</span>
                </a>
                {% elif f.code == 'malheur' %}
                {# Skip — combined with Ochoco #}
                {% else %}
                {% set is_sel = f.code in selected_forests %}
                <a href="{{ toggle_forest_url(f.code, selected_forests_str) }}"
                   class="forest-pill {{ 'pill-selected' if is_sel else '' }}"
                   style="{{ 'background:' + sc.get('pill','var(--accent)') + '; color:white; border-color:' + sc.get('pill','var(--accent)') + ';' if (not selected_forests or is_sel) else 'background:transparent; color:' + sc.get('pill','var(--accent)') + '; border-color:' + sc.get('pill','var(--accent)') + ';' }} opacity:{{ '1' if (not selected_forests or is_sel) else '0.4' }}; text-decoration:none;">
                    {{ f.name.replace('National Forest', 'NF') }}
                    <span class="forest-pill-count">{{ forest_counts[f.code].total }}</span>
                </a>
                {% endif %}
                {% endfor %}
            </div>
            {% endif %}
            {% endfor %}
        </div>
        <div class="forest-totals-row">
            {% if annotations.get('_about_text') %}
            <button type="button" class="about-btn" id="about-toggle"
                onclick="var p=document.getElementById('about-panel'); var open=p.style.display==='block'; p.style.display=open?'none':'block'; this.classList.toggle('open',!open);">
                About the LFDC NEPA Tracker
            </button>
            {% else %}
            <span></span>
            {% endif %}
            <div class="forest-totals-right">
                <span class="summary-totals">
                    <strong>{{ forest_counts.values()|sum(attribute='total') + multi_count }}</strong> total
                </span>
                <a href="{{ url_with_show_inactive }}"
                   class="forest-reset-btn"
                   style="{{ 'background:#5a7a58; color:white; border-color:#5a7a58;' if show_inactive else 'background:transparent; color:#888; border-color:#888;' }}">
                    Show Inactive
                </a>
                <a href="/?reset_inactive=1" class="forest-reset-btn">Reset</a>
            </div>
        </div>
        {% if annotations.get('_about_text') %}
        <div id="about-panel" class="about-panel">{{ annotations.get('_about_text') | safe }}</div>
        {% endif %}
        <div class="mobile-only" style="padding-top:6px;">
            <a href="mailto:andrew@wlfdc.org?subject=LFDC%20Tracker%20Feedback%20%2F%20Feature%20Suggestion"
               style="font-family:'Poppins',sans-serif; font-size:0.82rem; font-weight:400; color:#8fa68e; text-decoration:none; background:transparent; border:1.5px solid #8fa68e; padding:6px 16px; white-space:nowrap;">
                Submit Feedback — Suggest Features
            </a>
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
        <a href="{{ url_with_category('ce_only') }}"
           class="cat-btn ce-only {{ 'active' if 'ce_only' in selected_categories else '' }}">
            <span class="dot ce-only-dot"></span>
            Categorical Exclusions
        </a>
    </div>
    <div class="category-disclaimer-row">
        <span class="category-disclaimer">*Impact level assigned automatically, based on keywords and is intended as a general guide only</span>
    </div>

    <div class="results-header">
        {% if show_inactive and not (search or selected_forest or selected_status or selected_days or selected_category_str) %}
            <strong>{{ projects|length }}</strong> of <strong>{{ total }}</strong>
        {% elif selected_categories %}
            Showing: <strong>{{ projects|length }}</strong>
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

                    <!-- Mobile: Taking Comments Now badge -->
                    {% if p.get('accepting_comments') %}
                    <div class="comment-open-badge mobile-only">
                        <span class="badge-title">{{ 'Taking Objections Now!' if annotations.get(p.project_url, {}).get('taking_objections') else 'Taking Comments Now!' }}</span>
                        {% if p.get('comment_deadline') %}
                        <span class="badge-deadline">{{ format_deadline(p.comment_deadline) }}</span>
                        {% endif %}
                    </div>
                    {% endif %}

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
                        <span class="badge-title">{{ 'Taking Objections Now!' if annotations.get(p.project_url, {}).get('taking_objections') else 'Taking Comments Now!' }}</span>
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
                    {% elif p.get('analysis_type') == 'Environmental Assessment' %}
                    <span class="ea-badge">Environmental Assessment</span>
                    {% elif p.get('analysis_type') == 'Environmental Impact Statement' %}
                    <span class="eis-badge">Env. Impact Statement</span>
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
    Last updated: {{ last_scraped }}
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


def toggle_multi_forest_url_fn(codes, current_str):
    """Toggle multiple forest codes together as a group."""
    from flask import request as req
    from urllib.parse import urlencode
    current = [f.strip() for f in current_str.split(",") if f.strip()]
    all_selected = all(c in current for c in codes)
    if all_selected:
        new = [c for c in current if c not in codes]
    else:
        new = current + [c for c in codes if c not in current]
    args = {}
    if req.args.get("q"):      args["q"]        = req.args.get("q")
    if req.args.get("status"): args["status"]   = req.args.get("status")
    if req.args.get("days"):   args["days"]     = req.args.get("days")
    if req.args.get("sort"):   args["sort"]     = req.args.get("sort")
    if req.args.get("sort2"):  args["sort2"]    = req.args.get("sort2")
    if req.args.get("category"): args["category"] = req.args.get("category")
    if new:                    args["forests"]  = ",".join(new)
    return "/?" + urlencode(args) if args else "/"


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
    if req.args.get("category"):  args["category"] = req.args.get("category")
    if new:                       args["forests"]  = ",".join(new)
    return "/?" + urlencode(args) if args else "/"



@app.route("/robots.txt")
@limiter.exempt
def robots_txt():
    content = "\n".join([
        "User-agent: *",
        "Disallow: /",
        "",
        "User-agent: GPTBot",
        "Disallow: /",
        "",
        "User-agent: ClaudeBot",
        "Disallow: /",
        "",
        "User-agent: Amazonbot",
        "Disallow: /",
        "",
        "User-agent: anthropic-ai",
        "Disallow: /",
        "",
        "User-agent: Google-Extended",
        "Disallow: /",
        "",
        "User-agent: CCBot",
        "Disallow: /",
        "",
        "User-agent: Bytespider",
        "Disallow: /",
    ])
    return Response(content, mimetype="text/plain")


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
    # show_inactive is cookie-based for durability — toggle via ?toggle_inactive=1, cleared by Reset (/?reset_inactive=1)
    if request.args.get("toggle_inactive") == "1":
        show_inactive = request.cookies.get("show_inactive", "0") != "1"
    elif request.args.get("reset_inactive") == "1" or request.path == "/" and not request.query_string:
        show_inactive = False
    else:
        show_inactive = request.cookies.get("show_inactive", "0") == "1"
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
        args["toggle_inactive"] = "1"
        qs = urlencode(args)
        return f"/?{qs}"

    def url_with_category(cat):
        from urllib.parse import urlencode
        QUICK_FILTERS = {"newly_added", "taking_comments", "ce_only"}
        cats = list(selected_categories)
        if cat in cats:
            cats.remove(cat)
        else:
            # If toggling a quick-filter on, remove the other two quick-filters
            if cat in QUICK_FILTERS:
                cats = [c for c in cats if c not in QUICK_FILTERS]
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

    rendered = render_template_string(
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
        toggle_multi_forest_url=toggle_multi_forest_url_fn,
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
    resp = Response(rendered, mimetype="text/html")
    resp.set_cookie("show_inactive", "1" if show_inactive else "0", max_age=60*60*24*365, samesite="Lax")
    return resp


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

<div style="background:#f7f7f0; border:1px solid #ddd; padding:12px 18px; margin-bottom:24px; max-width:900px;">
  <strong style="font-size:0.82rem; color:#555;">About the LFDC NEPA Tracker</strong>
  <p style="font-size:0.75rem; color:#666; margin:4px 0 8px;">Text shown when users click the "About" button on the main page. Leave blank to hide the button.</p>
  <form method="POST" action="/admin/save-about">
    <div style="display:flex; gap:4px; margin-bottom:4px;">
      <button type="button" onclick="wrapSelection('about_text','<b>','</b>')" style="padding:2px 8px; font-size:0.72rem; font-weight:700; border:1px solid #ccc; background:white; cursor:pointer;">B</button>
      <button type="button" onclick="insertAt('about_text','<br>')" style="padding:2px 8px; font-size:0.72rem; border:1px solid #ccc; background:white; cursor:pointer;">↵ Line Break</button>
      <button type="button" onclick="insertBullet('about_text')" style="padding:2px 8px; font-size:0.72rem; border:1px solid #ccc; background:white; cursor:pointer;">• Bullet</button>
    </div>
    <textarea name="about_text" id="about_text" style="width:100%; height:120px; font-family:inherit; font-size:0.82rem; padding:8px; border:1px solid #ccc; box-sizing:border-box;">{{ annotations.get('_about_text', '') }}</textarea>
    <br>
    <button type="submit" style="margin-top:8px; padding:5px 16px; background:#2d7a1f; color:white; border:none; font-family:inherit; font-size:0.78rem; cursor:pointer;">Save</button>
  </form>
  <script>
  function wrapSelection(id, before, after) {
    var ta = document.getElementById(id);
    var s = ta.selectionStart, e = ta.selectionEnd;
    var sel = ta.value.substring(s, e);
    ta.value = ta.value.substring(0, s) + before + sel + after + ta.value.substring(e);
    ta.selectionStart = s + before.length;
    ta.selectionEnd = e + before.length;
    ta.focus();
  }
  function insertAt(id, text) {
    var ta = document.getElementById(id);
    var s = ta.selectionStart;
    ta.value = ta.value.substring(0, s) + text + ta.value.substring(s);
    ta.selectionStart = ta.selectionEnd = s + text.length;
    ta.focus();
  }
  function insertBullet(id) {
    var ta = document.getElementById(id);
    var s = ta.selectionStart;
    var before = ta.value.substring(0, s);
    var after = ta.value.substring(s);
    // Check if we're already inside a <ul>
    var lastUlOpen = before.lastIndexOf('<ul>');
    var lastUlClose = before.lastIndexOf('</ul>');
    if (lastUlOpen > lastUlClose) {
      // Already in a list — just add a new <li>
      ta.value = before + '<li></li>' + after;
      ta.selectionStart = ta.selectionEnd = s + 4;
    } else {
      // Start a new list
      ta.value = before + '<ul><li></li></ul>' + after;
      ta.selectionStart = ta.selectionEnd = s + 8;
    }
    ta.focus();
  }
  </script>
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
      <label style="display:flex; align-items:center; gap:8px; margin-bottom:10px; font-size:0.82rem; cursor:pointer;">
        <input type="checkbox" name="taking_objections" value="1" {{ 'checked' if annotations.get(p.project_url, {}).get('taking_objections') else '' }}>
        Show badge as <strong>"Taking Objections Now"</strong> instead of "Taking Comments Now"
      </label>
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


@limiter.exempt
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


@limiter.exempt
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


@limiter.exempt
@app.route("/admin/save-about", methods=["POST"])
def admin_save_about():
    if not session.get("admin_authed"):
        return redirect(url_for("admin_login"))
    about_text = request.form.get("about_text", "").strip()
    annotations = load_annotations()
    annotations["_about_text"] = about_text
    save_annotations_local(annotations)
    save_annotations_github(annotations)
    return redirect(url_for("admin") + "?flash=About+text+saved+✓")


@limiter.exempt
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


@limiter.exempt
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


@limiter.exempt
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


@limiter.exempt
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


@limiter.exempt
@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_authed", None)
    return redirect(url_for("admin_login"))


@limiter.exempt
@app.route("/admin/save", methods=["POST"])
def admin_save():
    if not session.get("admin_authed"):
        return redirect(url_for("admin_login"))

    project_url      = request.form.get("project_url", "").strip()
    annotation       = request.form.get("annotation", "").strip()
    intro            = request.form.get("intro", "").strip()
    notes            = request.form.get("notes", "").strip()
    taking_objections = request.form.get("taking_objections") == "1"

    if not project_url:
        return redirect(url_for("admin"))

    annotations = load_annotations()
    if annotation or intro or notes or taking_objections:
        existing = annotations.get(project_url, {})
        existing.update({
            "intro":             intro,
            "annotation":        annotation,
            "notes":             notes,
            "taking_objections": taking_objections,
            "updated":           datetime.datetime.utcnow().isoformat(),
        })
        annotations[project_url] = existing
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


@limiter.exempt
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



@limiter.exempt
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


@limiter.exempt
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
