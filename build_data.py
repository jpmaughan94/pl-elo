"""Combine the model outputs into one JSON for the page (pl-elo.json).

  python build_data.py <dir with elo.json, attdef.json, players.json, matches.json> <out.json>
The page (games/pl-elo.html) fetches this file; build_page.py embeds the same data as a fallback.
"""
import datetime, json, os, sys

EPOCH = datetime.date(2010, 1, 1)


def day(d):
    return (datetime.date.fromisoformat(d) - EPOCH).days


def make(d="."):
    o = json.load(open(os.path.join(d, "elo.json")))
    teams = sorted(o["history"])
    ti = {t: i for i, t in enumerate(teams)}
    si = {s: i for i, s in enumerate(o["seasons"])}
    hist = []
    for t in teams:
        pts, cur = [], None
        for dt, r, opp, gf, ga, v in o["history"][t]:
            if opp is None:  # season start marker
                cur = next(s for s in o["seasons"] if dt[:4] == s[:4])
                pts.append([day(dt), round(r, 1), -1, 0, 0, 0, si[cur]])
            else:
                pts.append([day(dt), round(r, 1), ti[opp], gf, ga, 1 if v == "H" else 0, si[cur]])
        hist.append(pts)
    load = lambda name: json.load(open(os.path.join(d, name))) if os.path.exists(os.path.join(d, name)) else None
    ad, pl, mt, fc = load("attdef.json"), load("players.json"), load("matches.json"), load("forecast.json")
    return dict(updated=day(o["updated"]), season=o["season"], params=o["params"], n_matches=o["n_matches"],
                seasons=o["seasons"], teams=teams, table=o["table"], peaks=o["peaks"], history=hist,
                players=pl, matches=mt, forecast=fc, ad=dict(rows=ad["rows"], finding=ad["finding"]) if ad else None,
                generated=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"))


if __name__ == "__main__":
    out = make(sys.argv[1] if len(sys.argv) > 1 else ".")
    path = sys.argv[2] if len(sys.argv) > 2 else "pl-elo.json"
    if os.path.exists(path):  # leave the file alone unless the content (not just the timestamp) changed
        old = json.load(open(path))
        if {k: v for k, v in old.items() if k != "generated"} == json.loads(json.dumps({k: v for k, v in out.items() if k != "generated"})):
            print("pl-elo.json unchanged")
            sys.exit(0)
    json.dump(out, open(path, "w"), separators=(",", ":"), ensure_ascii=False)
    print("wrote", path, os.path.getsize(path) // 1024, "KB")
