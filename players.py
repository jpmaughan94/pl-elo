"""Player power rankings v1: who matters most to each Premier League squad.

  power = minutes share x quality

minutes share : share of the team's league minutes this season the player has been on the pitch
quality       : percentile (0-100) against players in the same position, from per-90 numbers
                that blend this season with last season and are pulled towards the position
                average when there are few minutes to go on:
                  GK   goals prevented (xG faced minus goals conceded), team xG conceded, BPS
                  DEF  defensive actions, xG conceded with him on the pitch, xG + xA, BPS
                  MID  xG + xA, BPS, defensive actions
                  FWD  xG + xA, BPS, defensive actions

Every intermediate number is exported so the page can show the working.

Input: FPL "elements" season totals for this season and last season. That is players_raw.csv from
vaastav/Fantasy-Premier-League, or the `elements` list from the live FPL API
(https://fantasy.premierleague.com/api/bootstrap-static/), which has the same fields.
"""
import csv, json, math, sys
from collections import defaultdict

POS = {"1": "GK", "2": "DEF", "3": "MID", "4": "FWD"}
LAST_SEASON_WEIGHT = 0.5     # a minute last season counts half as much as a minute this season
PRIOR_90S = 5                # pull towards the position average with the weight of 5 full games
QUALIFY_MIN = 450            # blended minutes needed to be in the comparison group
WEIGHTS = {
    "GK":  {"gp": 0.45, "xgc": 0.30, "bps": 0.25},
    "DEF": {"dc": 0.30, "xgc": 0.25, "xgi": 0.25, "bps": 0.20},
    "MID": {"xgi": 0.45, "bps": 0.30, "dc": 0.25},
    "FWD": {"xgi": 0.65, "bps": 0.20, "dc": 0.15},
}
SIGN = {"gp": 1, "xgc": -1, "xgi": 1, "dc": 1, "bps": 1}   # lower xG conceded is better
TEAM_NAMES = {  # FPL short names -> names used in the Elo data
    "Man City": "Manchester City", "Man Utd": "Manchester United", "Spurs": "Tottenham Hotspur",
    "Nott'm Forest": "Nottingham Forest", "Newcastle": "Newcastle United", "Leeds": "Leeds United",
    "Brighton": "Brighton & Hove Albion", "Wolves": "Wolverhampton Wanderers", "Bournemouth": "AFC Bournemouth",
    "West Ham": "West Ham United", "Hull": "Hull City", "Ipswich": "Ipswich Town", "Coventry": "Coventry City",
    "Leicester": "Leicester City", "Sheffield Utd": "Sheffield United", "Luton": "Luton Town",
    "Norwich": "Norwich City", "West Brom": "West Bromwich Albion",
}
LEFT_WORDS = ("joined", "loan", "left the club", "transferred", "departed")


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def load_elements(path):
    if path.endswith(".json"):
        return json.load(open(path))["elements"]
    return list(csv.DictReader(open(path, encoding="utf-8", errors="replace")))


def totals(e):
    """Season totals for the measures used, plus a few for display."""
    xgc, gc = num(e["expected_goals_conceded"]), num(e["goals_conceded"])
    return dict(min=num(e["minutes"]), starts=num(e["starts"]),
                xgi=num(e["expected_goal_involvements"]), xgc=xgc, gp=xgc - gc,
                dc=num(e.get("defensive_contribution")), bps=num(e["bps"]),
                goals=num(e["goals_scored"]), assists=num(e["assists"]), saves=num(e["saves"]))


def per90(t, k):
    return round(t[k] / (t["min"] / 90), 3) if t["min"] >= 90 else None


def build(cur_elements, prev_elements, team_names):
    prev = {str(e["code"]): totals(e) for e in prev_elements}
    zero = {k: 0.0 for k in totals(cur_elements[0])}
    players = []
    for e in cur_elements:
        pos = POS.get(str(e["element_type"]))
        if not pos:
            continue
        cur = totals(e)
        prv = prev.get(str(e["code"]), zero)
        blend = {k: cur[k] + LAST_SEASON_WEIGHT * prv[k] for k in cur}
        players.append(dict(e=e, pos=pos, cur=cur, prev=prv, blend=blend))

    # 1. position average rate for each measure (pooled over everyone in the comparison group)
    pos_avg, ref = {}, {}
    for pos, wts in WEIGHTS.items():
        ref[pos] = [pl for pl in players if pl["pos"] == pos and pl["blend"]["min"] >= QUALIFY_MIN]
        n90 = sum(pl["blend"]["min"] for pl in ref[pos]) / 90
        pos_avg[pos] = {k: sum(pl["blend"][k] for pl in ref[pos]) / n90 for k in wts}

    # 2. rate used = (this season + half last season + 5 games at the position average) / (90s played + 5)
    for pl in players:
        n90 = pl["blend"]["min"] / 90
        pl["used"] = {k: (pl["blend"][k] + pos_avg[pl["pos"]][k] * PRIOR_90S) / (n90 + PRIOR_90S)
                      for k in WEIGHTS[pl["pos"]]}

    # 3. score each measure against the comparison group (z-score), weight, add up, turn into a percentile
    pos_stats = {}
    for pos, wts in WEIGHTS.items():
        stats = {}
        for k in wts:
            vals = [pl["used"][k] for pl in ref[pos]]
            m = sum(vals) / len(vals)
            sd = math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals)) or 1
            stats[k] = (m, sd)
        pos_stats[pos] = dict(n=len(ref[pos]), measures={k: dict(avg=round(stats[k][0], 3), sd=round(stats[k][1], 3),
                                                                  weight=wts[k], sign=SIGN[k]) for k in wts})
        for pl in [p for p in players if p["pos"] == pos]:
            pl["z"] = {k: SIGN[k] * (pl["used"][k] - stats[k][0]) / stats[k][1] for k in wts}
            pl["q"] = sum(wts[k] * pl["z"][k] for k in wts)
        refq = sorted(pl["q"] for pl in ref[pos])
        for pl in [p for p in players if p["pos"] == pos]:
            pl["pct"] = round(100 * sum(1 for q in refq if q < pl["q"]) / len(refq))

    # 4. minutes share: team games this season = team starts / 11 (every match has 11 starters)
    starts = defaultdict(float)
    for pl in players:
        starts[str(pl["e"]["team_code"])] += pl["cur"]["starts"]
    games = {k: round(v / 11) for k, v in starts.items()}

    by_team = defaultdict(list)
    for pl in players:
        e, tc = pl["e"], str(pl["e"]["team_code"])
        status, news = e.get("status", "a"), (e.get("news") or "").strip()
        if status == "u" and any(s in news.lower() for s in LEFT_WORDS):
            continue  # no longer at the club
        g = games.get(tc, 0)
        share = min(1.0, pl["cur"]["min"] / (90 * g)) if g else 0.0
        by_team[team_names.get(tc, tc)].append(dict(
            id=str(e["code"]), name=e.get("web_name"), pos=pl["pos"],
            power=round(share * pl["pct"], 1), share=round(share, 3), quality=pl["pct"], q=round(pl["q"], 3),
            minutes=int(pl["cur"]["min"]), starts=int(pl["cur"]["starts"]), prev_min=int(pl["prev"]["min"]),
            goals=int(pl["cur"]["goals"]), assists=int(pl["cur"]["assists"]),
            # per measure: [this season per 90, last season per 90, rate used, score vs position]
            m={k: [per90(pl["cur"], k), per90(pl["prev"], k), round(pl["used"][k], 3), round(pl["z"][k], 2)]
               for k in WEIGHTS[pl["pos"]]},
            status=status, news=news, chance=None if str(e.get("chance_of_playing_next_round")) in ("", "None") else int(num(e.get("chance_of_playing_next_round")))))
    for t in by_team:
        by_team[t].sort(key=lambda x: (-x["power"], -x["minutes"], -x["prev_min"]))
    team_games = {team_names.get(k, k): v for k, v in games.items()}
    return dict(teams=by_team, games=team_games, pos_stats=pos_stats,
                settings=dict(last_season_weight=LAST_SEASON_WEIGHT, prior_90s=PRIOR_90S, qualify_min=QUALIFY_MIN))


def team_names_from(path):
    return {str(r["code"]): TEAM_NAMES.get(r["name"], r["name"]) for r in csv.DictReader(open(path))}


if __name__ == "__main__":
    import glob, os
    base = sys.argv[1] if len(sys.argv) > 1 else "fpl"
    seasons = sorted(p for p in glob.glob(f"{base}/20*") if os.path.exists(f"{p}/players_raw.csv"))
    prev, cur = seasons[-2], seasons[-1]
    names = team_names_from(f"{cur}/teams.csv")
    out = build(load_elements(f"{cur}/players_raw.csv"), load_elements(f"{prev}/players_raw.csv"), names)
    out["as_of"] = sys.argv[2] if len(sys.argv) > 2 else None
    json.dump(out, open("players.json", "w"), separators=(",", ":"))
    print({p: v["n"] for p, v in out["pos_stats"].items()}, "in comparison groups")
    print(f"players: {sum(len(v) for v in out['teams'].values())} in {len(out['teams'])} squads ({os.path.basename(cur)})")
