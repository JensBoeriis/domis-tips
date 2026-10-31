#!/usr/bin/env python3
"""Henter resultater for ugens kupon og opdaterer data.json.

Køres automatisk af GitHub Actions (se .github/workflows/opdater.yml).
Kilde: ESPN's åbne resultat-feed. Kræver ingen nøgle og ingen ekstra pakker.
"""
import json, re, sys, unicodedata, urllib.request
from datetime import datetime, timedelta, timezone

DATA_FILE = "data.json"
LEAGUES = ["eng.1", "eng.2", "den.1", "den.2", "ger.1", "esp.1", "ita.1", "fra.1",
           "ned.1", "por.1", "sco.1", "swe.1", "nor.1", "uefa.champions", "uefa.europa",
           "uefa.europa.conf", "eng.fa", "eng.league_cup", "den.cup", "ger.dfb_pokal"]
LEAGUE_NAMES = {"eng.1": "Premier League", "eng.2": "Championship", "den.1": "3F Superliga",
                "den.2": "1. division", "ger.1": "Bundesliga", "esp.1": "La Liga", "ita.1": "Serie A",
                "fra.1": "Ligue 1", "ned.1": "Eredivisie", "uefa.champions": "Champions League",
                "uefa.europa": "Europa League", "uefa.europa.conf": "Conference League"}
URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{lg}/scoreboard?dates={d1}-{d2}&limit=300"

# Kendte kaldenavne -> ord der skal findes i ESPN's holdnavn
ALIASES = {
    "manu": ["manchester united", "man united", "man utd"],
    "manchester united": ["manchester united", "man united", "man utd"],
    "man city": ["manchester city"], "manchester city": ["manchester city", "man city"],
    "nottingham f": ["nottingham forest"], "nottingham": ["nottingham forest"],
    "bayern m": ["bayern munich", "bayern munchen", "bayern"], "bayern": ["bayern"],
    "dortmund": ["dortmund"], "leverkusen": ["leverkusen"], "spurs": ["tottenham"],
    "brondby": ["brondby"], "agf": ["agf", "aarhus"], "ob": ["odense"], "fck": ["copenhagen", "kobenhavn"],
    "fc kobenhavn": ["copenhagen", "kobenhavn"], "kobenhavn": ["copenhagen", "kobenhavn"],
    "fc nordsjaelland": ["nordsjaelland"], "fc midtjylland": ["midtjylland"], "fcm": ["midtjylland"],
    "ac horsens": ["horsens"], "randers": ["randers"], "sonderjyske": ["sonderjyske", "sonderjylland"],
    "vejle": ["vejle"], "lyngby": ["lyngby"], "viborg": ["viborg"], "silkeborg": ["silkeborg"],
    "aab": ["aalborg", "aab"], "inter": ["internazionale", "inter milan"], "psg": ["paris saint-germain", "paris sg"],
    "wolves": ["wolverhampton"], "west ham": ["west ham"], "leeds": ["leeds"],
}
STOP = {"fc", "afc", "if", "bk", "ff", "sc", "cf", "ac", "united", "city", "the", "1", "of"}


def norm(s):
    s = (s or "").lower().replace("ø", "o").replace("æ", "ae").replace("å", "a").replace("ß", "ss")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def keys_for(name):
    n = norm(name)
    keys = list(ALIASES.get(n, []))
    if not keys:
        words = [w for w in n.split() if w not in STOP and len(w) > 1]
        keys = [" ".join(words)] if words else [n]
    return keys


def team_names(comp):
    t = comp.get("team", {})
    return " | ".join(norm(t.get(k, "")) for k in ("displayName", "shortDisplayName", "name", "location", "abbreviation"))


def matches(ours, comp):
    hay = team_names(comp)
    return any(k and k in hay for k in keys_for(ours))


def fetch(lg, d1, d2):
    url = URL.format(lg=lg, d1=d1, d2=d2)
    req = urllib.request.Request(url, headers={"User-Agent": "domis-tips/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r).get("events", [])
    except Exception as e:  # en liga der fejler må ikke stoppe resten
        print(f"  {lg}: {e}", file=sys.stderr)
        return []


def to_res(ev):
    comp = ev["competitions"][0]
    st = (ev.get("status") or comp.get("status") or {})
    typ = st.get("type", {})
    name, state = typ.get("name", ""), typ.get("state", "")
    home = next(c for c in comp["competitors"] if c.get("homeAway") == "home")
    away = next(c for c in comp["competitors"] if c.get("homeAway") == "away")
    def sc(c):
        try: return int(float(c.get("score", 0)))
        except Exception: return 0
    if "POSTPONED" in name or "CANCELED" in name or "SUSPENDED" in name or "ABANDONED" in name:
        return {"s": "PP"}
    if state == "pre":
        return {"s": "NS"}
    if state == "post" or typ.get("completed"):
        return {"s": "FT", "h": sc(home), "a": sc(away)}
    if "HALFTIME" in name:
        return {"s": "HT", "h": sc(home), "a": sc(away)}
    minute = (st.get("displayClock") or "").replace("'", "").strip()
    r = {"s": "LIVE", "h": sc(home), "a": sc(away)}
    if minute and minute != "0:00":
        r["min"] = minute
    return r


def main():
    with open(DATA_FILE, encoding="utf-8") as f:
        data = json.load(f)
    week = next(w for w in data["weeks"] if w["id"] == data["current"])
    todo = [m for m in week["matches"] if (m.get("res") or {}).get("s") != "FT"]
    if not todo:
        print("Alle kampe er afsluttet. Intet at gøre."); return

    now = datetime.now(timezone.utc)
    kos = [datetime.fromisoformat(m["ko"].replace("Z", "+00:00")) for m in todo if m.get("ko")]
    start = (min(kos) if kos else now) - timedelta(days=2)
    end = (max(kos) if kos else now) + timedelta(days=2)
    if not kos:  # ny kupon uden tider: kig en uge frem
        end = now + timedelta(days=8)
    d1, d2 = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    events = []
    for lg in LEAGUES:
        for ev in fetch(lg, d1, d2):
            ev["_lg"] = lg
            events.append(ev)
    print(f"Hentede {len(events)} kampe fra {d1} til {d2}")

    changed = []
    for m in todo:
        best = None
        for ev in events:
            comp = ev["competitions"][0]
            cs = comp.get("competitors", [])
            h = next((c for c in cs if c.get("homeAway") == "home"), None)
            a = next((c for c in cs if c.get("homeAway") == "away"), None)
            if not h or not a or not (matches(m["home"], h) and matches(m["away"], a)):
                continue
            evdate = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
            ref = datetime.fromisoformat(m["ko"].replace("Z", "+00:00")) if m.get("ko") else now
            dist = abs((evdate - ref).total_seconds())
            if best is None or dist < best[0]:
                best = (dist, ev, evdate)
        if not best:
            print(f"  Kamp {m['n']} {m['home']} - {m['away']}: ikke fundet")
            continue
        _, ev, evdate = best
        new_res = to_res(ev)
        new_ko = evdate.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        before = json.dumps([m.get("res"), m.get("ko"), m.get("lg")], sort_keys=True)
        m["res"] = new_res
        m["ko"] = new_ko
        if not m.get("lg"):
            m["lg"] = LEAGUE_NAMES.get(ev["_lg"], ev["_lg"])
        if json.dumps([m.get("res"), m.get("ko"), m.get("lg")], sort_keys=True) != before:
            changed.append(f"{m['n']}: {m['home']}-{m['away']} {new_res}")

    if not changed:
        print("Ingen ændringer."); return
    data["updated"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("Opdateret:\n  " + "\n  ".join(changed))


if __name__ == "__main__":
    main()
