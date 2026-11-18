"""Premier League Elo, recomputed from the full match history on every run.

Inputs : openfootball en.1.<season>.json files (+ optional supplement CSVs)
Output : elo.json  (current table + per-match rating history for every club)

The whole history is rebuilt every time, so a run is idempotent: a missed or
repeated run can't throw the numbers off, and changing a setting below rewrites
the entire timeline consistently.
"""
import csv, glob, json, math, os, re, sys
from collections import defaultdict

PARAMS = {
    "start": 1500,      # league average
    "k": 20,            # how fast ratings move
    "home_adv": 65,     # Elo points added to the home side's rating
    "regress": 0.20,    # share of the gap to 1500 removed each summer
}

# 2009-10 final table, used to seed August 2010 so the first season isn't all 1500s
SEED_2009_10 = ["Chelsea", "Manchester United", "Arsenal", "Tottenham Hotspur",
                "Manchester City", "Aston Villa", "Liverpool", "Everton",
                "Birmingham City", "Blackburn Rovers", "Stoke City", "Fulham",
                "Sunderland", "Bolton Wanderers", "Wolverhampton Wanderers",
                "Wigan Athletic", "West Ham United"]
SEED_PROMOTED = 1410  # Newcastle, West Brom, Blackpool in 2010

ALIASES = {"Brighton": "Brighton & Hove Albion", "Bournemouth": "AFC Bournemouth",
           "Wolves": "Wolverhampton Wanderers", "Spurs": "Tottenham Hotspur",
           # Fantasy Premier League short names
           "Man City": "Manchester City", "Man Utd": "Manchester United", "Nott'm Forest": "Nottingham Forest",
           "Newcastle": "Newcastle United", "Leeds": "Leeds United", "West Ham": "West Ham United",
           "Hull": "Hull City", "Ipswich": "Ipswich Town", "Coventry": "Coventry City", "Leicester": "Leicester City",
           "Sheffield Utd": "Sheffield United", "Luton": "Luton Town", "Norwich": "Norwich City",
           "West Brom": "West Bromwich Albion", "Stoke": "Stoke City", "Swansea": "Swansea City",
           "Cardiff": "Cardiff City", "Huddersfield": "Huddersfield Town", "Birmingham": "Birmingham City",
           "Middlesbrough": "Middlesbrough", "Wolverhampton": "Wolverhampton Wanderers",
           # football-data.co.uk style names
           "Man United": "Manchester United", "Nottm Forest": "Nottingham Forest", "Tottenham": "Tottenham Hotspur",
           "QPR": "Queens Park Rangers", "Blackburn": "Blackburn Rovers", "Bolton": "Bolton Wanderers",
           "Wigan": "Wigan Athletic", "Blackpool": "Blackpool",
           # The Odds API
           "Brighton and Hove Albion": "Brighton & Hove Albion"}


def norm(name):
    n = re.sub(r"^AFC (?!Bournemouth)", "", name.strip())
    n = re.sub(r" (FC|AFC)$", "", n)
    if n == "Bournemouth":
        n = "AFC Bournemouth"
    return ALIASES.get(n, n)


def full_time(m):
    s = m.get("score")
    if isinstance(s, dict):
        s = s.get("ft")
    if isinstance(s, list) and len(s) == 2:
        return int(s[0]), int(s[1])
    return None


def load_matches(data_dir):
    matches = {}
    for path in sorted(glob.glob(os.path.join(data_dir, "en.1.*.json"))):
        season = os.path.basename(path)[5:12]
        for m in json.load(open(path))["matches"]:
            ft = full_time(m)
            if not ft:
                continue
            key = (m["date"], norm(m["team1"]), norm(m["team2"]))
            matches[key] = dict(date=m["date"], season=season, home=key[1], away=key[2], hg=ft[0], ag=ft[1])
    for path in sorted(glob.glob(os.path.join(data_dir, "supplement_*.csv"))):
        season = os.path.basename(path)[11:18]
        for r in csv.DictReader(open(path)):
            key = (r["date"], norm(r["home"]), norm(r["away"]))
            matches.setdefault(key, dict(date=r["date"], season=season, home=key[1], away=key[2],
                                         hg=int(r["hg"]), ag=int(r["ag"])))
    return sorted(matches.values(), key=lambda m: (m["date"], m["home"]))


def expected(r_home, r_away, p=PARAMS):
    return 1 / (1 + 10 ** (-(r_home + p["home_adv"] - r_away) / 400))


def margin_mult(gd):
    gd = abs(gd)
    return 1 if gd <= 1 else 1.5 if gd == 2 else (11 + gd) / 8


def run(matches, p=PARAMS):
    rating, history = {}, defaultdict(list)
    seasons = sorted({m["season"] for m in matches})
    by_season = defaultdict(list)
    for m in matches:
        by_season[m["season"]].append(m)

    prev_teams = set()
    for i, season in enumerate(seasons):
        games = by_season[season]
        teams = {m["home"] for m in games} | {m["away"] for m in games}
        start_date = games[0]["date"]
        if i == 0:
            for pos, t in enumerate(SEED_2009_10, 1):
                rating[t] = p["start"] + (10.5 - pos) * 12
            for t in teams - set(SEED_2009_10):
                rating[t] = SEED_PROMOTED
        else:
            relegated = prev_teams - teams
            promoted = teams - prev_teams
            rel_avg = sum(rating[t] for t in relegated) / len(relegated)
            for t in prev_teams & teams:
                rating[t] = rating[t] - p["regress"] * (rating[t] - p["start"])
            for t in promoted:
                rating[t] = rel_avg
        for t in teams:
            history[t].append(dict(date=start_date, r=rating[t], season=season, start=True))
        for m in games:
            h, a = m["home"], m["away"]
            e = expected(rating[h], rating[a], p)
            s = 1 if m["hg"] > m["ag"] else 0.5 if m["hg"] == m["ag"] else 0
            delta = p["k"] * margin_mult(m["hg"] - m["ag"]) * (s - e)
            rating[h] += delta
            rating[a] -= delta
            for t, opp, gf, ga, venue, d in ((h, a, m["hg"], m["ag"], "H", delta), (a, h, m["ag"], m["hg"], "A", -delta)):
                res = "W" if gf > ga else "D" if gf == ga else "L"
                history[t].append(dict(date=m["date"], r=rating[t], d=d, opp=opp, gf=gf, ga=ga,
                                       venue=venue, res=res, season=season))
        prev_teams = teams
    return rating, history, seasons, prev_teams


def build_output(matches, p=PARAMS):
    rating, history, seasons, current = run(matches, p)
    current_season = seasons[-1]
    last_date = max(m["date"] for m in matches)

    table = []
    for t in current:
        h = [x for x in history[t] if "res" in x and x["season"] == current_season]
        pts = sum(3 if x["res"] == "W" else 1 if x["res"] == "D" else 0 for x in h)
        gd = sum(x["gf"] - x["ga"] for x in h)
        start = next(x["r"] for x in history[t] if x.get("start") and x["season"] == current_season)
        last5 = h[-5:]
        table.append(dict(team=t, elo=round(rating[t], 1), played=len(h), pts=pts, gd=gd,
                          season_change=round(rating[t] - start, 1),
                          last5_change=round(sum(x["d"] for x in last5), 1),
                          form="".join(x["res"] for x in last5),
                          last=[dict(date=x["date"], opp=x["opp"], venue=x["venue"], gf=x["gf"], ga=x["ga"],
                                     d=round(x["d"], 1)) for x in reversed(last5)]))
    table.sort(key=lambda r: -r["elo"])
    for i, r in enumerate(table, 1):
        r["rank"] = i
    lg = sorted(table, key=lambda r: (-r["pts"], -r["gd"], r["team"]))
    for i, r in enumerate(lg, 1):
        r["league_pos"] = i

    # Compact history: per club, list of [date, rating, opp, gf, ga, venue]; season starts flagged with opp=None
    hist = {t: [[x["date"], round(x["r"], 1), x.get("opp"), x.get("gf"), x.get("ga"), x.get("venue")] for x in hs]
            for t, hs in history.items()}
    peaks = []
    for t, hs in history.items():
        best = max(hs, key=lambda x: x["r"])
        peaks.append(dict(team=t, elo=round(best["r"], 1), date=best["date"]))
    peaks.sort(key=lambda x: -x["elo"])

    return dict(updated=last_date, season=current_season, params=p, n_matches=len(matches),
                seasons=seasons, table=table, history=hist, peaks=peaks[:10])


if __name__ == "__main__":
    data_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    out = sys.argv[2] if len(sys.argv) > 2 else "elo.json"
    ms = load_matches(data_dir)
    o = build_output(ms)
    json.dump(o, open(out, "w"), separators=(",", ":"))
    print(f"{len(ms)} matches, last {o['updated']}, wrote {out} ({os.path.getsize(out)//1024} KB)")
