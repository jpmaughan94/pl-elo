"""Match ratings: each player's impact in each Premier League match, out of 10.

For every appearance, each measure is compared with all appearances in the same position this
season (a z-score), the scores are weighted by position and added up, and the total is scaled so
that a typical 60+ minute game is a 6.5 and each standard deviation is 0.75 of a point (range 3-10).

  GK   goals prevented (xG faced minus goals conceded), BPS
  DEF  BPS, defensive actions, goals + assists, xG + xA
  MID  BPS, xG + xA, goals + assists, defensive actions
  FWD  xG + xA, goals + assists, BPS, defensive actions

Measures are match totals, not per 90, so playing longer gives more chance to make an impact.
Input: vaastav/Fantasy-Premier-League gws/merged_gw.csv (or the FPL API element-summary history).
"""
import csv, json, math, statistics, sys
from collections import defaultdict
from players import TEAM_NAMES

WEIGHTS = {
    "GK":  {"gp": 0.55, "bps": 0.45},
    "DEF": {"bps": 0.35, "dc": 0.30, "ga": 0.20, "xgi": 0.15},
    "MID": {"bps": 0.30, "xgi": 0.30, "ga": 0.25, "dc": 0.15},
    "FWD": {"xgi": 0.40, "ga": 0.35, "bps": 0.20, "dc": 0.05},
}
BASE, SCALE, CLIP = 6.5, 0.75, (3.0, 10.0)   # 0.75 of a point per standard deviation
FORM_HALF_LIFE = 3     # form: a game 3 gameweeks ago counts half
FORM_MIN_MINUTES = 30  # only games where he played 30+ minutes count towards form (cameos are judged by power)
FORM_MIN_GAMES = 3     # at least this many such games this season to have a form score


def weighted_median(pairs):
    """pairs = [(value, weight)]: the value where half the weight is above and half below."""
    pairs = sorted(pairs); half = sum(w for _, w in pairs) / 2; acc = 0.0
    for i, (v, w) in enumerate(pairs):
        acc += w
        if acc > half + 1e-12:
            return v
        if abs(acc - half) <= 1e-12:   # exactly half: average with the next value
            return (v + pairs[i + 1][0]) / 2
    return pairs[-1][0]


def form_scores(apps, code_of):
    """Form per player (FPL code): weighted median match rating of recent 30+ minute games."""
    gw_now = max(x["gw"] for x in apps)
    by = defaultdict(list)
    for x in apps:
        by[code_of.get(x["el"], x["el"])].append(x)
    out = {}
    for code, xs in by.items():
        xs.sort(key=lambda x: (x["gw"], x["kickoff"]))
        games = [x for x in xs if x["min"] >= FORM_MIN_MINUTES]
        if len(games) < FORM_MIN_GAMES:
            continue
        pairs = [(x["rating"], 0.5 ** ((gw_now - x["gw"]) / FORM_HALF_LIFE)) for x in games]
        out[code] = dict(form=round(weighted_median(pairs), 1), n=len(games),
                         mean=round(sum(r * w for r, w in pairs) / sum(w for _, w in pairs), 2),
                         last=[[x["gw"], x["rating"], x["min"]] for x in xs[-6:]])
    return out


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def build(merged_gw_path):
    rows = [r for r in csv.DictReader(open(merged_gw_path, encoding="utf-8", errors="replace")) if f(r["minutes"]) > 0]
    apps = []
    for r in rows:
        pos = r["position"] if r["position"] in WEIGHTS else "MID"
        g, a, xg, xa = f(r["goals_scored"]), f(r["assists"]), f(r["expected_goals"]), f(r["expected_assists"])
        apps.append(dict(gw=int(f(r["GW"])), el=r["element"], fixture=r["fixture"], kickoff=r["kickoff_time"], name=r["name"], team=TEAM_NAMES.get(r["team"], r["team"]),
                         home=r["was_home"] == "True", hs=int(f(r["team_h_score"])), as_=int(f(r["team_a_score"])),
                         pos=pos, min=int(f(r["minutes"])), g=int(g), a=int(a), xg=round(xg, 2), xa=round(xa, 2),
                         dc=int(f(r.get("defensive_contribution"))), saves=int(f(r["saves"])), bps=int(f(r["bps"])),
                         gc=int(f(r["goals_conceded"])), xgc=round(f(r["expected_goals_conceded"]), 2),
                         v=dict(xgi=xg + xa, ga=g + a, dc=f(r.get("defensive_contribution")), bps=f(r["bps"]),
                                gp=f(r["expected_goals_conceded"]) - f(r["goals_conceded"]))))
    stats = {}
    for pos, wts in WEIGHTS.items():
        grp = [x for x in apps if x["pos"] == pos]
        st = {}
        for k in wts:
            vals = [x["v"][k] for x in grp]
            m = sum(vals) / len(vals)
            sd = math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals)) or 1
            st[k] = (m, sd)
        for x in grp:
            x["z"] = {k: (x["v"][k] - st[k][0]) / st[k][1] for k in wts}
            x["c"] = sum(wts[k] * x["z"][k] for k in wts)
        full = [x["c"] for x in grp if x["min"] >= 60]
        med, sd = statistics.median(full), statistics.pstdev(full)
        for x in grp:
            x["rating"] = round(min(CLIP[1], max(CLIP[0], BASE + SCALE * (x["c"] - med) / sd)), 1)
        stats[pos] = dict(n=len(grp), median=round(med, 3), sd=round(sd, 3),
                          measures={k: dict(avg=round(st[k][0], 3), sd=round(st[k][1], 3), weight=wts[k]) for k in wts})

    fixtures = defaultdict(list)
    for x in apps:
        fixtures[x["fixture"]].append(x)
    out = []
    for fid, xs in fixtures.items():
        h = next(x for x in xs if x["home"])["team"]
        a = next(x for x in xs if not x["home"])["team"]
        xs.sort(key=lambda x: -x["rating"])
        out.append(dict(id=fid, date=xs[0]["kickoff"][:10], home=h, away=a, hs=xs[0]["hs"], as_=xs[0]["as_"],
                        p=[[x["name"], 1 if x["home"] else 0, x["pos"], x["min"], x["g"], x["a"], x["xg"], x["xa"],
                            x["dc"], x["saves"], x["bps"], x["rating"], {k: round(v, 2) for k, v in x["z"].items()},
                            round(x["xgc"] - x["gc"], 2)] for x in xs]))
    out.sort(key=lambda m: (m["date"], m["home"]))
    import os
    pr = os.path.join(os.path.dirname(merged_gw_path), "players_raw.csv")
    code_of = {r["id"]: r["code"] for r in csv.DictReader(open(pr, encoding="utf-8", errors="replace"))} if os.path.exists(pr) else {}
    form = form_scores(apps, code_of)
    return dict(fixtures=out, stats=stats, base=BASE, scale=SCALE, form=form,
                form_settings=dict(half_life=FORM_HALF_LIFE, min_minutes=FORM_MIN_MINUTES, min_games=FORM_MIN_GAMES))


if __name__ == "__main__":
    o = build(sys.argv[1] if len(sys.argv) > 1 else "fpl/2026-27/merged_gw.csv")
    o["as_of"] = sys.argv[2] if len(sys.argv) > 2 else None
    json.dump(o, open("matches.json", "w"), separators=(",", ":"), ensure_ascii=False)
    print(len(o["fixtures"]), "matches with player ratings")
