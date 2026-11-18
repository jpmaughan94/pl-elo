"""Squad churn per club-season from vaastav/Fantasy-Premier-League data.
churn(S, club) = share of club's PL minutes in season S-1 played by players NOT on the club's books in season S.
Players are matched by the stable FPL `code`, clubs by the stable `team_code`."""
import csv, glob, json, os, sys
from collections import defaultdict
base = sys.argv[1] if len(sys.argv) > 1 else "fpl"
seasons = sorted(os.path.basename(p) for p in glob.glob(os.path.join(base, "20*")))
squad, minutes, names = {}, {}, {}
for s in seasons:
    raw = list(csv.DictReader(open(os.path.join(base, s, "players_raw.csv"), encoding="utf-8", errors="replace")))
    squad[s] = {r["code"]: r["team_code"] for r in raw}
    id2 = {r["id"]: (r["code"], r["team_code"]) for r in raw}
    mins = defaultdict(lambda: defaultdict(int))
    for r in csv.DictReader(open(os.path.join(base, s, "merged_gw.csv"), encoding="utf-8", errors="replace")):
        k = id2.get(r["element"])
        if k and r.get("minutes"):
            mins[k[1]][k[0]] += int(float(r["minutes"]))
    minutes[s] = mins
    tp = os.path.join(base, s, "teams.csv")
    if os.path.exists(tp) and os.path.getsize(tp) > 100:
        for r in csv.DictReader(open(tp)):
            names[r["code"]] = r["name"]
out = {}
for prev, cur in zip(seasons, seasons[1:]):
    clubs_now = set(squad[cur].values())
    for club, pm in minutes[prev].items():
        if club not in clubs_now:
            continue
        tot = sum(pm.values())
        left = sum(m for code, m in pm.items() if squad[cur].get(code) != club)
        out.setdefault(cur, {})[club] = round(left / tot, 3)
    # this season: share of minutes so far played by players new to the club
    for club, cm in minutes[cur].items():
        tot = sum(cm.values())
        new = sum(m for code, m in cm.items() if minutes[prev].get(club, {}).get(code, 0) == 0)
        out.setdefault(cur + ":new_share", {})[club] = round(new / tot, 3) if tot else None
json.dump(dict(churn=out, names=names), open(sys.argv[2] if len(sys.argv) > 2 else "churn.json", "w"), indent=0)
cur = seasons[-1]
print("Churn into", cur)
for club, v in sorted(out[cur].items(), key=lambda x: -x[1]):
    print(f"  {names.get(club, club):<16} left {v:.0%}   new-player share of minutes this season {out[cur+':new_share'].get(club, 0):.0%}")
import statistics
allv = [v for s, d in out.items() if ':' not in s for v in d.values()]
print("all seasons: median", statistics.median(allv), "p90", sorted(allv)[int(len(allv)*.9)], "max", max(allv))
