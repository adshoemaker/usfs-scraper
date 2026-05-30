# forests.py
# ---------------------------------------------------------------
# Master list of National Forest NEPA project pages to scrape.
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

    # ── Oregon (Region 6) ──────────────────────────────────────

    {
        "name": "Rogue River-Siskiyou National Forest",
        "code": "rogue-siskiyou",
        "region": "R06",
        "state": "OR",
        "projects_url": "https://www.fs.usda.gov/r06/rogue-siskiyou/projects",
    },
    {
        "name": "Wallowa-Whitman National Forest",
        "code": "wallowa-whitman",
        "region": "R06",
        "state": "OR",
        "projects_url": "https://www.fs.usda.gov/r06/wallowa-whitman/projects",
    },
    {
        "name": "Fremont-Winema National Forest",
        "code": "fremont-winema",
        "region": "R06",
        "state": "OR",
        "projects_url": "https://www.fs.usda.gov/r06/fremont-winema/projects",
    },

    # ── California (Region 5) ──────────────────────────────────

    {
        "name": "Shasta-Trinity National Forest",
        "code": "shasta-trinity",
        "region": "R05",
        "state": "CA",
        "projects_url": "https://www.fs.usda.gov/r05/shasta-trinity/projects",
    },
    {
        "name": "Inyo National Forest",
        "code": "inyo",
        "region": "R05",
        "state": "CA",
        "projects_url": "https://www.fs.usda.gov/r05/inyo/projects",
    },
    {
        "name": "Los Padres National Forest",
        "code": "lospadres",
        "region": "R05",
        "state": "CA",
        "projects_url": "https://www.fs.usda.gov/r05/lospadres/projects",
    },
    {
        "name": "Klamath National Forest",
        "code": "klamath",
        "region": "R05",
        "state": "CA",
        "projects_url": "https://www.fs.usda.gov/r05/klamath/projects",
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


if __name__ == "__main__":
    print(f"{'Forest':<45} {'Region':<6} {'State':<5} URL")
    print("-" * 120)
    for f in FORESTS:
        print(f"{f['name']:<45} {f['region']:<6} {f['state']:<5} {f['projects_url']}")
    print(f"\nTotal forests: {len(FORESTS)}")
