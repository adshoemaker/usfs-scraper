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

    # ── Idaho (Region 1 + Region 4) ────────────────────────────

    {
        "name": "Nez Perce-Clearwater & Idaho Panhandle",
        "code": "nezperce-ipnf",
        "region": "R01",
        "state": "ID",
        "projects_url": "https://www.fs.usda.gov/r01/nezperce-clearwater/projects",
        "extra_urls": ["https://www.fs.usda.gov/r01/ipnf/projects"],
    },
    {
        "name": "Boise & Payette National Forests",
        "code": "boise-payette",
        "region": "R04",
        "state": "ID",
        "projects_url": "https://www.fs.usda.gov/r04/boise/projects",
        "extra_urls": ["https://www.fs.usda.gov/r04/payette/projects"],
    },
    {
        "name": "Caribou-Targhee National Forests",
        "code": "caribou-targhee",
        "region": "R04",
        "state": "ID",
        "projects_url": "https://www.fs.usda.gov/r04/caribou-targhee/projects",
    },
    {
        "name": "Salmon-Challis National Forest",
        "code": "salmon-challis",
        "region": "R04",
        "state": "ID",
        "projects_url": "https://www.fs.usda.gov/r04/salmon-challis/projects",
    },
    {
        "name": "Sawtooth National Forest",
        "code": "sawtooth",
        "region": "R04",
        "state": "ID",
        "projects_url": "https://www.fs.usda.gov/r04/sawtooth/projects",
    },

    # ── Oregon (Region 6) ──────────────────────────────────────

    {
        "name": "Rogue River-Siskiyou National Forest",
        "code": "rogue-siskiyou",
        "region": "R06",
        "state": "CA+OR",
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
        "name": "Fremont-Winema & Umatilla NFs",
        "code": "fremont-umatilla",
        "region": "R06",
        "state": "OR",
        "projects_url": "https://www.fs.usda.gov/r06/fremont-winema/projects",
        "extra_urls": ["https://www.fs.usda.gov/r06/umatilla/projects"],
    },
    {
        "name": "Deschutes National Forest",
        "code": "deschutes",
        "region": "R06",
        "state": "OR",
        "projects_url": "https://www.fs.usda.gov/r06/deschutes/projects",
    },
    {
        "name": "Mt. Hood National Forest",
        "code": "mthood",
        "region": "R06",
        "state": "OR",
        "projects_url": "https://www.fs.usda.gov/r06/mthood/projects",
    },
    {
        "name": "Ochoco National Forest",
        "code": "ochoco",
        "region": "R06",
        "state": "OR",
        "projects_url": "https://www.fs.usda.gov/r06/ochoco/projects",
    },
    {
        "name": "Willamette National Forest",
        "code": "willamette",
        "region": "R06",
        "state": "OR",
        "projects_url": "https://www.fs.usda.gov/r06/willamette/projects",
    },
    {
        "name": "Malheur National Forest",
        "code": "malheur",
        "region": "R06",
        "state": "OR",
        "projects_url": "https://www.fs.usda.gov/r06/malheur/projects",
    },
    {
        "name": "Siuslaw National Forest",
        "code": "siuslaw",
        "region": "R06",
        "state": "OR",
        "projects_url": "https://www.fs.usda.gov/r06/siuslaw/projects",
    },

    # ── California (Region 5) ──────────────────────────────────

    {
        "name": "Six Rivers & Mendocino NFs",
        "code": "sixrivers-mendocino",
        "region": "R05",
        "state": "CA",
        "projects_url": "https://www.fs.usda.gov/r05/sixrivers/projects",
        "extra_urls": ["https://www.fs.usda.gov/r05/mendocino/projects"],
    },
    {
        "name": "Shasta-Trinity National Forest",
        "code": "shasta-trinity",
        "region": "R05",
        "state": "CA",
        "projects_url": "https://www.fs.usda.gov/r05/shasta-trinity/projects",
    },
    {
        "name": "Klamath National Forest",
        "code": "klamath",
        "region": "R05",
        "state": "CA+OR",
        "projects_url": "https://www.fs.usda.gov/r05/klamath/projects",
    },
    {
        "name": "Lassen & Modoc National Forests",
        "code": "lassen-modoc",
        "region": "R05",
        "state": "CA",
        "projects_url": "https://www.fs.usda.gov/r05/lassen/projects",
        "extra_urls": ["https://www.fs.usda.gov/r05/modoc/projects"],
    },
    {
        "name": "Plumas National Forest",
        "code": "plumas",
        "region": "R05",
        "state": "CA",
        "projects_url": "https://www.fs.usda.gov/r05/plumas/projects",
    },
    {
        "name": "Tahoe & Eldorado National Forests",
        "code": "tahoe-eldorado",
        "region": "R05",
        "state": "CA",
        "projects_url": "https://www.fs.usda.gov/r05/tahoe/projects",
        "extra_urls": ["https://www.fs.usda.gov/r05/eldorado/projects"],
    },
    {
        "name": "Stanislaus National Forest",
        "code": "stanislaus",
        "region": "R05",
        "state": "CA",
        "projects_url": "https://www.fs.usda.gov/r05/stanislaus/projects",
    },
    {
        "name": "Sierra National Forest",
        "code": "sierra",
        "region": "R05",
        "state": "CA",
        "projects_url": "https://www.fs.usda.gov/r05/sierra/projects",
    },
    {
        "name": "Inyo & Sequoia National Forests",
        "code": "inyo-sequoia",
        "region": "R05",
        "state": "CA",
        "projects_url": "https://www.fs.usda.gov/r05/inyo/projects",
        "extra_urls": ["https://www.fs.usda.gov/r05/sequoia/projects"],
    },
    {
        "name": "Los Padres National Forest",
        "code": "lospadres",
        "region": "R05",
        "state": "CA",
        "projects_url": "https://www.fs.usda.gov/r05/lospadres/projects",
    },
    {
        "name": "Southern CA National Forests",
        "code": "socal",
        "region": "R05",
        "state": "CA",
        "projects_url": "https://www.fs.usda.gov/r05/angeles/projects",
        "extra_urls": [
            "https://www.fs.usda.gov/r05/cleveland/projects",
            "https://www.fs.usda.gov/r05/sanbernardino/projects",
        ],
    },

    # ── Alaska (Region 10) ─────────────────────────────────────

    {
        "name": "Chugach National Forest",
        "code": "chugach",
        "region": "R10",
        "state": "AK",
        "projects_url": "https://www.fs.usda.gov/r10/chugach/projects",
    },
    {
        "name": "Tongass National Forest",
        "code": "tongass",
        "region": "R10",
        "state": "AK",
        "projects_url": "https://www.fs.usda.gov/r10/tongass/projects",
    },
]


if __name__ == "__main__":
    print(f"{'Forest':<55} {'Region':<6} {'State':<5} URL")
    print("-" * 140)
    for f in FORESTS:
        print(f"{f['name']:<55} {f['region']:<6} {f['state']:<5} {f['projects_url']}")
    print(f"\nTotal forests: {len(FORESTS)}")
