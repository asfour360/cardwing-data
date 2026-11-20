#!/usr/bin/env python3
"""Check the four data files the Cardwing app downloads, before every "push data".

Run from anywhere:  python3 scripts/validate.py [--app-repo PATH] [--base REF] [--data-dir PATH]

Prints ERROR lines (the app would ignore or drop something, or a Free/Pro rule is broken) and
WARN lines (a person should look). Exits 1 when there is at least one ERROR, else 0.

Python 3 standard library only. The rules mirror the app's Swift code (paths below are in the
app repo). When the app repo is found (--app-repo, default ../stackjet-app next to this repo),
the lists (enums, URL allowlists, struct fields, built-in tiers) are read from the Swift source
and the bundled data; otherwise the copies in this file are used and the checks that need
bundled data are skipped with a WARN.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from urllib.parse import urlsplit, unquote

FILES = {
    "cards": "cards/corrections.json",
    "lounges": "lounges/corrections.json",
    "status": "status/benefits.json",
    "app": "app/data.json",
}
FORMATS = {  # the `format` id each file must have (Sources/CardwingCore/*.swift `static let format`)
    "cards": "cardwing-card-corrections",      # CardCorrections.swift
    "lounges": "cardwing-lounge-corrections",  # LoungeCorrections.swift
    "status": "cardwing-status-benefits",      # StatusBenefits.swift
    "app": "cardwing-app-data",                # RemoteAppData.swift
}
MAX_BYTES = {"cards": 5_000_000, "lounges": 2_000_000, "status": 2_000_000, "app": 2_000_000}
MAX_AMOUNT_MINOR = 10_000_000_000  # Catalog.maxAmountMinor, Models.swift

# ---------------------------------------------------------------------------------------------
# Built-in copies of the app's lists. Used when the app repo is not there (e.g. GitHub Actions).
# When it is there, the same lists are read from the Swift source and a WARN says if these differ.
# ---------------------------------------------------------------------------------------------

# Models.swift `enum Category`, `enum ResetRule`, `enum Market`, `enum LoungeProvider`;
# StatusBenefits.swift `enum Topic`; RemoteAppData.swift `enum Feature`.
ENUMS = {
    "Category": [
        "flightsDirect", "flightsPortal", "hotelsDirect", "hotelsPortal", "dining", "groceries",
        "onlineGroceries", "gas", "evCharging", "transit", "drugstores", "streaming", "entertainment",
        "utilities", "redCross", "americanAirlines", "carRentalsPortal", "attractionsPortal", "citiNightsDining",
        "telecommunications", "cableSatellite", "carRentals", "attPurchases", "selfSelect",
        "recurringPayments", "foodDelivery", "digitalGaming", "digitalMedia", "officeSupplies", "shipping",
        "professionalServices", "homeImprovement", "wholesaleClubs", "alcohol", "foreignCurrency", "onlineShopping",
        "memberships", "fitness", "furniture", "porter", "airCanada", "westJet", "britishAirways", "cathayPacific", "hyatt",
        "airFranceKLM", "usdPurchases", "costcoOnline", "costcoBusinessOnline", "insuranceServices",
        "starAlliance", "marriottPurchases", "amazonWholeFoods", "sobeysGroup", "homeHardware", "cineplex",
        "triangleStores", "loblawsGroup", "shoppersDrugMart", "joeFresh", "costcoGas", "costcoPurchases", "everything",
    ],
    "ResetRule": ["monthly", "billingMonth", "quarterly", "semiannual", "aeroplanWallet", "calendarYear",
                  "anniversary", "oneTime", "none"],
    "Market": ["US", "CAN"],
    "LoungeProvider": [
        "American Express", "Priority Pass", "DragonPass", "Plaza Premium", "Escape Lounges",
        "Executive Lounges by Swissport", "Capital One Lounges", "Chase Sapphire Lounge", "Delta Sky Club",
        "Air Canada Maple Leaf Lounge", "United Club", "Admirals Club", "Alaska Lounge", "Qantas Club", "ANA Lounges",
    ],
    "Topic": ["earn", "upgrades", "seats", "bags", "lounge", "stay", "partners", "other"],
    "Feature": ["loungePair", "amexDirectory", "amexWaitlist", "weather", "issuerPages"],
}
ENUM_SOURCES = {  # where each enum lives: (file, enum declaration name)
    "Category": ("Sources/CardwingCore/Models.swift", "Category"),
    "ResetRule": ("Sources/CardwingCore/Models.swift", "ResetRule"),
    "Market": ("Sources/CardwingCore/Models.swift", "Market"),
    "LoungeProvider": ("Sources/CardwingCore/Models.swift", "LoungeProvider"),
    "Topic": ("Sources/CardwingCore/StatusBenefits.swift", "Topic"),
    "Feature": ("Sources/CardwingCore/RemoteAppData.swift", "Feature"),
}
# String fields with a fixed set of values. Benefit.kind and LoyaltyProgram.kind are plain
# Strings in Models.swift; these are the values the code reads (`kind == "protection"`,
# `"credit"`, `"cashback"`, `"carRental"`...) and the bundled catalog.json uses.
BENEFIT_KINDS = {"benefit", "protection", "credit"}
PROGRAM_KINDS = {"bank", "airline", "hotel", "cashback", "carRental", "other", "travel", "cruise"}
REWARD_KINDS = {"points", "cashback"}  # Catalog.validate(): anything else drops the whole fix set
TRACKABLE_STATUS_PERIODS = {"monthly", "quarterly", "semiannual", "calendarYear"}  # StatusBenefits.swift

# RemoteAppData.swift `enum RemoteURLPolicy` (lists at ~lines 932-1017).
URL_LISTS = {
    "trackerDomains": ["princeoftravel.com", "thepointsguy.com", "frequentmiler.com"],
    "issuerDomains": [
        "americanexpress.com", "apple.com", "avionrewards.com", "bankofamerica.com", "barclaycardus.com", "bilt.com", "biltrewards.com",
        "bluerewards.ca", "bmo.com", "bmorewards.com", "brimfinancial.com", "capitalone.ca", "capitalone.com", "chase.com",
        "cibc.com", "cibcrewards.com", "citi.com", "desjardins.com", "discover.com", "gm.com", "hometrust.ca",
        "laurentianbank.ca", "mbna.ca", "meridiancu.ca", "nbc.ca", "neofinancial.com", "pcoptimum.ca", "rbc.com",
        "rbcroyalbank.com", "rogersbank.com", "sceneplus.ca", "scotiabank.com", "simplii.com", "tangerine.ca", "td.com",
        "tdrewards.com", "thankyou.com", "usbank.com", "wealthsimple.com", "wellsfargo.com",
    ],
    "referralDomains_extra": [
        "americanexpress.com", "amex.ca", "chase.com", "referyourchasecard.com", "rbc.com", "rbcroyalbank.com", "td.com",
        "cibc.com", "scotiabank.com", "bmo.com", "nbc.ca", "capitalone.com", "capitalone.ca", "citi.com", "wellsfargo.com",
        "desjardins.com", "mbna.ca", "tangerine.ca", "neofinancial.com", "bilt.com",
    ],
    "brandDomains_extra": [
        "aa.com", "accor.com", "admtl.com", "aerlingus.com", "aexp-static.com", "aircanada.com", "airfrance.ca", "alaskaair.com",
        "amazon.ca", "ana.co.jp", "atmosrewards.com", "avis.com", "bestwestern.com", "britishairways.com", "businessinsider.com",
        "canadiantire.ca", "cathaypacific.com", "chasebenefits.com", "chasecdn.com", "choicehotels.com", "creditcardgenius.ca",
        "ctfs.com", "delta.com", "disneyrewards.com", "doordash.com", "ekstatic.net", "emirates.com", "enterprise.com", "etihadguest.com",
        "europcar.co.uk", "europcar.com", "expedia.com",
        "finnair.com", "flybreeze.com", "flyfrontier.com", "flyingblue.ca", "flyingblue.com", "flyingblue.us", "flyporter.com", "gigsky.com",
        "hertz.com", "hilton.com", "hyatt.com", "iberia.com", "ihg.com", "instacart.com", "jetblue.com", "lhw.com",
        "loganair.co.uk", "marriott.com", "mastercard.com", "morganstanley.com", "nationalcar.com", "neo.cc", "omnihotels.com",
        "pcfinancial.ca", "prioritypass.com", "qantas.com", "qatarairways.com", "radissonhotels.com", "schwab.com",
        "singaporeair.com", "sixt.com", "southwest.com", "thecenturionlounge.com", "united.com", "upgradedpoints.com", "virginatlantic.com",
        "visa.com", "visaairportcompanion.ca", "vueling.com", "westjet.com", "wyndhamhotels.com", "wyndhamrewardscreditcard.com",
    ],
    "statusMatchDomains": [
        "accor.com", "bestwestern.com", "choicehotels.com", "hilton.com", "hyatt.com", "ihg.com", "lhw.com",
        "marriott.com", "omnihotels.com", "radissonhotels.com", "sonesta.com", "worldhotels.com", "wyndhamhotels.com",
        "aa.com", "aircanada.com", "alaskaair.com", "atmosrewards.com", "britishairways.com", "cathaypacific.com",
        "delta.com", "emirates.com", "etihadguest.com", "flyingblue.com", "flyporter.com", "iberia.com", "jetblue.com",
        "qantas.com", "qatarairways.com", "singaporeair.com", "southwest.com", "united.com", "virginatlantic.com",
        "westjet.com",
        "avis.com", "enterprise.com", "europcar.co.uk", "europcar.com", "hertz.com", "nationalcar.ca", "nationalcar.com", "sixt.com",
    ],
    "cardHosts": [
        "americanexpress.mediaroom.com", "assets.ctfassets.net", "cdn.prodstatic.com", "images.ctfassets.net",
        "flyfrontier-com-frontdoor-en-chh3dufqdpdff8h9.a02.azurefd.net", "kqphqqvkrdovtyhdapud.supabase.co",
    ],
    "loungeDomains": [
        "aa.com", "aexp-static.com", "aircanada.com", "alaskaair.com", "americanexpress.com", "ana.co.jp", "capitalone.com",
        "capitalonetravel.com", "chase.com", "delta.com", "dragonpass.com", "escapelounges.com", "heathrow.com",
        "hongkongairport.com", "narita-airport.jp", "plazapremiumlounge.com", "prioritypass.com", "qantas.com",
        "swissport.com", "thecenturionlounge.com", "united.com",
    ],
    "loungeHosts": [
        "united.mediaroom.com", "d3gpl2rcbhkqw8.cloudfront.net", "images.contentstack.io", "images.ctfassets.net",
        "runway-media-production.global.ssl.fastly.net", "s202.q4cdn.com", "a.storyblok.com", "mma.prnewswire.com",
        "asfour360.github.io", "content.presspage.com", "yow-website.transforms.svdcdn.com",
    ],
}
HOST_PATH_PREFIXES = {"asfour360.github.io": "/cardwing-photos/"}  # RemoteURLPolicy.hostPathPrefixes

# StatusTierCatalog.swift `builtInTiers` (lowest first). app/data.json `statusTiers` replaces a program's list.
BUILTIN_TIERS = {
    "delta": ["Silver Medallion", "Gold Medallion", "Platinum Medallion", "Diamond Medallion"],
    "united": ["Premier Silver", "Premier Gold", "Premier Platinum", "Premier 1K"],
    "american": ["AAdvantage Gold", "AAdvantage Platinum", "AAdvantage Platinum Pro", "AAdvantage Executive Platinum"],
    "aeroplan": ["Aeroplan 25K", "Aeroplan 35K", "Aeroplan 50K", "Aeroplan 75K", "Aeroplan Super Elite"],
    "flying-blue": ["Flying Blue Silver", "Flying Blue Gold", "Flying Blue Platinum", "Flying Blue Ultimate"],
    "southwest": ["A-List", "A-List Preferred"],
    "jetblue": ["Mosaic 1", "Mosaic 2", "Mosaic 3", "Mosaic 4"],
    "frontier": ["Elite Silver", "Elite Gold", "Elite Platinum", "Elite Diamond"],
    "hilton": ["Hilton Honors Silver", "Hilton Honors Gold", "Hilton Honors Diamond", "Hilton Honors Diamond Reserve"],
    "marriott": ["Marriott Bonvoy Silver Elite", "Marriott Bonvoy Gold Elite", "Marriott Bonvoy Platinum Elite",
                 "Marriott Bonvoy Titanium Elite", "Marriott Bonvoy Ambassador Elite"],
    "hyatt": ["World of Hyatt Discoverist", "World of Hyatt Explorist", "World of Hyatt Globalist"],
    "ihg": ["IHG Silver Elite", "IHG Gold Elite", "IHG Platinum Elite", "IHG Diamond Elite"],
    "choice": ["Choice Privileges Gold", "Choice Privileges Platinum", "Choice Privileges Diamond", "Choice Privileges Titanium"],
    "wyndham": ["Wyndham Rewards Gold", "Wyndham Rewards Platinum", "Wyndham Rewards Diamond"],
    "hertz": ["Hertz Five Star", "Hertz President's Circle"],
    "avis": ["Avis Preferred Plus", "Avis President's Club"],
    "national": ["National Executive", "National Executive Elite"],
    "sixt": ["SIXT ONE Silver", "SIXT ONE Gold", "SIXT ONE Platinum", "SIXT ONE Diamond"],
    "enterprise": ["Enterprise Plus Silver", "Enterprise Plus Gold", "Enterprise Plus Platinum"],
    "europcar": ["Privilege Club", "Privilege Executive", "Privilege Elite", "Privilege Elite VIP"],
    "westjet": ["WestJet Silver", "WestJet Gold", "WestJet Platinum"],
    "porter": ["VIPorter Passport", "VIPorter Venture", "VIPorter Ascent", "VIPorter First"],
    "ba-avios": ["British Airways Bronze", "British Airways Silver", "British Airways Gold", "British Airways Gold Guest List"],
    "iberia-avios": ["Iberia Plata", "Iberia Oro", "Iberia Platino", "Iberia Platino Prime", "Iberia Infinita", "Iberia Infinita Prime"],
    "aerclub-avios": ["AerClub Silver", "AerClub Platinum", "AerClub Concierge"],
    "cathay": ["Cathay Silver", "Cathay Gold", "Cathay Diamond"],
    "atmos": ["Atmos Silver", "Atmos Gold", "Atmos Platinum", "Atmos Titanium"],
    "breeze": ["Breezy Rewards Member", "Breezy 1", "Breezy 2", "Breezy 3", "Breezy Club"],
    "accor": ["ALL Accor Silver", "ALL Accor Gold", "ALL Accor Platinum", "ALL Accor Diamond", "ALL Accor Limitless"],
}

# Decoded struct fields: name -> (Swift type, optional). Mirrors Models.swift, LoungeCatalog.swift,
# LoungeCorrections.swift, LoungeRatings.swift. A missing non-optional field, or a wrong type,
# makes the app skip that card / program / lounge entry without a word.
STRUCT_SOURCES = {
    "CardDefinition": "Sources/CardwingCore/Models.swift",
    "Benefit": "Sources/CardwingCore/Models.swift",
    "StatusGrant": "Sources/CardwingCore/Models.swift",
    "EarningRule": "Sources/CardwingCore/Models.swift",
    "LoungeAccessGrant": "Sources/CardwingCore/Models.swift",
    "LoyaltyProgram": "Sources/CardwingCore/Models.swift",
    "LoungeCatalogEntry": "Sources/CardwingCore/LoungeCatalog.swift",
    "Target": "Sources/CardwingCore/LoungeCorrections.swift",
    "Change": "Sources/CardwingCore/LoungeCorrections.swift",
    "LoungeRating": "Sources/CardwingCore/LoungeRatings.swift",
}
STRUCTS = {
    "CardDefinition": {
        "id": ("String", False), "name": ("String", False), "issuer": ("String", False), "market": ("Market", False),
        "annualFeeMinor": ("Int", True), "foreignTransactionFeePercent": ("Double", True), "feeNote": ("String", False),
        "upcomingFeeNote": ("String", True), "colorHex": ("String", False), "secondaryHex": ("String", False),
        "artworkAsset": ("String", True), "programIDs": ("[String]", False), "sourceURL": ("String", False),
        "checkedOn": ("String", False), "coverageNote": ("String", False), "benefits": ("[Benefit]", False),
        "statuses": ("[StatusGrant]", False), "earningRules": ("[EarningRule]", False),
        "earningDataComplete": ("Bool", True), "accountType": ("String", True), "artworkURL": ("String", True),
        "artworkPageURL": ("String", True), "artworkSearchTerms": ("String", True), "reviewURL": ("String", True),
        "catalogLevel": ("String", True), "availability": ("String", True),
        "loungeAccess": ("[LoungeAccessGrant]", True), "benefitSourceURLs": ("[String]", True),
    },
    "Benefit": {
        "id": ("String", False), "title": ("String", False), "detail": ("String", False), "capMinor": ("Int", True),
        "reset": ("ResetRule", False), "enrollmentRequired": ("Bool", False), "sourceURL": ("String", False),
        "startsOn": ("String", True), "endsBefore": ("String", True), "monthOverrides": ("[String: Int]", True),
        "intervalYears": ("Int", True), "reminderOnPeriodStart": ("Bool", True), "selectionLabel": ("String", True),
        "selectionOptions": ("[String]", True), "kind": ("String", True), "priority": ("Int", True),
        "spendMilestoneMinor": ("Int", True), "spendPeriod": ("ResetRule", True), "valueMinor": ("Int", True),
        "valueNote": ("String", True), "passCount": ("Int", True),
    },
    "StatusGrant": {
        "id": ("String", False), "programID": ("String", False), "tier": ("String", False), "rank": ("Int", False),
        "enrollmentRequired": ("Bool", False), "condition": ("String", False), "automatic": ("Bool", False),
        "sourceURL": ("String", False),
    },
    "EarningRule": {
        "id": ("String", False), "categories": ("[Category]", False), "rate": ("Double", False),
        "rewardKind": ("String", False), "programID": ("String", False), "label": ("String", False),
        "detail": ("String", False), "countries": ("[String]", False), "startsOn": ("String", True),
        "endsBefore": ("String", True), "activationID": ("String", True), "capGroup": ("String", True),
        "capMinor": ("Int", True), "capReset": ("ResetRule", False), "sourceURL": ("String", False),
        "calculationNote": ("String", True),
    },
    "LoungeAccessGrant": {
        "id": ("String", False), "provider": ("LoungeProvider", False), "title": ("String", False),
        "detail": ("String", False), "enrollmentRequired": ("Bool", False), "sourceURL": ("String", False),
        "startsOn": ("String", True), "finderURLTemplate": ("String", True),
    },
    "LoyaltyProgram": {
        "id": ("String", False), "name": ("String", False), "region": ("String", False), "kind": ("String", False),
        "unit": ("String", False), "sourceURL": ("String", False), "logoURL": ("String", True),
        "logoAsset": ("String", True), "logoPageURL": ("String", True), "logoSearchTerms": ("String", True),
        "airlineCodes": ("[String]", True),
    },
    "LoungeCatalogEntry": {
        "airportCode": ("String", False), "provider": ("LoungeProvider", False), "name": ("String", False),
        "officialURL": ("String", False), "description": ("String", False), "terminal": ("String", True),
        "location": ("String", True), "hours": ("[String]", False), "amenities": ("[String]", False),
        "restrictions": ("[String]", False), "photoURLs": ("[String]", False),
    },
    "Target": {"airport": ("String", False), "provider": ("LoungeProvider", False), "name": ("String", False)},
    "Change": {
        "airport": ("String", False), "provider": ("LoungeProvider", False), "name": ("String", False),
        "newName": ("String", True), "terminal": ("String", True), "location": ("String", True),
        "hours": ("[String]", True), "officialURL": ("String", True), "description": ("String", True),
        "restrictions": ("[String]", True), "amenities": ("[String]", True), "photoURLs": ("[String]", True),
    },
    "LoungeRating": {
        "airport": ("String", False), "provider": ("LoungeProvider", False), "name": ("String", False),
        "terminal": ("String", True), "rating": ("Double", False), "reviewCount": ("Int", True),
        "source": ("String", False), "sourceURL": ("String", True), "checked": ("String", False),
    },
}

# Free/Pro guard: keys that would let data decide who is Pro. None may appear in any file
# (free-vs-pro-gating.md item 5). Only exception: profile.badges[].proOnly == true (can only add a lock).
GATING_WORDS = {"pro", "premium", "paid", "paywall", "entitlement", "entitlements", "locked", "lock", "unlock",
                "unlocked", "plan", "plans", "free", "subscription", "subscriber"}


def gating_words(key):
    """The words of a field-like key ("isPro" -> is, pro; "pro_only" -> pro, only). Keys that are
    data, not field names (card ids like "freedom-flex-us", English text) have '-' or spaces: skipped."""
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key):
        return set()
    words = re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", key.replace("_", " "))
    return {w.lower() for w in words} & GATING_WORDS


# ---------------------------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------------------------

class Report:
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.infos = []

    def error(self, where, msg):
        self.errors.append(f"ERROR {where}: {msg}")

    def warn(self, where, msg):
        self.warnings.append(f"WARN  {where}: {msg}")

    def info(self, where, msg):
        self.infos.append(f"INFO  {where}: {msg}")


# ---------------------------------------------------------------------------------------------
# Reading the app repo (optional)
# ---------------------------------------------------------------------------------------------

def strip_line_comments(text):
    return re.sub(r"//[^\n]*", "", text)


def swift_block(text, header_re):
    """The text of the `{ ... }` block that follows the first match of header_re, or None."""
    m = re.search(header_re, text, re.MULTILINE)
    if not m:
        return None
    start = text.find("{", m.end() - 1)
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
    return None


def swift_enum_cases(text, name):
    block = swift_block(text, r"^\s*enum " + re.escape(name) + r"\b[^{\n]*\{")
    if block is None:
        return None
    lines = block.split("\n")
    indent = None
    for line in lines:
        if line.strip():
            indent = len(line) - len(line.lstrip())
            break
    out = []
    for line in lines:
        if not line.strip() or len(line) - len(line.lstrip()) != indent:
            continue
        s = strip_line_comments(line).strip()
        if not s.startswith("case "):
            continue
        for part in s[5:].split(","):
            part = part.strip()
            if not part:
                continue
            m = re.match(r'(\w+)\s*(?:=\s*"([^"]*)")?$', part)
            if m:
                out.append(m.group(2) if m.group(2) is not None else m.group(1))
    return out


def swift_struct_fields(text, name):
    block = swift_block(text, r"^\s*struct " + re.escape(name) + r"\b[^{\n]*\{")
    if block is None:
        return None
    lines = block.split("\n")
    indent = None
    for line in lines:
        if line.strip() and not line.strip().startswith("//"):
            indent = len(line) - len(line.lstrip())
            break
    fields = {}
    for line in lines:
        if not line.strip() or len(line) - len(line.lstrip()) != indent:
            continue
        s = strip_line_comments(line).strip()
        m = re.match(r"(?:private\(set\)\s+)?(?:var|let)\s+(\w+)\s*:\s*([^={]+?)\s*(=.*)?$", s)
        if not m or s.endswith("{"):
            continue
        typ = m.group(2).strip()
        optional = typ.endswith("?")
        fields[m.group(1)] = (typ.rstrip("?").strip(), optional)
    return fields


def swift_string_set(text, name, known_names):
    """Strings and referenced lists in `static let <name>: Set<String> = ...` (RemoteURLPolicy)."""
    m = re.search(r"static let " + re.escape(name) + r"\s*:\s*Set<String>\s*=", text)
    if not m:
        return None
    rest = text[m.end():]
    end = re.search(r"\n\s*(///|static |// MARK|\}\n)", rest)
    expr = strip_line_comments(rest[:end.start()] if end else rest)
    strings = re.findall(r'"([^"]+)"', expr)
    refs = [r for r in re.findall(r"\b(\w+Domains|\w+Hosts)\b", expr) if r in known_names and r != name]
    return strings, refs


class AppRepo:
    """What the validator can learn from the app repo. Missing pieces stay None."""

    def __init__(self, path, report):
        self.path = path
        self.report = report
        self.catalog = None
        self.status_benefits = None
        self.french = None
        self.lounge_airports = None
        self.ok = bool(path) and os.path.isdir(os.path.join(path, "Sources", "CardwingCore"))
        if not self.ok:
            return
        self.catalog = self._json("Resources/catalog.json")
        self.status_benefits = self._json("Resources/status-benefits.json")
        self.french = self._json("Resources/catalog-fr.json")
        lounges = self._json("Resources/lounges.json")
        if isinstance(lounges, dict):
            self.lounge_airports = {e.get("airportCode") for e in lounges.get("lounges", []) if isinstance(e, dict)}
        svc = self._text("App/LoungeService.swift") or ""
        m = re.search(r"top75NorthAmericaCodes[^=]*=\s*\[(.*?)\]", svc, re.S)
        if m and self.lounge_airports is not None:
            self.lounge_airports |= set(re.findall(r'"([A-Z]{3})"', m.group(1)))
        self._sync_lists()

    def _text(self, rel):
        try:
            with open(os.path.join(self.path, rel), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None

    def _json(self, rel):
        t = self._text(rel)
        if t is None:
            self.report.warn("app repo", f"{rel} not found; checks that need it are skipped")
            return None
        try:
            return json.loads(t)
        except ValueError as e:
            self.report.warn("app repo", f"{rel} does not parse ({e}); checks that need it are skipped")
            return None

    def _sync_lists(self):
        """Read enums, structs, URL lists and tiers from Swift; use them if they differ from ours."""
        stale = []
        for key, (rel, name) in ENUM_SOURCES.items():
            t = self._text(rel)
            cases = swift_enum_cases(t, name) if t else None
            if not cases:
                self.report.warn("app repo", f"could not read enum {name} from {rel}; using the copy in validate.py")
                continue
            if set(cases) != set(ENUMS[key]):
                stale.append(f"enum {name}")
                ENUMS[key] = cases
        for name, rel in STRUCT_SOURCES.items():
            t = self._text(rel)
            fields = swift_struct_fields(t, name) if t else None
            if not fields:
                self.report.warn("app repo", f"could not read struct {name} from {rel}; using the copy in validate.py")
                continue
            if fields != STRUCTS[name]:
                stale.append(f"struct {name}")
                STRUCTS[name] = fields
        rel = "Sources/CardwingCore/RemoteAppData.swift"
        t = self._text(rel)
        if t:
            names = ["trackerDomains", "issuerDomains", "referralDomains", "brandDomains", "statusMatchDomains",
                     "cardHosts", "loungeDomains", "loungeHosts"]
            raw = {}
            for n in names:
                got = swift_string_set(t, n, set(names))
                if got is None:
                    self.report.warn("app repo", f"could not read {n} from {rel}; using the copy in validate.py")
                    raw = None
                    break
                raw[n] = got
            if raw:
                def resolve(n, seen=()):
                    strings, refs = raw[n]
                    out = set(strings)
                    for r in refs:
                        if r not in seen:
                            out |= resolve(r, seen + (n,))
                    return out
                ours = build_url_sets()
                theirs = {n: resolve(n) for n in names}
                if any(ours[n] != theirs[n] for n in names):
                    stale.append("URL allowlists")
                    URL_SETS.clear()
                    URL_SETS.update(theirs)
            m = re.search(r'hostPathPrefixes[^=]*=\s*\[(.*?)\]', t, re.S)
            if m:
                pairs = dict(re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', m.group(1)))
                if pairs and pairs != HOST_PATH_PREFIXES:
                    stale.append("hostPathPrefixes")
                    HOST_PATH_PREFIXES.clear()
                    HOST_PATH_PREFIXES.update(pairs)
        rel = "Sources/CardwingCore/StatusTierCatalog.swift"
        t = self._text(rel)
        block = re.search(r"builtInTiers[^=]*=\s*\[(.*?)\n    \]", t or "", re.S)
        if block:
            tiers = {}
            for m in re.finditer(r'"([^"]+)"\s*:\s*\[([^\]]*)\]', block.group(1)):
                tiers[m.group(1)] = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(2))
            if tiers and tiers != BUILTIN_TIERS:
                stale.append("built-in status tiers")
                BUILTIN_TIERS.clear()
                BUILTIN_TIERS.update(tiers)
        else:
            self.report.warn("app repo", f"could not read builtInTiers from {rel}; using the copy in validate.py")
        if self.catalog:
            # Benefit and program kinds the bundled card list uses are known values too.
            for c in self.catalog.get("cards", []):
                for b in c.get("benefits", []):
                    if isinstance(b.get("kind"), str):
                        BENEFIT_KINDS.add(b["kind"])
            for p in self.catalog.get("programs", []):
                if isinstance(p.get("kind"), str):
                    PROGRAM_KINDS.add(p["kind"])
        if stale:
            self.report.warn("validate.py", "its built-in copy differs from the app repo for: " + ", ".join(stale)
                             + ". Using the app's values this run; update the copy in scripts/validate.py so CI matches.")


def build_url_sets():
    L = URL_LISTS
    issuer = set(L["issuerDomains"])
    tracker = set(L["trackerDomains"])
    return {
        "trackerDomains": tracker,
        "issuerDomains": issuer,
        "referralDomains": issuer | set(L["referralDomains_extra"]),
        "brandDomains": issuer | tracker | set(L["brandDomains_extra"]),
        "statusMatchDomains": set(L["statusMatchDomains"]),
        "cardHosts": set(L["cardHosts"]),
        "loungeDomains": set(L["loungeDomains"]),
        "loungeHosts": set(L["loungeHosts"]),
    }


URL_SETS = build_url_sets()


def purpose_lists(purpose):
    """RemoteURLPolicy.lists(_:) — (domains, hosts) per purpose."""
    S = URL_SETS
    return {
        "loungeLink": (S["loungeDomains"], S["loungeHosts"]),
        "loungePhoto": (S["loungeDomains"], S["loungeHosts"]),
        "cardLink": (S["brandDomains"], S["cardHosts"]),
        "statusBenefitSource": (S["brandDomains"], set()),
        "bestUseSource": (S["brandDomains"], set()),
        "transferBonusSource": (S["trackerDomains"] | S["issuerDomains"], set()),
        "referral": (S["referralDomains"], set()),
        "statusMatchPage": (S["statusMatchDomains"], set()),
        "pageLink": (S["brandDomains"] | {"flytrippers.com"}, S["cardHosts"]),
    }[purpose]


def url_problem(text, purpose):
    """None when the link passes safeRemoteURL(_:purpose:) (RemoteAppData.swift), else why not."""
    if not isinstance(text, str):
        return "not a string"
    if text != text.strip() or any(ord(ch) <= 32 for ch in text):
        return "has spaces"
    try:
        u = urlsplit(text)
    except ValueError:
        return "not a valid URL"
    if u.scheme.lower() != "https":
        return "not HTTPS"
    if not u.hostname:
        return "no host"
    if u.username or u.password:
        return "has a user name or password"
    try:
        port = u.port
    except ValueError:
        return "bad port"
    if port not in (None, 443):
        return f"port {port}"
    host = u.hostname.lower()
    if not host.isascii():
        return "non-ASCII host"
    domains, hosts = purpose_lists(purpose)
    if not (host in hosts or any(host == d or host.endswith("." + d) for d in domains)):
        return f"host {host} is not on the {purpose} allowlist"
    prefix = HOST_PATH_PREFIXES.get(host)
    if prefix:
        path = u.path
        decoded = unquote(path)
        if not path.startswith(prefix) or not decoded.startswith(prefix) or "\\" in decoded or \
                ".." in path.split("/") or ".." in decoded.split("/"):
            return f"on {host} only paths under {prefix} are allowed"
    return None


# ---------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------

def is_date(s):
    if not isinstance(s, str) or len(s) != 10:
        return False
    try:
        datetime.date.fromisoformat(s)
        return True
    except ValueError:
        return False


def blank(s):
    return not isinstance(s, str) or not s.strip()


def version_tuple(v):
    parts = []
    for p in str(v).split("."):
        m = re.match(r"\d+", p)
        parts.append(int(m.group()) if m else 0)
    return tuple(parts)


def norm_words(s):
    return " ".join(re.sub(r"[^a-z0-9+]+", " ", str(s).lower()).split())


def tier_matches(a, b):
    """Same rule as StatusBenefits.Program.tierIndex: equal, or one ends/starts with the other on a word."""
    a, b = norm_words(a), norm_words(b)
    if not a or not b:
        return False
    return a == b or a.endswith(" " + b) or b.endswith(" " + a) or a.startswith(b + " ") or b.startswith(a + " ")


def tier_index(tiers, name):
    """Index of `name` in `tiers` (exact first, else one unique word match), or None."""
    n = norm_words(name)
    for i, t in enumerate(tiers):
        if norm_words(t) == n:
            return i
    hits = [i for i, t in enumerate(tiers) if tier_matches(t, name)]
    return hits[0] if len(hits) == 1 else None


def type_ok(value, swift_type):
    """Does a JSON value decode as this Swift type? Returns (ok, why)."""
    t = swift_type.strip()
    if t.endswith("?"):
        if value is None:
            return True, ""
        t = t[:-1]
    if t == "String":
        return isinstance(value, str), "expected a string"
    if t == "Int":
        return (isinstance(value, int) and not isinstance(value, bool)), "expected a whole number"
    if t == "Double":
        return (isinstance(value, (int, float)) and not isinstance(value, bool)), "expected a number"
    if t == "Bool":
        return isinstance(value, bool), "expected true or false"
    if t in ENUMS:
        if not isinstance(value, str):
            return False, f"expected a {t} string"
        return value in ENUMS[t], f"unknown {t} value {value!r}"
    m = re.fullmatch(r"\[\s*(.+?)\s*:\s*(.+)\s*\]", t)
    if m:
        if not isinstance(value, dict):
            return False, "expected an object"
        for k, v in value.items():
            ok, why = type_ok(v, m.group(2))
            if not ok:
                return False, f"[{k!r}] {why}"
        return True, ""
    m = re.fullmatch(r"\[(.+)\]", t)
    if m:
        if not isinstance(value, list):
            return False, "expected a list"
        inner = m.group(1).strip()
        if inner in STRUCTS:
            return all(isinstance(v, dict) for v in value), "expected a list of objects"
        for i, v in enumerate(value):
            ok, why = type_ok(v, inner)
            if not ok:
                return False, f"[{i}] {why}"
        return True, ""
    if t in STRUCTS:
        return isinstance(value, dict), "expected an object"
    return True, ""  # a type this script doesn't model: not checked


def check_struct(obj, struct, where):
    """Problems that make the app skip this object (missing field, wrong type, unknown enum)."""
    problems = []
    if not isinstance(obj, dict):
        return [f"{where}: expected an object"]
    for field, (typ, optional) in STRUCTS[struct].items():
        if field not in obj or (obj[field] is None and not optional):
            if not optional:
                problems.append(f"{where}: missing required field `{field}`")
            continue
        ok, why = type_ok(obj[field], typ + ("?" if optional else ""))
        if not ok:
            problems.append(f"{where}.{field}: {why}")
            continue
        inner = re.fullmatch(r"\[(\w+)\]", typ)
        if inner and inner.group(1) in STRUCTS and isinstance(obj[field], list):
            for i, item in enumerate(obj[field]):
                problems += check_struct(item, inner.group(1), f"{where}.{field}[{i}]")
    return problems


def load_json_text(text, where, report):
    """Parse JSON; duplicate keys are an ERROR (which one wins is not defined)."""
    def hook(pairs):
        seen = {}
        for k, v in pairs:
            if k in seen:
                report.error(where, f"duplicate key {k!r} in one object")
            seen[k] = v
        return seen
    return json.loads(text, object_pairs_hook=hook)


def git_show(data_dir, ref, rel):
    try:
        out = subprocess.run(["git", "-C", data_dir, "show", f"{ref}:{rel}"], capture_output=True, check=True)
        return out.stdout.decode("utf-8")
    except (subprocess.CalledProcessError, OSError):
        return None


# ---------------------------------------------------------------------------------------------
# Checks common to every file
# ---------------------------------------------------------------------------------------------

def check_header(key, data, report, where):
    if data.get("format") != FORMATS[key]:
        report.error(where, f"`format` must be {FORMATS[key]!r} (is {data.get('format')!r}); the app ignores the whole file")
    if data.get("version") != 1 or isinstance(data.get("version"), bool):
        report.error(where, f"`version` must be 1 (is {data.get('version')!r}); the app ignores the whole file")
    if not is_date(data.get("updated")):
        report.error(where, f"`updated` must be a yyyy-MM-dd date (is {data.get('updated')!r}); the app ignores the whole file")
    elif data["updated"] > datetime.date.today().isoformat():
        report.warn(where, f"`updated` {data['updated']} is in the future")


def walk_keys(obj, path, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.append((path, k, v))
            walk_keys(v, f"{path}.{k}", out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            walk_keys(v, f"{path}[{i}]", out)


def check_gating_keys(key, data, report, where):
    """No key may decide Free vs Pro (free-vs-pro-gating.md item 5)."""
    found = []
    walk_keys(data, key, found)
    for path, k, v in found:
        if key == "cards" and path == "cards.french":
            continue  # English text -> French text, not field names
        if not gating_words(k):
            continue
        if k == "proOnly" and re.fullmatch(r"app\.profile\.badges\[\d+\]", path):
            if v is not True:
                report.error(where, f"{path}.proOnly is {json.dumps(v)}: only `true` is allowed (a data file may add a Pro lock, never remove one)")
            continue
        report.error(where, f"{path}: key `{k}` looks like a Free/Pro switch. Data files must never decide who is Pro; Pro checks live in app code")


# ---------------------------------------------------------------------------------------------
# cards/corrections.json
# ---------------------------------------------------------------------------------------------

CARD_LINK_FIELDS = ["sourceURL", "artworkURL", "artworkPageURL", "reviewURL"]
FR_FIELDS_CARD = ["feeNote", "coverageNote", "upcomingFeeNote"]
FR_FIELDS_BENEFIT = ["title", "detail", "valueNote"]


def check_link(value, purpose, report, where, allow_empty=True):
    if value is None or (allow_empty and value == ""):
        return
    why = url_problem(value, purpose)
    if why:
        report.error(where, f"link {value!r}: {why}; the app drops it")


def check_cards(data, app, report):
    W = FILES["cards"]
    cv = data.get("catalogVersion")
    if blank(cv):
        report.error(W, "`catalogVersion` is missing or empty; the app ignores the whole file")
    elif app.catalog:
        bundled = app.catalog.get("version")
        if version_tuple(cv) < version_tuple(bundled):
            report.error(W, f"`catalogVersion` {cv} is older than the app's bundled card list {bundled}; "
                            "that app build ignores the whole file. Set it to at least " + str(bundled))
        elif version_tuple(cv) > version_tuple(bundled):
            report.info(W, f"`catalogVersion` {cv} is newer than the app repo's bundled list {bundled} (fine: it applies to older builds)")
    else:
        report.warn(W, "app repo not found: `catalogVersion` not compared with the bundled card list")

    for k, t in [("cards", list), ("removeCards", list), ("programs", list), ("french", dict)]:
        if k in data and not isinstance(data[k], t):
            report.error(W, f"`{k}` has the wrong type; the app ignores the whole file")
    cards = data.get("cards") if isinstance(data.get("cards"), list) else []
    programs = data.get("programs") if isinstance(data.get("programs"), list) else []
    remove = data.get("removeCards") if isinstance(data.get("removeCards"), list) else []
    french = data.get("french") if isinstance(data.get("french"), dict) else {}
    if len(cards) + len(remove) + len(programs) > 1000 or len(french) > 20000:
        report.error(W, "too many entries (cards + removeCards + programs > 1,000 or french > 20,000); the app ignores the whole file")
    for i, r in enumerate(remove):
        if blank(r):
            report.error(W, f"removeCards[{i}] is empty; the app ignores the whole file")

    # Programs
    file_programs = {}
    for i, p in enumerate(programs):
        where = f"{W} programs[{i}]" + (f" ({p.get('id')})" if isinstance(p, dict) else "")
        for prob in check_struct(p, "LoyaltyProgram", "program"):
            report.error(where, prob + "; the app skips this program")
        if not isinstance(p, dict):
            continue
        if p.get("kind") not in PROGRAM_KINDS:
            report.error(where, f"unknown program kind {p.get('kind')!r} (known: {', '.join(sorted(PROGRAM_KINDS))})")
        for f in ["sourceURL", "logoURL", "logoPageURL"]:
            check_link(p.get(f), "cardLink", report, f"{where}.{f}")
        if p.get("id") in file_programs:
            report.error(where, f"duplicate program id {p.get('id')!r}; the whole fix set is dropped")
        file_programs[p.get("id")] = p

    bundled_programs = {}
    if app.catalog:
        bundled_programs = {p.get("id"): p for p in app.catalog.get("programs", [])}
    known_programs = set(bundled_programs) | set(file_programs)

    english_used = set()
    seen_ids = set()
    for i, c in enumerate(cards):
        cid = c.get("id") if isinstance(c, dict) else None
        where = f"{W} cards[{i}] ({cid})"
        for prob in check_struct(c, "CardDefinition", "card"):
            report.error(where, prob + "; the app skips this card")
        if not isinstance(c, dict):
            continue
        if cid in seen_ids:
            report.error(where, f"duplicate card id {cid!r}; the whole fix set is dropped")
        seen_ids.add(cid)
        if blank(cid) or blank(c.get("name")):
            report.error(where, "empty id or name; the whole fix set is dropped")
        fee = c.get("annualFeeMinor")
        if isinstance(fee, int) and not (0 <= fee <= MAX_AMOUNT_MINOR):
            report.error(where, f"annualFeeMinor {fee} out of range; the whole fix set is dropped")
        fx = c.get("foreignTransactionFeePercent")
        if isinstance(fx, (int, float)) and not (0 <= fx <= 10):
            report.error(where, f"foreignTransactionFeePercent {fx} out of 0-10; the whole fix set is dropped")
        if not is_date(c.get("checkedOn")):
            report.warn(where, f"checkedOn {c.get('checkedOn')!r} is not a yyyy-MM-dd date")
        for f in ("colorHex", "secondaryHex"):
            if isinstance(c.get(f), str) and not re.fullmatch(r"[0-9A-Fa-f]{6}", c[f]):
                report.warn(where, f"{f} {c[f]!r} is not 6 hex digits")
        for pid in c.get("programIDs") or []:
            if app.catalog and pid not in known_programs:
                report.error(where, f"programIDs has unknown program {pid!r}; the whole fix set is dropped")
        # Links
        for f in CARD_LINK_FIELDS:
            check_link(c.get(f), "cardLink", report, f"{where}.{f}")
        for j, u in enumerate(c.get("benefitSourceURLs") or []):
            check_link(u, "cardLink", report, f"{where}.benefitSourceURLs[{j}]")
        # Benefits
        bids = set()
        for j, b in enumerate(c.get("benefits") or []):
            if not isinstance(b, dict):
                continue
            bw = f"{where} benefit {b.get('id')}"
            if b.get("id") in bids:
                report.error(bw, "duplicate benefit id on this card; the whole fix set is dropped")
            bids.add(b.get("id"))
            kind = b.get("kind")
            if kind is not None and kind not in BENEFIT_KINDS:
                report.error(bw, f"unknown benefit kind {kind!r} (known: {', '.join(sorted(BENEFIT_KINDS))})")
            cap = b.get("capMinor")
            if kind == "credit" and not (isinstance(cap, int) and not isinstance(cap, bool) and cap > 0):
                report.error(bw, f"kind \"credit\" needs a positive capMinor (is {cap!r}); without it the app shows it as a perk, not a trackable credit")
            for f in ("capMinor", "valueMinor", "spendMilestoneMinor"):
                v = b.get(f)
                if isinstance(v, int) and not (0 <= v <= MAX_AMOUNT_MINOR):
                    report.error(bw, f"{f} {v} out of range; the whole fix set is dropped")
            mo = b.get("monthOverrides")
            if isinstance(mo, dict):
                for k, v in mo.items():
                    if not (k.isdigit() and 1 <= int(k) <= 12) or not isinstance(v, int) or not (0 <= v <= MAX_AMOUNT_MINOR):
                        report.error(bw, f"monthOverrides {k!r}: {v!r} is not month 1-12 with a valid amount; the whole fix set is dropped")
            s, e = b.get("startsOn"), b.get("endsBefore")
            for f, v in (("startsOn", s), ("endsBefore", e)):
                if v is not None and not is_date(v):
                    report.warn(bw, f"{f} {v!r} is not a yyyy-MM-dd date")
            if isinstance(s, str) and isinstance(e, str) and s >= e:
                report.error(bw, f"startsOn {s} is not before endsBefore {e}; the whole fix set is dropped")
            check_link(b.get("sourceURL"), "cardLink", report, f"{bw}.sourceURL")
            if c.get("market") == "CAN":
                for f in FR_FIELDS_BENEFIT:
                    if isinstance(b.get(f), str):
                        english_used.add(b[f])
        if c.get("market") == "CAN":
            for f in FR_FIELDS_CARD:
                if isinstance(c.get(f), str):
                    english_used.add(c[f])
        # Status grants
        sids = set()
        for j, s in enumerate(c.get("statuses") or []):
            if not isinstance(s, dict):
                continue
            sw = f"{where} status {s.get('id')}"
            if s.get("id") in sids:
                report.error(sw, "duplicate status id on this card; the whole fix set is dropped")
            sids.add(s.get("id"))
            if app.catalog and s.get("programID") not in known_programs:
                report.error(sw, f"unknown status program {s.get('programID')!r}; the whole fix set is dropped")
            check_link(s.get("sourceURL"), "cardLink", report, f"{sw}.sourceURL")
            check_grant_rank(s.get("programID"), s.get("tier"), s.get("rank"), 10, report, sw)
        # Earning rules
        eids = set()
        for j, r in enumerate(c.get("earningRules") or []):
            if not isinstance(r, dict):
                continue
            rw = f"{where} earn {r.get('id')}"
            if r.get("id") in eids:
                report.error(rw, "duplicate earning rule id on this card; the whole fix set is dropped")
            eids.add(r.get("id"))
            rate = r.get("rate")
            if isinstance(rate, (int, float)) and not isinstance(rate, bool) and not (0 <= rate <= 1000):
                report.error(rw, f"rate {rate} out of 0-1000; the whole fix set is dropped")
            if isinstance(r.get("categories"), list) and not r["categories"]:
                report.error(rw, "categories is empty; the whole fix set is dropped")
            if r.get("rewardKind") not in REWARD_KINDS:
                report.error(rw, f"rewardKind must be points or cashback (is {r.get('rewardKind')!r}); the whole fix set is dropped")
            if app.catalog and r.get("programID") not in known_programs:
                report.error(rw, f"unknown programID {r.get('programID')!r}; the whole fix set is dropped")
            cap = r.get("capMinor")
            if isinstance(cap, int) and not (0 <= cap <= MAX_AMOUNT_MINOR):
                report.error(rw, f"capMinor {cap} out of range; the whole fix set is dropped")
            s, e = r.get("startsOn"), r.get("endsBefore")
            if isinstance(s, str) and isinstance(e, str) and s >= e:
                report.error(rw, f"startsOn {s} is not before endsBefore {e}; the whole fix set is dropped")
            check_link(r.get("sourceURL"), "pageLink", report, f"{rw}.sourceURL")
        # Lounge access
        lids = set()
        for j, g in enumerate(c.get("loungeAccess") or []):
            if not isinstance(g, dict):
                continue
            gw = f"{where} loungeAccess {g.get('id')}"
            if g.get("id") in lids:
                report.error(gw, "duplicate lounge grant id on this card; the whole fix set is dropped")
            lids.add(g.get("id"))
            check_link(g.get("sourceURL"), "cardLink", report, f"{gw}.sourceURL")
            t = g.get("finderURLTemplate")
            if isinstance(t, str):
                check_link(t.replace("{IATA}", "XXX"), "cardLink", report, f"{gw}.finderURLTemplate")

    if not app.catalog:
        report.warn(W, "app repo not found: card programIDs / status programs not checked against the bundled program list")

    # French table
    for k, v in french.items():
        if blank(k) or blank(v):
            report.error(W, f"french entry {k[:60]!r} has an empty English or French side")
        elif k not in english_used:
            report.warn(W, f"french key not used by any Canadian card text in this file (unused): {k[:80]!r}...")
    if app.french is not None:
        missing = [t for t in sorted(english_used) if t and t not in french and t not in app.french]
        if missing:
            report.warn(W, f"{len(missing)} Canadian card text(s) have no French (not in `french` nor bundled catalog-fr.json); "
                           "French users see English. First: " + "; ".join(repr(m[:60]) for m in missing[:3]))

    # Cash-back kind on a program that is a points program in the app
    for pid, p in file_programs.items():
        b = bundled_programs.get(pid)
        if b and isinstance(p, dict) and (p.get("kind") == "cashback") != (b.get("kind") == "cashback"):
            report.warn(W, f"PICKS program {pid!r} kind is {p.get('kind')!r} here but {b.get('kind')!r} in the app's bundled list. "
                           "A cashback program never counts as one of a Free user's 3 picks; check this is intended")
        elif not b and isinstance(p, dict) and p.get("kind") == "cashback" and app.catalog:
            report.warn(W, f"PICKS new program {pid!r} has kind cashback (never counts as a Free pick); check this is a real cash-back program")


def tiers_for(program, statustiers):
    return statustiers.get(program) if program in statustiers else BUILTIN_TIERS.get(program)


STATUS_TIERS_IN_USE = {}


def check_grant_rank(program, tier, rank, scale, report, where):
    """A status grant's rank must equal (tierIndex+1)*scale; 0 = the program's base (member) tier.

    scale 10: card status grants (StatusGrant.rank). scale 1: status match ranks (app/data.json).
    Tiers: StatusTierCatalog built-ins with app/data.json `statusTiers` replacing a program's list.
    """
    if not isinstance(rank, int) or isinstance(rank, bool) or not isinstance(tier, str):
        return
    tiers = tiers_for(program, STATUS_TIERS_IN_USE)
    if not tiers:
        return  # the app has no tier list for this program: nothing to compare with
    idx = tier_index(tiers, tier)
    if rank == 0:
        if idx is not None:
            report.error(where, f"{program} {tier!r} has rank 0 (base tier) but is tier {idx + 1} of {tiers}; rank should be {(idx + 1) * scale}")
        return
    if idx is None:
        expected = rank // scale - 1 if rank % scale == 0 else None
        hint = f" (rank {rank} is {tiers[expected]!r})" if expected is not None and 0 <= expected < len(tiers) else ""
        if expected is None or not (0 <= expected < len(tiers)):
            report.error(where, f"{program} rank {rank} is not a tier rank (tiers are {', '.join(str((i + 1) * scale) for i in range(len(tiers)))}) "
                                f"and {tier!r} is not one of {tiers}")
        else:
            report.error(where, f"{program} tier name {tier!r} does not match the app's tier at rank {rank}{hint}; "
                                "the person's tier will not be marked")
        return
    if rank != (idx + 1) * scale:
        report.error(where, f"{program} {tier!r} is tier {idx + 1} ({tiers[idx]!r}) so its rank must be {(idx + 1) * scale}, not {rank}")


# ---------------------------------------------------------------------------------------------
# lounges/corrections.json
# ---------------------------------------------------------------------------------------------

HOURS_OK = re.compile(
    r"(\d{1,2}(:\d{2})?\s*(a\.?\s?m\.?|p\.?\s?m\.?|h)?\s*[-–—to]+\s*\d{1,2}(:\d{2})?|24\s*hours|24/7|closed|open|"
    r"reopen|varies|vary|flight|departure|arrival|hours|until|noon|midnight|seasonal|temporar|call|see )",
    re.IGNORECASE,
)


def check_lounges(data, app, report):
    W = FILES["lounges"]
    airport_re = re.compile(r"[A-Z]{3}")
    whole = "; the app rejects the WHOLE lounge file"
    for k, t in [("remove", list), ("change", list), ("add", list), ("replaceAirports", dict), ("ratings", list)]:
        if k in data and not isinstance(data[k], t):
            report.error(W, f"`{k}` has the wrong type; the app ignores the whole file")
    remove = data.get("remove") if isinstance(data.get("remove"), list) else []
    change = data.get("change") if isinstance(data.get("change"), list) else []
    add = data.get("add") if isinstance(data.get("add"), list) else []
    repl = data.get("replaceAirports") if isinstance(data.get("replaceAirports"), dict) else {}
    ratings = data.get("ratings") if isinstance(data.get("ratings"), list) else []
    n_entries = len(add) + sum(len(v) for v in repl.values() if isinstance(v, list))
    if n_entries + len(remove) + len(change) > 2000 or len(ratings) > 5000 or len(repl) > 100:
        report.error(W, "too many entries (2,000 entries, 5,000 ratings, 100 replaced airports max)" + whole)
    unknown_airports = set()

    def airport(code, where):
        if not isinstance(code, str) or not airport_re.fullmatch(code):
            report.error(where, f"airport code {code!r} is not 3 upper-case letters" + whole)
        elif app.lounge_airports is not None and code not in app.lounge_airports:
            unknown_airports.add(code)

    def hours(lst, where):
        for h in lst or []:
            if isinstance(h, str) and h.strip() and not HOURS_OK.search(h):
                report.warn(where, f"hours line does not look like opening hours: {h!r}")

    def https_link(u, purpose, where):
        if u is None:
            return
        try:
            p = urlsplit(u) if isinstance(u, str) else None
        except ValueError:
            p = None
        if not p or p.scheme != "https" or not p.hostname:
            report.error(where, f"link {u!r} is not HTTPS" + whole)
            return
        why = url_problem(u, purpose)
        if why:
            report.error(where, f"link {u!r}: {why}; the app drops it")

    def entry(e, where, struct):
        probs = check_struct(e, struct, "entry")
        for p in probs:
            report.error(where, p + "; the app skips this entry (unknown network or missing field)")
        if not isinstance(e, dict):
            return
        name_field = "name"
        if struct == "Change":
            airport(e.get("airport"), where)
            if blank(e.get("name")) or ("newName" in e and e.get("newName") is not None and blank(e.get("newName"))):
                report.error(where, "empty name / newName" + whole)
            https_link(e.get("officialURL"), "loungeLink", where + ".officialURL")
            for j, u in enumerate(e.get("photoURLs") or []):
                https_link(u, "loungePhoto", f"{where}.photoURLs[{j}]")
            hours(e.get("hours"), where)
        elif struct == "Target":
            airport(e.get("airport"), where)
            if blank(e.get(name_field)):
                report.error(where, "empty name" + whole)
        else:
            airport(e.get("airportCode"), where)
            if blank(e.get("name")):
                report.error(where, "empty name" + whole)
            https_link(e.get("officialURL"), "loungeLink", where + ".officialURL")
            for j, u in enumerate(e.get("photoURLs") or []):
                https_link(u, "loungePhoto", f"{where}.photoURLs[{j}]")
            hours(e.get("hours"), where)

    for i, e in enumerate(remove):
        entry(e, f"{W} remove[{i}]", "Target")
    for i, e in enumerate(change):
        entry(e, f"{W} change[{i}] ({e.get('airport') if isinstance(e, dict) else ''} {e.get('name') if isinstance(e, dict) else ''})", "Change")
    for i, e in enumerate(add):
        entry(e, f"{W} add[{i}] ({e.get('airportCode') if isinstance(e, dict) else ''} {e.get('name') if isinstance(e, dict) else ''})", "LoungeCatalogEntry")
    for code, lst in repl.items():
        airport(code, f"{W} replaceAirports[{code!r}]")
        if not isinstance(lst, list):
            report.error(f"{W} replaceAirports[{code!r}]", "must be a list" + whole)
            continue
        for i, e in enumerate(lst):
            where = f"{W} replaceAirports[{code}][{i}]"
            entry(e, where, "LoungeCatalogEntry")
            if isinstance(e, dict) and e.get("airportCode") != code:
                report.error(where, f"airportCode {e.get('airportCode')!r} is not {code}" + whole)
    for i, r in enumerate(ratings):
        where = f"{W} ratings[{i}]"
        for p in check_struct(r, "LoungeRating", "rating"):
            report.error(where, p + "; the app skips this rating")
        if not isinstance(r, dict):
            continue
        rt = r.get("rating")
        if not (isinstance(rt, (int, float)) and 0 < rt <= 5) or blank(r.get("name")) or blank(r.get("source")) \
                or not is_date(r.get("checked")) or not airport_re.fullmatch(str(r.get("airport"))) \
                or (r.get("reviewCount") is not None and not (isinstance(r["reviewCount"], int) and r["reviewCount"] > 0)):
            report.error(where, "rating must be 0-5 with a name, source, 3-letter airport, yyyy-MM-dd checked date and positive reviewCount; the app skips it")
        if r.get("sourceURL") is not None:
            u = r["sourceURL"]
            if not isinstance(u, str) or urlsplit(u).scheme != "https":
                report.error(where, f"sourceURL {u!r} is not HTTPS; the app skips this rating")
            elif url_problem(u, "loungeLink"):
                report.warn(where, f"sourceURL {u!r}: {url_problem(u, 'loungeLink')}")
    if app.lounge_airports is None:
        report.warn(W, "app repo not found: airport codes checked for format only")
    elif unknown_airports:
        report.warn(W, f"airport codes not in the app's bundled lounge list (fine for a new airport, check for typos): "
                       f"{', '.join(sorted(unknown_airports))}")


# ---------------------------------------------------------------------------------------------
# status/benefits.json
# ---------------------------------------------------------------------------------------------

def check_status(data, app, report):
    W = FILES["status"]
    if app.status_benefits:
        b = app.status_benefits.get("updated")
        if is_date(data.get("updated")) and is_date(b) and data["updated"] < b:
            report.error(W, f"`updated` {data['updated']} is older than the app's bundled status-benefits.json ({b}); "
                            "the app will IGNORE this file. Set `updated` to today")
    else:
        report.warn(W, "app repo not found: `updated` not compared with the bundled status-benefits.json")
    progs = data.get("programs")
    if not isinstance(progs, dict):
        report.error(W, "`programs` must be an object; the app ignores the whole file")
        return
    whole = "; one bad program makes the app reject the WHOLE status file"
    if len(progs) > 300:
        report.error(W, "more than 300 programs" + whole)
    for pid, p in progs.items():
        where = f"{W} programs.{pid}"
        if blank(pid) or not isinstance(p, dict):
            report.error(where, "empty id or not an object" + whole)
            continue
        for f in ("sourceName", "sourceURL", "checked"):
            if not isinstance(p.get(f), str):
                report.error(where, f"`{f}` is missing" + whole)
        u = p.get("sourceURL")
        if isinstance(u, str):
            sp = urlsplit(u)
            if sp.scheme != "https" or not sp.hostname:
                report.error(where, f"sourceURL {u!r} is not HTTPS" + whole)
            elif url_problem(u, "statusBenefitSource"):
                report.error(where, f"sourceURL {u!r}: {url_problem(u, 'statusBenefitSource')}; the app drops the link")
        if isinstance(p.get("checked"), str) and len(p["checked"]) != 10:
            report.error(where, f"checked {p['checked']!r} is not yyyy-MM-dd" + whole)
        if p.get("note") and not p.get("noteFR"):
            report.warn(where, "has `note` but no `noteFR`")
        tiers = p.get("tiers")
        if not isinstance(tiers, list) or not tiers or len(tiers) > 12:
            report.error(where, "`tiers` must have 1-12 tiers" + whole)
            continue
        names = [t.get("name") for t in tiers if isinstance(t, dict)]
        if len(set(names)) != len(names):
            report.error(where, "tier names are not unique" + whole)
        for t in tiers:
            if not isinstance(t, dict):
                report.error(where, "a tier is not an object" + whole)
                continue
            tw = f"{where} tier {t.get('name')!r}"
            ben = t.get("benefits")
            if blank(t.get("name")) or not isinstance(ben, list) or not ben or len(ben) > 20 or any(blank(x) for x in ben):
                report.error(tw, "needs a name and 1-20 non-blank benefits" + whole)
                continue
            keys = t.get("keys")
            if keys is not None:
                if not isinstance(keys, list) or len(keys) != len(ben) or any(blank(k) for k in keys):
                    report.error(tw, f"`keys` must have one non-empty key per benefit ({len(ben)})" + whole)
                else:
                    for k in keys:
                        if k.split(".")[0] not in ENUMS["Topic"]:
                            report.warn(tw, f"key {k!r}: topic is not one of {', '.join(ENUMS['Topic'])} (shown under Other)")
            fr = t.get("benefitsFR")
            if not isinstance(fr, list):
                report.error(tw, "`benefitsFR` is missing: French users see English")
            elif len(fr) != len(ben) or any(blank(x) for x in fr):
                report.error(tw, f"`benefitsFR` has {len(fr)} lines for {len(ben)} English lines (or a blank line): French users see English")
            if t.get("qualify") and not t.get("qualifyFR"):
                report.warn(tw, "has `qualify` but no `qualifyFR`")
            for c in t.get("credits") or []:
                if not isinstance(c, dict) or blank(c.get("id")) or not isinstance(c.get("amountMinor"), int) \
                        or not (1 <= c["amountMinor"] <= 10_000_000):
                    report.error(tw, f"credit {c!r}: needs an id and amountMinor 1-10,000,000; the app does not track it")
                elif c.get("period") not in TRACKABLE_STATUS_PERIODS and c.get("period") != "none":
                    report.error(tw, f"credit {c.get('id')!r}: period {c.get('period')!r} is not one of "
                                     f"{', '.join(sorted(TRACKABLE_STATUS_PERIODS))} (or none); the app does not track it")
        # Tier names vs the app's tier list (StatusTierCatalog + app/data.json statusTiers)
        app_tiers = tiers_for(pid, STATUS_TIERS_IN_USE)
        if app_tiers:
            idx = [tier_index(names, a) for a in app_tiers]
            missing = [a for a, i in zip(app_tiers, idx) if i is None]
            for a in missing:
                report.error(where, f"app tier {a!r} has no tier here with the same name; a person with that status gets no benefits shown/marked "
                                    f"(tiers here: {names})")
            found = [i for i in idx if i is not None]
            if found != sorted(found):
                report.error(where, f"tiers are not in the same order as the app's tier list {app_tiers}")
            extra = [n for j, n in enumerate(names) if j not in found and j != 0]
            if extra:
                report.info(where, f"tiers not in the app's tier list (shown, never marked as held): {extra}")


# ---------------------------------------------------------------------------------------------
# app/data.json
# ---------------------------------------------------------------------------------------------

def check_app(data, app, report):
    W = FILES["app"]

    def section_error(sec, where, msg):
        report.error(f"{W} {where}", f"{msg}. One bad entry makes the app DROP THE WHOLE `{sec}` section (the built-in table is used)")

    def entry_error(where, msg):
        report.error(f"{W} {where}", f"{msg}; the app drops this entry")

    def typed(sec, t):
        v = data.get(sec)
        if v is None:
            return None
        if not isinstance(v, t):
            report.error(W, f"`{sec}` has the wrong JSON type; the app drops the whole section")
            return None
        return v

    counted = 0
    for sec in ["storeChains", "pointsGuySources", "frequentMilerSources", "partnerNames", "statusMatchRules",
                "statusMatchCards", "pointValueNames", "homeCities", "bestUses"]:
        v = data.get(sec)
        counted += len(v) if isinstance(v, list) else 0
    for sec in ["princeOfTravelSources", "princeOfTravelPartners", "statusProgramNames", "statusTiers", "retiredCards",
                "nearbyCities", "sources", "liveLounges"]:
        v = data.get(sec)
        counted += len(v) if isinstance(v, dict) else 0
    tp = data.get("transferPartners")
    if isinstance(tp, dict):
        counted += sum(len(v) for v in tp.values() if isinstance(v, list))
    if counted > 5000:
        report.error(W, f"{counted} entries in the counted sections (max 5,000); the app ignores the whole file")

    # statusTiers first: other rank checks use it.
    st = typed("statusTiers", dict) or {}
    for k, v in st.items():
        if blank(k) or not isinstance(v, list) or not v or any(blank(x) for x in v):
            section_error("statusTiers", f"statusTiers.{k}", "needs a program id and a non-empty list of non-blank tier names")
        elif len(set(v)) != len(v):
            report.warn(f"{W} statusTiers.{k}", "has a repeated tier name")

    tp = typed("transferPartners", dict) or {}
    for src, lst in tp.items():
        if blank(src) or not isinstance(lst, list):
            section_error("transferPartners", f"transferPartners.{src}", "empty source id or not a list")
            continue
        for i, p in enumerate(lst):
            if not isinstance(p, dict) or blank(p.get("id")) or blank(p.get("name")) or \
                    not all(isinstance(p.get(f), int) and not isinstance(p.get(f), bool) and 1 <= p[f] <= 100_000 for f in ("from", "to")) \
                    or blank(p.get("category")) or blank(p.get("unit")):
                section_error("transferPartners", f"transferPartners.{src}[{i}]", "needs id, name, category, unit and from/to 1-100,000")

    for i, ch in enumerate(typed("storeChains", list) or []):
        if not isinstance(ch, dict) or not isinstance(ch.get("match"), list) or not ch["match"] or any(blank(m) for m in ch["match"]) \
                or ch.get("category") not in ENUMS["Category"]:
            section_error("storeChains", f"storeChains[{i}]", "needs a non-empty match list and a known card Category")

    rules = typed("statusMatchRules", list) or []
    for i, r in enumerate(rules):
        where = f"statusMatchRules[{i}]"
        if not isinstance(r, dict):
            section_error("statusMatchRules", where, "not an object")
            continue
        req = {"source": str, "minRank": int, "destProgram": str, "destRank": int, "dest": str, "instant": bool, "detail": str}
        bad = [f for f, t in req.items() if not isinstance(r.get(f), t) or (t is int and isinstance(r.get(f), bool))]
        if bad or blank(r.get("source")) or blank(r.get("destProgram")) or blank(r.get("dest")) \
                or r.get("minRank", 0) < 0 or r.get("destRank", 0) < 0:
            section_error("statusMatchRules", where, f"missing/blank/wrong-type fields {bad or ''} or a negative rank")
            continue
        if r.get("countries") is not None:
            if not isinstance(r["countries"], list) or any(c not in ("US", "CA") for c in r["countries"]):
                section_error("statusMatchRules", where, f"countries {r['countries']!r}: only \"US\" and \"CA\" are used")
        if r.get("url") is not None:
            check_link(r["url"], "statusMatchPage", report, f"{W} {where}.url")
        check_grant_rank(r["destProgram"], r["dest"], r["destRank"], 1, report, f"{W} {where} dest")

    for i, c in enumerate(typed("statusMatchCards", list) or []):
        where = f"statusMatchCards[{i}]" + (f" ({c.get('name')})" if isinstance(c, dict) else "")
        if not isinstance(c, dict) or blank(c.get("name")) or not isinstance(c.get("grants"), list):
            section_error("statusMatchCards", where, "needs a name and a grants list")
            continue
        if c.get("country") is not None and c["country"] not in ("US", "CA"):
            section_error("statusMatchCards", where, f"country {c['country']!r} must be \"US\" or \"CA\"")
        if isinstance(c.get("mqd"), int) and c["mqd"] < 0:
            section_error("statusMatchCards", where, "mqd is negative")
        for j, g in enumerate(c["grants"]):
            gw = f"{where} grants[{j}]"
            if not isinstance(g, dict) or blank(g.get("program")) or blank(g.get("tier")) \
                    or not isinstance(g.get("rank"), int) or isinstance(g.get("rank"), bool) or g["rank"] < 0:
                section_error("statusMatchCards", gw, "needs program, tier and a rank >= 0")
                continue
            check_grant_rank(g["program"], g["tier"], g["rank"], 1, report, f"{W} {gw}")

    for i, r in enumerate(typed("partnerStatuses", list) or []):
        where = f"partnerStatuses[{i}]"
        req = {"source": str, "minRank": int, "destProgram": str, "destRank": int, "dest": str, "link": bool}
        if not isinstance(r, dict) or any(not isinstance(r.get(f), t) or (t is int and isinstance(r.get(f), bool)) for f, t in req.items()):
            entry_error(where, "needs source, minRank, destProgram, destRank, dest and link (true/false)")
            continue
        if blank(r["source"]) or blank(r["destProgram"]) or blank(r["dest"]) or r["source"] == r["destProgram"] \
                or not (1 <= r["minRank"] <= 99) or not (1 <= r["destRank"] <= 99):
            entry_error(where, "blank names, same source and dest program, or a rank outside 1-99")
            continue
        if isinstance(r.get("detail"), str) and len(r["detail"]) > 400:
            report.warn(f"{W} {where}", "detail is longer than 400 characters (cut short)")
        if r.get("url") is not None:
            check_link(r["url"], "statusMatchPage", report, f"{W} {where}.url")
        check_grant_rank(r["destProgram"], r["dest"], r["destRank"], 1, report, f"{W} {where} dest")

    for k, v in (typed("statusProgramNames", dict) or {}).items():
        if blank(k) or blank(v):
            section_error("statusProgramNames", f"statusProgramNames.{k}", "blank id or name")
    for k, v in (typed("statusMatchPrograms", dict) or {}).items():
        if blank(k) or not isinstance(v, bool):
            section_error("statusMatchPrograms", f"statusMatchPrograms.{k}", "blank id or not true/false")

    tt = typed("tripTints", dict) or {}
    if len(tt) > 300:
        section_error("tripTints", "tripTints", "more than 300 entries")
    for k, v in tt.items():
        if not (k == "default" or re.fullmatch(r"[A-Z]{2}", k)) or not (isinstance(v, str) and re.fullmatch(r"[0-9A-Fa-f]{6}", v)):
            section_error("tripTints", f"tripTints.{k}", "key must be \"default\" or a 2-letter upper-case country; value RRGGBB")
    for sec in ("tripPhotos", "tripPhotosTall"):
        tbl = typed(sec, dict) or {}
        if len(tbl) > 300:
            report.error(f"{W} {sec}", "more than 300 entries; the app drops the whole section")
        for k, v in tbl.items():
            if not re.fullmatch(r"[A-Z]{2}", k):
                entry_error(f"{sec}.{k}", "key must be a 2-letter upper-case country code")
            check_link(v, "loungePhoto", report, f"{W} {sec}.{k}", allow_empty=False)

    rl = typed("referralLinks", dict) or {}
    if len(rl) > 300:
        report.warn(f"{W} referralLinks", "more than 300 links: the app keeps the first 300 by card id")
    for k, v in rl.items():
        if not (0 < len(k) <= 120 and re.fullmatch(r"[A-Za-z0-9._-]+", k)):
            entry_error(f"referralLinks.{k}", "card id must be letters, digits, - _ . (max 120)")
        check_link(v, "referral", report, f"{W} referralLinks.{k}", allow_empty=False)
        if app.catalog is not None and k not in {c.get("id") for c in app.catalog.get("cards", [])} \
                and k not in {c.get("id") for c in CURRENT_CARD_IDS}:
            report.warn(f"{W} referralLinks.{k}", "card id is not in the bundled card list or cards/corrections.json (link never shown)")

    for i, e in enumerate(typed("loungeNetworkPartners", list) or []):
        where = f"loungeNetworkPartners[{i}]"
        if not isinstance(e, dict) or e.get("network") not in ENUMS["LoungeProvider"] or not isinstance(e.get("airlines"), list) \
                or not e["airlines"] or len(e["airlines"]) > 80 or any(not airline_code(a) for a in e["airlines"]):
            entry_error(where, "needs a known lounge network and 1-80 two-character airline codes")
    for i, e in enumerate(typed("airlineTerminals", list) or []):
        where = f"airlineTerminals[{i}]"
        ok = isinstance(e, dict) and isinstance(e.get("airport"), str) and re.fullmatch(r"[A-Za-z]{3}", e["airport"].strip() or "") \
            and isinstance(e.get("airlines"), list) and e["airlines"] and len(e["airlines"]) <= 60 \
            and (e["airlines"] == ["*"] or all(airline_code(a) for a in e["airlines"])) \
            and isinstance(e.get("terminals"), list) and e["terminals"] and len(e["terminals"]) <= 12 \
            and all(isinstance(t, str) and t.strip() and len(t.strip()) <= 80 for t in e["terminals"])
        if not ok:
            entry_error(where, "needs a 3-letter airport, airline codes (or [\"*\"]) and 1-12 terminal names")

    ac = typed("airlineCityAirports", dict) or {}
    if len(ac) > 300:
        section_error("airlineCityAirports", "airlineCityAirports", "more than 300 rows")
    for k, row in ac.items():
        if not (k == "*" or airline_code(k)) or not isinstance(row, dict) or len(row) > 200 or \
                any(blank(c) or not (isinstance(a, str) and re.fullmatch(r"[A-Z]{3}", a)) for c, a in row.items()):
            section_error("airlineCityAirports", f"airlineCityAirports.{k}", "airline code (or *) -> city -> 3-letter airport")
    al = typed("airlines", dict) or {}
    if len(al) > 600:
        section_error("airlines", "airlines", "more than 600 entries")
    for k, v in al.items():
        if not airline_code(k) or blank(v) or len(v) > 60 or "\n" in v:
            section_error("airlines", f"airlines.{k}", "2-character airline code -> name (max 60, one line)")

    for i, s in enumerate(typed("pointsGuySources", list) or []):
        if not isinstance(s, dict) or any(blank(s.get(f)) for f in ("match", "id", "name")):
            section_error("pointsGuySources", f"pointsGuySources[{i}]", "needs match, id and name")
    for i, s in enumerate(typed("frequentMilerSources", list) or []):
        if not isinstance(s, dict) or any(blank(s.get(f)) for f in ("match", "id", "name")):
            section_error("frequentMilerSources", f"frequentMilerSources[{i}]", "needs match, id and name")
    for i, s in enumerate(typed("partnerNames", list) or []):
        if not isinstance(s, dict) or any(blank(s.get(f)) for f in ("match", "id")):
            section_error("partnerNames", f"partnerNames[{i}]", "needs match and id")
    for k, v in (typed("princeOfTravelSources", dict) or {}).items():
        if blank(k) or not isinstance(v, dict) or blank(v.get("id")) or blank(v.get("name")):
            section_error("princeOfTravelSources", f"princeOfTravelSources.{k}", "needs id and name")
    for k, v in (typed("princeOfTravelPartners", dict) or {}).items():
        if blank(k) or blank(v):
            section_error("princeOfTravelPartners", f"princeOfTravelPartners.{k}", "blank key or value")
    for i, s in enumerate(typed("pointValueNames", list) or []):
        if not isinstance(s, dict) or blank(s.get("name")) or not isinstance(s.get("ids"), list) or not s["ids"] or any(blank(x) for x in s["ids"]):
            section_error("pointValueNames", f"pointValueNames[{i}]", "needs a name and non-blank ids")
    for k, v in (typed("retiredCards", dict) or {}).items():
        if blank(k) or blank(v) or k == v:
            section_error("retiredCards", f"retiredCards.{k}", "blank ids or a card retired into itself")
    for i, c in enumerate(typed("homeCities", list) or []):
        if not isinstance(c, dict) or blank(c.get("city")) or not isinstance(c.get("airports"), list) or not c["airports"] \
                or any(not (isinstance(a, str) and re.fullmatch(r"[A-Z]{3}", a)) for a in c["airports"]):
            section_error("homeCities", f"homeCities[{i}]", "needs a city and 3-letter airport codes")
    for k, v in (typed("nearbyCities", dict) or {}).items():
        if blank(k) or not isinstance(v, list) or any(blank(x) for x in v):
            section_error("nearbyCities", f"nearbyCities.{k}", "blank city")
    uses = typed("bestUses", list) or []
    ids = [u.get("id") for u in uses if isinstance(u, dict)]
    if len(set(ids)) != len(ids):
        section_error("bestUses", "bestUses", "repeated id")
    for i, u in enumerate(uses):
        where = f"bestUses[{i}]"
        if not isinstance(u, dict):
            section_error("bestUses", where, "not an object")
            continue
        kind = u.get("kind")
        cabin_ok = (u.get("cabin") is None) if kind == "hotel" else (u.get("cabin") in ("economy", "premium", "business", "first"))
        need = ["id", "programID", "programName", "title", "titleFR", "why", "whyFR", "tip", "tipFR", "unit", "unitFR", "sourceName"]
        if any(blank(u.get(f)) for f in need) or kind not in ("flight", "hotel") or not cabin_ok \
                or (u.get("points") is not None) == (u.get("rule") is not None) or (u.get("rule") is None) != (u.get("ruleFR") is None) \
                or (isinstance(u.get("points"), int) and u["points"] <= 0) or not is_date(u.get("checked")) \
                or urlsplit(str(u.get("sourceURL"))).scheme != "https":
            section_error("bestUses", where, "missing EN/FR text, bad kind/cabin, points XOR rule, date or HTTPS source")
            continue
        check_link(u.get("sourceURL"), "bestUseSource", report, f"{W} {where}.sourceURL")
    src_keys = {"princeOfTravel", "pointsGuyBonuses", "frequentMiler", "flytrippers", "flyingBluePromos", "pointValues"}
    for k, v in (typed("sources", dict) or {}).items():
        if k not in src_keys or not isinstance(v, dict):
            section_error("sources", f"sources.{k}", f"unknown key (known: {', '.join(sorted(src_keys))})")
        elif v.get("url") is not None and urlsplit(str(v["url"])).scheme != "https":
            section_error("sources", f"sources.{k}", "url must be HTTPS")
    for k, v in (typed("liveLounges", dict) or {}).items():
        if k not in ENUMS["LoungeProvider"] or not isinstance(v, bool):
            section_error("liveLounges", f"liveLounges.{k}", "unknown lounge network or not true/false")
    feats = typed("features", dict) or {}
    for k, v in feats.items():
        if k not in ENUMS["Feature"]:
            report.warn(f"{W} features.{k}", f"unknown switch (ignored). Known: {', '.join(ENUMS['Feature'])}")
        elif v is not False:
            report.warn(f"{W} features.{k}", "only `false` does anything (switches can only turn a feature off)")
    tb = typed("transferBonuses", dict)
    if tb is not None:
        for i, b in enumerate(tb.get("bonuses") or []):
            where = f"transferBonuses.bonuses[{i}]"
            if not isinstance(b, dict) or blank(b.get("source")) or blank(b.get("destination")) \
                    or not isinstance(b.get("percent"), int) or not (1 <= b["percent"] <= 200) or not is_date(b.get("expires")):
                entry_error(where, "needs source, destination, percent 1-200 and a yyyy-MM-dd expiry")
                continue
            check_link(b.get("sourceURL"), "transferBonusSource", report, f"{W} {where}.sourceURL", allow_empty=False)
    pv = typed("pointValues", dict)
    if pv is not None:
        for cur in ("USD", "CAD"):
            for pid, cents in (pv.get(cur) or {}).items():
                if not isinstance(cents, (int, float)) or isinstance(cents, bool) or not (0.05 <= cents <= 10):
                    entry_error(f"pointValues.{cur}.{pid}", f"{cents!r} cents is outside 0.05-10")

    # Profile badges: only `proOnly: true` is allowed (checked with the gating keys).
    prof = data.get("profile")
    if prof is not None and isinstance(prof, dict):
        for i, b in enumerate(prof.get("badges") or []):
            if isinstance(b, dict) and "proOnly" not in b and not b.get("hidden"):
                report.warn(f"{W} profile.badges[{i}] ({b.get('id')})",
                            "no `proOnly`: a remote badge REPLACES the bundled one, so it is shown to Free users. "
                            "If the bundled badge is Pro-only, add \"proOnly\": true")

    # Notice banner (shown to every user, Free included)
    notice = data.get("notice")
    if notice is not None:
        check_notice(notice, report)


def airline_code(s):
    return isinstance(s, str) and len(s) == 2 and all(c.isascii() and (c.isupper() or c.isdigit()) for c in s) \
        and any(c.isalpha() for c in s)


NOTICE_LINK = re.compile(r"(?i)(://|\bwww\.|@|\b(mailto|tel|sms|itms[a-z-]*):|\b[a-z0-9-]+\.(com|ca|net|org|io|app|co|ly|me|info|biz|link|page|site|xyz|gg|us|fr)\b)")


def check_notice(n, report):
    W = FILES["app"] + " notice"
    if not isinstance(n, dict):
        report.error(W, "must be an object; the app shows no notice")
        return
    probs = []
    for f in ("id", "title", "titleFR", "body", "bodyFR", "until"):
        if blank(n.get(f)):
            probs.append(f"`{f}` is missing or blank")
    if isinstance(n.get("id"), str) and (len(n["id"]) > 64 or "\n" in n["id"]):
        probs.append("`id` is over 64 characters or has a new line")
    for f, mx in (("title", 80), ("titleFR", 80), ("body", 400), ("bodyFR", 400)):
        if isinstance(n.get(f), str) and len(n[f]) > mx:
            probs.append(f"`{f}` is over {mx} characters")
        if isinstance(n.get(f), str) and NOTICE_LINK.search(n[f]):
            probs.append(f"`{f}` contains a link, e-mail or web address (not allowed)")
    if not is_date(n.get("until")):
        probs.append("`until` is not yyyy-MM-dd")
    elif n["until"] < datetime.date.today().isoformat():
        report.warn(W, f"`until` {n['until']} is in the past: nobody sees this notice")
    for f in ("minAppVersion", "maxAppVersion"):
        v = n.get(f)
        if v is not None and not (isinstance(v, str) and re.fullmatch(r"\d+(\.\d+){0,3}", v.strip())):
            probs.append(f"`{f}` {v!r} is not a version like 1.5 or 1.5.2")
    for p in probs:
        report.error(W, p + "; the app shows no notice")
    report.warn(W, "check wording does not promise Pro features to Free users (the banner shows to everyone). "
                   f"title: {n.get('title')!r} body: {n.get('body')!r} | FR title: {n.get('titleFR')!r} body: {n.get('bodyFR')!r}")


CURRENT_CARD_IDS = []


# ---------------------------------------------------------------------------------------------
# Pick diff: what changes Free users' 3 picks (compared with the last push)
# ---------------------------------------------------------------------------------------------

def pick_diff(cur, base, base_ref, report):
    W = FILES["cards"]
    if base is None:
        report.warn(W, f"pick diff skipped: no base copy at {base_ref!r} (use --base <ref>)")
        return

    def cards_by_id(d):
        return {c.get("id"): c for c in (d.get("cards") or []) if isinstance(c, dict)}

    def grants(c):
        return sorted((s.get("programID"), s.get("tier"), s.get("rank"), s.get("automatic"))
                      for s in (c.get("statuses") or []) if isinstance(s, dict))

    a, b = cards_by_id(base), cards_by_id(cur)
    changes = 0
    for cid in sorted(set(a) | set(b), key=str):
        if cid not in a:
            report.warn(W, f"PICKS card {cid!r} is new in this file: programIDs {b[cid].get('programIDs')}, "
                           f"status grants {[g[:3] for g in grants(b[cid])]}. Check the Free pick count is what you expect")
            changes += 1
            continue
        if cid not in b:
            report.warn(W, f"PICKS card {cid!r} was removed from this file: the app's built-in programIDs/statuses come back for it")
            changes += 1
            continue
        pa, pb = a[cid].get("programIDs") or [], b[cid].get("programIDs") or []
        if pa != pb:
            report.warn(W, f"PICKS card {cid!r} programIDs changed {pa} -> {pb}: changes Free users' 3 picks")
            changes += 1
        ga, gb = grants(a[cid]), grants(b[cid])
        if ga != gb:
            removed = [g for g in ga if g not in gb]
            added = [g for g in gb if g not in ga]
            report.warn(W, f"PICKS card {cid!r} status grants changed: removed {removed} added {added} "
                           "(program, tier, rank, automatic): changes Free users' 3 picks")
            changes += 1
    pa = {p.get("id"): p for p in (base.get("programs") or []) if isinstance(p, dict)}
    pb = {p.get("id"): p for p in (cur.get("programs") or []) if isinstance(p, dict)}
    for pid in sorted(set(pa) | set(pb), key=str):
        ka = pa.get(pid, {}).get("kind") if pid in pa else None
        kb = pb.get(pid, {}).get("kind") if pid in pb else None
        if pid in pa and pid in pb and ka != kb and "cashback" in (ka, kb):
            report.warn(W, f"PICKS program {pid!r} kind changed {ka!r} -> {kb!r}: a cashback program never counts as a Free pick")
            changes += 1
        elif pid not in pa and kb == "cashback":
            report.warn(W, f"PICKS program {pid!r} added with kind cashback (never counts as a Free pick)")
            changes += 1
    report.info(W, f"pick diff vs {base_ref}: {changes} change(s) that affect Free picks")


# ---------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------

def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Validate the Cardwing data files before a push.")
    ap.add_argument("--data-dir", default=os.path.dirname(here), help="data repo folder (default: this repo)")
    ap.add_argument("--app-repo", default=None,
                    help="app repo folder (default: ../stackjet-app next to the data repo; skipped with a WARN if missing)")
    ap.add_argument("--base", default="origin/main", help="git ref to compare with for the pick diff (default origin/main)")
    ap.add_argument("--quiet", action="store_true", help="hide INFO lines")
    args = ap.parse_args(argv)

    report = Report()
    data_dir = os.path.abspath(args.data_dir)
    app_path = args.app_repo or os.path.join(os.path.dirname(data_dir), "stackjet-app")
    app = AppRepo(os.path.abspath(app_path), report)
    if not app.ok:
        report.warn("app repo", f"not found at {app_path}: running every check that does not need it "
                                "(catalogVersion, bundled programs/dates, French table and airport list are skipped)")

    files = {}
    for key, rel in FILES.items():
        path = os.path.join(data_dir, rel)
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError as e:
            report.error(rel, f"cannot read file: {e}")
            continue
        if len(raw) > MAX_BYTES[key]:
            report.error(rel, f"file is {len(raw):,} bytes, over the app's {MAX_BYTES[key]:,} limit; the app ignores it")
        try:
            data = load_json_text(raw.decode("utf-8"), rel, report)
        except (ValueError, UnicodeDecodeError) as e:
            report.error(rel, f"not valid JSON: {e}")
            continue
        if not isinstance(data, dict):
            report.error(rel, "top level must be an object")
            continue
        files[key] = data
        check_header(key, data, report, rel)
        check_gating_keys(key, data, report, rel)
        base_text = git_show(data_dir, args.base, rel)
        if base_text is not None:
            try:
                base = json.loads(base_text)
            except ValueError:
                base = None
            if isinstance(base, dict) and base != data and is_date(base.get("updated")) and is_date(data.get("updated")):
                if data["updated"] < base["updated"]:
                    report.warn(rel, f"`updated` {data['updated']} is older than at {args.base} ({base['updated']})")
                elif data["updated"] == base["updated"]:
                    report.warn(rel, f"content changed since {args.base} but `updated` is still {data['updated']}: bump it")
            files["base_" + key] = base if isinstance(base, dict) else None
        else:
            files["base_" + key] = None

    # Tier lists in use: built-ins with app/data.json statusTiers on top (StatusTierCatalog.published).
    STATUS_TIERS_IN_USE.clear()
    STATUS_TIERS_IN_USE.update(BUILTIN_TIERS)
    st = files.get("app", {}).get("statusTiers")
    if isinstance(st, dict):
        for k, v in st.items():
            if isinstance(v, list) and v and all(isinstance(x, str) for x in v):
                STATUS_TIERS_IN_USE[k] = v
    CURRENT_CARD_IDS[:] = [c for c in (files.get("cards", {}).get("cards") or []) if isinstance(c, dict)]

    if "cards" in files:
        check_cards(files["cards"], app, report)
        pick_diff(files["cards"], files.get("base_cards"), args.base, report)
    if "lounges" in files:
        check_lounges(files["lounges"], app, report)
    if "status" in files:
        check_status(files["status"], app, report)
    if "app" in files:
        check_app(files["app"], app, report)

    for line in report.errors:
        print(line)
    for line in report.warnings:
        print(line)
    if not args.quiet:
        for line in report.infos:
            print(line)
    print(f"\n{len(report.errors)} error(s), {len(report.warnings)} warning(s). "
          + ("FAILED: fix the errors before pushing." if report.errors else "OK to push (read the warnings)."))
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
