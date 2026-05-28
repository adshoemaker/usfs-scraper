# forests.py
# ---------------------------------------------------------------
# Master list of National Forest NEPA project pages to scrape.
# Each entry is a dictionary with:
#   name        - Human-readable forest name
#   code        - Short identifier used in URLs and the database
#   region      - USFS region number
#   projects_url - Direct URL to the active projects listing page
#   state       - State(s) where the forest is located
#
# URLs verified manually against live fs.usda.gov pages.
# ---------------------------------------------------------------

FORESTS = [

    # ── Washington (Region 6) ──────────────────────────────────

    {
        "name": "Mt. Baker-Snoqualmie National Forest",
        "code": "mbs",
        "region": "R06",
        "state": "WA",
        "projects_url": "https://www.fs.usda.gov/r06/mbs/projects",
    },
    {
        "name": "Olympic National Forest",
        "code": "olympic",
        "region": "R06",
        "state": "WA",
        "projects_url": "https://www.fs.usda.gov/r06/olympic/projects",
    },
    {
        "name": "Okanogan-Wenatchee National Forest",
        "code": "okanogan-wenatchee",
        "region": "R06",
        "state": "WA",
        "projects_url": "https://www.fs.usda.gov/r06/okanogan-wenatchee/projects",
    },
    {
        "name": "Gifford Pinchot National Forest",
        "code": "giffordpinchot",
        "region": "R06",
        "state": "WA",
        "projects_url": "https://www.fs.usda.gov/r06/giffordpinchot/projects",
    },
    {
        "name": "Colville National Forest",
        "code": "colville",
        "region": "R06",
        "state": "WA",
        "projects_url": "https://www.fs.usda.gov/r06/colville/projects",
    },

    # ── Alaska (Region 10) ─────────────────────────────────────

    {
        "name": "Tongass National Forest",
        "code": "tongass",
        "region": "R10",
        "state": "AK",
        "projects_url": "https://www.fs.usda.gov/r10/tongass/projects",
    },
]


# ---------------------------------------------------------------
# Quick sanity-check: print all forests and their URLs.
# Run this file directly to verify the list looks right:
#   python forests.py
# ---------------------------------------------------------------

if __name__ == "__main__":
    print(f"{'Forest':<45} {'Region':<6} {'State':<5} URL")
    print("-" * 120)
    for f in FORESTS:
        print(f"{f['name']:<45} {f['region']:<6} {f['state']:<5} {f['projects_url']}")
    print(f"\nTotal forests: {len(FORESTS)}")
