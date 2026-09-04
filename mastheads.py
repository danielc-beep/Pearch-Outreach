"""
The ACM mastheads a prospect can be pitched from.

A business in Newcastle hears "the Newcastle Herald" as their paper. They
hear "Australian Community Media" as a company they have not dealt with.
The masthead is the credential that carries locally, so every outreach email
is written from one, and this is the register of which ones exist and where
each of them lands.

Sites and states come from the 2026 SEO rate card. The `matches` keywords are
what the search location is checked against, so typing "Newcastle NSW" in the
prospecting bar selects the Newcastle Herald without anyone choosing it.
"""
from __future__ import annotations

from typing import Any

# name    — how the masthead is written in an email. This is the string a
#           business reads, so it is the one worth getting right.
# site    — the domain, and the stable key. Names get restyled; domains do not.
# state   — for grouping the picker.
# matches — suburb, city and region words that select this masthead from a
#           typed location. Lower case; matched as substrings both ways.
TITLES: list[dict[str, Any]] = [
    # --- Metro and major regional dailies ---
    {"site": "canberratimes.com.au", "name": "The Canberra Times", "state": "ACT",
     "matches": ["canberra", "act", "queanbeyan", "gungahlin", "belconnen", "woden"]},
    {"site": "newcastleherald.com.au", "name": "the Newcastle Herald", "state": "NSW",
     "matches": ["newcastle", "hunter", "lake macquarie", "charlestown", "hamilton",
                 "merewether", "cooks hill", "wallsend", "kotara", "belmont", "warners bay"]},
    {"site": "theleader.com.au", "name": "The St George and Sutherland Leader", "state": "NSW",
     "matches": ["sutherland", "st george", "cronulla", "miranda", "hurstville", "kogarah",
                 "caringbah", "engadine", "menai", "sylvania", "rockdale", "shire"]},
    {"site": "illawarramercury.com.au", "name": "the Illawarra Mercury", "state": "NSW",
     "matches": ["illawarra", "wollongong", "shellharbour", "kiama", "dapto", "corrimal",
                 "thirroul", "port kembla", "figtree"]},
    {"site": "thecourier.com.au", "name": "The Courier", "state": "VIC",
     "matches": ["ballarat", "sebastopol", "wendouree", "creswick", "daylesford"]},
    {"site": "bendigoadvertiser.com.au", "name": "the Bendigo Advertiser", "state": "VIC",
     "matches": ["bendigo", "kangaroo flat", "eaglehawk", "castlemaine", "heathcote"]},
    {"site": "standard.net.au", "name": "The Standard", "state": "VIC",
     "matches": ["warrnambool", "port fairy", "koroit", "terang", "south west victoria"]},
    {"site": "bordermail.com.au", "name": "The Border Mail", "state": "NSW/VIC",
     "matches": ["albury", "wodonga", "lavington", "wangaratta", "corowa", "border"]},
    {"site": "theadvocate.com.au", "name": "The Advocate", "state": "TAS",
     "matches": ["burnie", "devonport", "ulverstone", "north west tasmania", "wynyard"]},
    {"site": "examiner.com.au", "name": "The Examiner", "state": "TAS",
     "matches": ["launceston", "tasmania", "tas", "george town", "deloraine", "scottsdale"]},
    {"site": "bluemountainsgazette.com.au", "name": "the Blue Mountains Gazette", "state": "NSW",
     "matches": ["blue mountains", "katoomba", "springwood", "leura", "blackheath", "lawson"]},
    {"site": "maitlandmercury.com.au", "name": "The Maitland Mercury", "state": "NSW",
     "matches": ["maitland", "rutherford", "thornton", "morpeth", "east maitland"]},
    {"site": "southcoastregister.com.au", "name": "the South Coast Register", "state": "NSW",
     "matches": ["nowra", "shoalhaven", "berry", "sanctuary point", "huskisson", "jervis bay"]},
    {"site": "northweststar.com.au", "name": "The North West Star", "state": "QLD",
     "matches": ["mount isa", "mt isa", "cloncurry", "north west queensland"]},
    {"site": "northerndailyleader.com.au", "name": "The Northern Daily Leader", "state": "NSW",
     "matches": ["tamworth", "gunnedah", "quirindi", "new england north west"]},
    {"site": "centralwesterndaily.com.au", "name": "the Central Western Daily", "state": "NSW",
     "matches": ["orange", "molong", "canowindra", "central west"]},
    {"site": "dailyliberal.com.au", "name": "the Daily Liberal", "state": "NSW",
     "matches": ["dubbo", "wellington nsw", "narromine"]},
    {"site": "portnews.com.au", "name": "the Port Macquarie News", "state": "NSW",
     "matches": ["port macquarie", "wauchope", "laurieton", "camden haven"]},
    {"site": "dailyadvertiser.com.au", "name": "The Daily Advertiser", "state": "NSW",
     "matches": ["wagga", "wagga wagga", "riverina", "tumut", "gundagai"]},
    {"site": "begadistrictnews.com.au", "name": "the Bega District News", "state": "NSW",
     "matches": ["bega", "tathra", "candelo", "bemboka"]},
    {"site": "westernadvocate.com.au", "name": "the Western Advocate", "state": "NSW",
     "matches": ["bathurst", "kelso", "perthville"]},
    {"site": "hawkesburygazette.com.au", "name": "the Hawkesbury Gazette", "state": "NSW",
     "matches": ["hawkesbury", "windsor", "richmond nsw", "north richmond", "pitt town"]},
    {"site": "southernhighlandnews.com.au", "name": "the Southern Highland News", "state": "NSW",
     "matches": ["bowral", "mittagong", "moss vale", "southern highlands", "berrima"]},
    {"site": "armidaleexpress.com.au", "name": "The Armidale Express", "state": "NSW",
     "matches": ["armidale", "uralla", "guyra", "new england"]},
    {"site": "goulburnpost.com.au", "name": "the Goulburn Post", "state": "NSW",
     "matches": ["goulburn", "marulan", "southern tablelands"]},
    {"site": "queanbeyanage.com.au", "name": "The Queanbeyan Age", "state": "ACT",
     "matches": ["queanbeyan", "jerrabomberra", "googong"]},
    {"site": "portstephensexaminer.com.au", "name": "the Port Stephens Examiner", "state": "NSW",
     "matches": ["port stephens", "nelson bay", "raymond terrace", "medowie", "anna bay"]},
    {"site": "manningrivertimes.com.au", "name": "the Manning River Times", "state": "NSW",
     "matches": ["taree", "manning", "wingham", "old bar", "forster"]},
    {"site": "lithgowmercury.com.au", "name": "the Lithgow Mercury", "state": "NSW",
     "matches": ["lithgow", "portland nsw", "wallerawang"]},
    {"site": "batemansbaypost.com.au", "name": "the Bay Post", "state": "NSW",
     "matches": ["batemans bay", "moruya", "eurobodalla"]},
    {"site": "macleayargus.com.au", "name": "the Macleay Argus", "state": "NSW",
     "matches": ["kempsey", "south west rocks", "macleay"]},
    {"site": "inverelltimes.com.au", "name": "The Inverell Times", "state": "NSW",
     "matches": ["inverell", "ashford"]},
    {"site": "moreechampion.com.au", "name": "the Moree Champion", "state": "NSW",
     "matches": ["moree", "boggabilla"]},
    {"site": "lismorecitynews.com.au", "name": "the Lismore City News", "state": "NSW",
     "matches": ["lismore", "northern rivers", "ballina", "byron"]},
    {"site": "mudgeeguardian.com.au", "name": "the Mudgee Guardian", "state": "NSW",
     "matches": ["mudgee", "gulgong", "rylstone"]},
    {"site": "yasstribune.com.au", "name": "the Yass Tribune", "state": "NSW",
     "matches": ["yass", "murrumbateman", "binalong"]},
    {"site": "greatlakesadvocate.com.au", "name": "the Great Lakes Advocate", "state": "NSW",
     "matches": ["great lakes", "tuncurry", "pacific palms"]},
    {"site": "singletonargus.com.au", "name": "The Singleton Argus", "state": "NSW",
     "matches": ["singleton", "branxton"]},
    {"site": "muswellbrookchronicle.com.au", "name": "the Muswellbrook Chronicle", "state": "NSW",
     "matches": ["muswellbrook", "denman", "upper hunter"]},
    {"site": "cessnockadvertiser.com.au", "name": "the Cessnock Advertiser", "state": "NSW",
     "matches": ["cessnock", "kurri kurri", "pokolbin"]},
    {"site": "huntervalleynews.net.au", "name": "the Hunter Valley News", "state": "NSW",
     "matches": ["hunter valley", "lovedale", "rothbury"]},
    {"site": "sconeadvocate.com.au", "name": "the Scone Advocate", "state": "NSW",
     "matches": ["scone", "aberdeen nsw", "murrurundi"]},
    {"site": "dungogchronicle.com.au", "name": "the Dungog Chronicle", "state": "NSW",
     "matches": ["dungog", "clarence town", "gresford"]},
    {"site": "gloucesteradvocate.com.au", "name": "the Gloucester Advocate", "state": "NSW",
     "matches": ["gloucester nsw", "barrington"]},
    {"site": "areanews.com.au", "name": "The Area News", "state": "NSW",
     "matches": ["griffith", "leeton", "murrumbidgee", "yenda"]},
    {"site": "irrigator.com.au", "name": "The Irrigator", "state": "NSW",
     "matches": ["leeton", "whitton"]},
    {"site": "ulladullatimes.com.au", "name": "the Ulladulla Times", "state": "NSW",
     "matches": ["ulladulla", "milton nsw", "mollymook"]},
    {"site": "edenmagnet.com.au", "name": "the Eden Magnet", "state": "NSW",
     "matches": ["eden nsw", "boydtown"]},
    {"site": "merimbulanewsweekly.com.au", "name": "the Merimbula News Weekly", "state": "NSW",
     "matches": ["merimbula", "pambula", "tura beach"]},
    {"site": "naroomanewsonline.com.au", "name": "the Narooma News", "state": "NSW",
     "matches": ["narooma", "dalmeny", "bermagui"]},
    {"site": "wellingtontimes.com.au", "name": "the Wellington Times", "state": "NSW",
     "matches": ["wellington nsw"]},
    {"site": "camdencourier.com.au", "name": "the Camden Haven Courier", "state": "NSW",
     "matches": ["camden haven", "laurieton", "north haven", "kendall"]},
    {"site": "gleninnesexaminer.com.au", "name": "the Glen Innes Examiner", "state": "NSW",
     "matches": ["glen innes", "emmaville"]},
    {"site": "crookwellgazette.com.au", "name": "the Crookwell Gazette", "state": "NSW",
     "matches": ["crookwell", "taralga"]},
    {"site": "blayneychronicle.com.au", "name": "the Blayney Chronicle", "state": "NSW",
     "matches": ["blayney", "millthorpe"]},
    {"site": "hardenexpress.com.au", "name": "the Harden Express", "state": "NSW",
     "matches": ["harden", "murrumburrah"]},
    {"site": "tenterfieldstar.com.au", "name": "the Tenterfield Star", "state": "NSW",
     "matches": ["tenterfield"]},
    {"site": "juneesoutherncross.com.au", "name": "the Junee Southern Cross", "state": "NSW",
     "matches": ["junee"]},
    {"site": "braidwoodtimes.com.au", "name": "the Braidwood Times", "state": "NSW",
     "matches": ["braidwood"]},
    {"site": "cootamundraherald.com.au", "name": "the Cootamundra Herald", "state": "NSW",
     "matches": ["cootamundra", "gundagai"]},
    {"site": "oberonreview.com.au", "name": "the Oberon Review", "state": "NSW",
     "matches": ["oberon"]},
    {"site": "narrominenewsonline.com.au", "name": "the Narromine News", "state": "NSW",
     "matches": ["narromine", "trangie"]},
    {"site": "easternriverinachronicle.com.au", "name": "the Eastern Riverina Chronicle",
     "state": "NSW", "matches": ["wagga", "the rock", "henty", "eastern riverina"]},
    {"site": "nynganobserver.com.au", "name": "the Nyngan Observer", "state": "NSW",
     "matches": ["nyngan", "warren nsw"]},
    {"site": "nvi.com.au", "name": "the Nambucca Valley Independent", "state": "NSW",
     "matches": ["nambucca", "macksville", "bowraville", "valla"]},
    {"site": "therural.com.au", "name": "The Rural", "state": "NSW", "matches": []},
    {"site": "katherinetimes.com.au", "name": "the Katherine Times", "state": "NT",
     "matches": ["katherine", "northern territory", "nt"]},
    {"site": "hepburnadvocate.com.au", "name": "the Hepburn Advocate", "state": "VIC",
     "matches": ["hepburn", "daylesford", "clunes"]},

    # --- National and rural mastheads. No location keywords: these are chosen
    #     for the kind of business, not the town it sits in. ---
    {"site": "theland.com.au", "name": "The Land", "state": "National", "matches": []},
    {"site": "queenslandcountrylife.com.au", "name": "Queensland Country Life",
     "state": "National", "matches": []},
    {"site": "farmweekly.com.au", "name": "Farm Weekly", "state": "National", "matches": []},
    {"site": "stockandland.com.au", "name": "Stock & Land", "state": "National", "matches": []},
    {"site": "stockjournal.com.au", "name": "Stock Journal", "state": "National", "matches": []},
    {"site": "northqueenslandregister.com.au", "name": "North Queensland Register",
     "state": "National", "matches": []},
    {"site": "farmonline.com.au", "name": "Farmonline", "state": "National", "matches": []},
    {"site": "goodfruitandvegetables.com.au", "name": "Good Fruit & Vegetables",
     "state": "National", "matches": []},
    {"site": "thesenior.com.au", "name": "The Senior", "state": "National", "matches": []},
    {"site": "horsedeals.com.au", "name": "Horse Deals", "state": "National", "matches": []},
]

BY_SITE = {t["site"]: t for t in TITLES}

# Where no masthead is chosen or matched. The network, named as itself.
NETWORK = {"site": "", "name": "Australian Community Media", "state": "", "matches": []}

STATE_ORDER = ["NSW", "ACT", "VIC", "TAS", "QLD", "WA", "NT", "SA", "NSW/VIC", "National"]


def get(site: str | None) -> dict[str, Any]:
    """The masthead for a site key, or the network if it is unknown or unset."""
    return BY_SITE.get((site or "").strip(), NETWORK)


def name_for(site: str | None) -> str:
    """How this masthead is written into an email."""
    return get(site)["name"]


def match(location: str | None, region: str | None = None) -> str:
    """
    The masthead that best fits a typed location, as a site key.

    Longest keyword first, so "port macquarie" is not beaten by "port
    stephens" sharing the word "port", and a suburb always wins over the
    region it sits in. Returns "" when nothing fits, which leaves the pitch
    with the network rather than guessing a town's paper wrong.
    """
    haystack = " ".join(x for x in (location, region) if x).lower()
    if not haystack.strip():
        return ""

    best_site, best_len = "", 0
    for title in TITLES:
        for keyword in title["matches"]:
            if len(keyword) > best_len and keyword in haystack:
                best_site, best_len = title["site"], len(keyword)
    return best_site


# Where a masthead's first match keyword is not the place you would type into
# a search. Everything else derives its home town from matches[0], which is
# the main town in all but these.
HOME_OVERRIDES = {
    "illawarramercury.com.au": "Wollongong NSW",
    "bordermail.com.au": "Albury NSW",          # the state field reads NSW/VIC
    "dailyadvertiser.com.au": "Wagga Wagga NSW",
    "greatlakesadvocate.com.au": "Forster NSW",
}


def home_location(site: str | None) -> str:
    """
    The place to search when working this masthead's patch.

    Empty for the national and rural titles: they are chosen for the kind of
    business rather than the town it sits in, so there is no patch to sweep.
    """
    title = BY_SITE.get((site or "").strip())
    if not title or not title["matches"]:
        return ""
    if title["site"] in HOME_OVERRIDES:
        return HOME_OVERRIDES[title["site"]]
    state = title["state"] if title["state"] not in ("National", "NSW/VIC") else ""
    return " ".join(x for x in (title["matches"][0].title(), state) if x)


def with_a_patch() -> list[dict[str, Any]]:
    """The mastheads a territory run can sweep — those with a home town."""
    return [t for t in TITLES if home_location(t["site"])]


def options() -> list[dict[str, Any]]:
    """Every masthead, grouped by state, for a picker."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for title in TITLES:
        groups.setdefault(title["state"], []).append(title)
    ordered = []
    for state in STATE_ORDER:
        if state in groups:
            ordered.append({"state": state,
                            "titles": sorted(groups[state], key=lambda t: t["name"].lower())})
    for state in sorted(groups):          # anything the order above misses
        if state not in STATE_ORDER:
            ordered.append({"state": state,
                            "titles": sorted(groups[state], key=lambda t: t["name"].lower())})
    return ordered
