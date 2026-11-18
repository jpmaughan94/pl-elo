"""Player power rankings v1: who matters most to each Premier League squad.

  power = minutes share x quality

minutes share : share of the team's league games this season the player has played, counted per
                appearance: a start of FULL_GAME_MIN+ minutes is a full game (being taken off
                at 85 minutes in a won game is not a sign of being less important), a shorter
                start counts minutes / FULL_GAME_MIN, and a sub appearance counts minutes / 90
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
LAST_SEASON_WEIGHT = 0.5     # fallback only (no match-by-match data): last season's minutes count half
# Recency (chosen by backtest, 18 Nov 2026: predicting each player's next 5 gameweeks, 2025-26 and 2026-27)
QUALITY_HALF_LIFE = 24       # a game 24 gameweeks ago counts half towards quality. Shorter was worse:
QUALITY_SUMMER_GAP = 0       #   form over a few weeks is mostly noise, so quality needs a long memory
PLAYED_HALF_LIFE = 1         # the last game counts fully, the one before half, and so on: who is in the
PLAYED_SUMMER_GAP = 10       #   team changes fast. The summer counts as 10 extra gameweeks
PLAYED_LONG_HALF_LIFE = 12   # ...steadied by his longer-run record (half-life 12), which counts as
PLAYED_LONG_WEIGHT = 2       #   2 extra games. About 14.5% better than this season's plain average
                             #   (half-life 3: 9.5%; 0.5: 15.9%, but that is close to "last game only")
PRIOR_90S = 5                # pull towards the position average with the weight of 5 full games
FULL_GAME_MIN = 70           # a start of this many minutes or more counts as a full game
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


def credits(gw_rows):
    """Games counted per player (FPL element id) from per-match rows (merged_gw.csv)."""
    out = defaultdict(lambda: dict(full=0, short=0, short_c=0.0, subs=0, subs_c=0.0))
    for r in gw_rows:
        m = num(r["minutes"])
        if m <= 0:
            continue
        c = out[str(r["element"])]
        if num(r["starts"]) >= 1:
            if m >= FULL_GAME_MIN: c["full"] += 1
            else: c["short"] += 1; c["short_c"] += m / FULL_GAME_MIN
        else:
            c["subs"] += 1; c["subs_c"] += m / 90
    return out


def history_rows(seasons):
    """Per-match rows for several seasons, oldest first: [(players_raw elements, merged_gw rows), ...].
    Returns normalised rows keyed by the FPL player code (stable across seasons)."""
    out = []
    for si, (elements, gw_rows) in enumerate(seasons):
        code = {str(e["id"]): str(e["code"]) for e in elements}
        for r in gw_rows or []:
            c = code.get(str(r["element"]))
            if c is None:
                continue
            dc = r.get("defensive_contribution")
            m, st = num(r["minutes"]), num(r["starts"])
            xgc = num(r["expected_goals_conceded"])
            out.append(dict(code=c, s=si, gw=int(num(r["GW"])), min=m,
                            credit=0.0 if m <= 0 else min(1.0, m / FULL_GAME_MIN) if st >= 1 else m / 90,
                            xgi=num(r["expected_goal_involvements"]), xgc=xgc, gp=xgc - num(r["goals_conceded"]),
                            bps=num(r["bps"]), dc=None if dc in (None, "") else num(dc)))
    return out


def decayed(rows, s_now, gw_now):
    """Recency-weighted sums per player code: quality sums (half-life QUALITY_HALF_LIFE) and the
    games-played share (half-life PLAYED_HALF_LIFE)."""
    t = lambda s, g, gap: s * (38 + gap) + g
    out = defaultdict(lambda: dict(min=0.0, min_dc=0.0, xgi=0.0, xgc=0.0, gp=0.0, bps=0.0, dc=0.0, pw=0.0, pc=0.0, lw=0.0, lc=0.0))
    for r in rows:
        d = out[r["code"]]
        wq = 0.5 ** ((t(s_now, gw_now, QUALITY_SUMMER_GAP) - t(r["s"], r["gw"], QUALITY_SUMMER_GAP)) / QUALITY_HALF_LIFE)
        wp = 0.5 ** ((t(s_now, gw_now, PLAYED_SUMMER_GAP) - t(r["s"], r["gw"], PLAYED_SUMMER_GAP)) / PLAYED_HALF_LIFE)
        d["min"] += wq * r["min"]
        for k in ("xgi", "xgc", "gp", "bps"):
            d[k] += wq * r[k]
        if r["dc"] is not None:   # defensive contributions are only recorded from 2025-26
            d["dc"] += wq * r["dc"]; d["min_dc"] += wq * r["min"]
        wl = 0.5 ** ((t(s_now, gw_now, PLAYED_SUMMER_GAP) - t(r["s"], r["gw"], PLAYED_SUMMER_GAP)) / PLAYED_LONG_HALF_LIFE)
        d["pw"] += wp; d["pc"] += wp * r["credit"]; d["lw"] += wl; d["lc"] += wl * r["credit"]
    for d in out.values():
        long_run = (d["lc"] + 0.35) / (d["lw"] + 1)   # 0.35 = a typical squad player, for anyone with little history
        d["share"] = (d["pc"] + PLAYED_LONG_WEIGHT * long_run) / (d["pw"] + PLAYED_LONG_WEIGHT)
        d["long_run"] = long_run
    return out


def build(cur_elements, prev_elements, team_names, gw_rows=None, history=None):
    """history: normalised rows from history_rows() for every season up to and including this one.
    With it, quality and games played are recency-weighted; without it, the older season blend is used."""
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
        blend["min_dc"] = blend["min"]
        players.append(dict(e=e, pos=pos, cur=cur, prev=prv, blend=blend))
    dec = None
    if history:
        s_now = max(r["s"] for r in history)
        gw_now = max(r["gw"] for r in history if r["s"] == s_now)
        dec = decayed(history, s_now, gw_now)
        last5 = defaultdict(list)   # credit in each of his last 5 matches this season, oldest first
        for r in history:
            if r["s"] == s_now:
                last5[r["code"]].append(round(r["credit"], 2))
        last5 = {k: v[-5:] for k, v in last5.items()}
        empty = dict(min=0.0, min_dc=0.0, xgi=0.0, xgc=0.0, gp=0.0, bps=0.0, dc=0.0, pw=0.0, pc=0.0, lw=0.0, lc=0.0)
        for pl in players:
            pl["blend"] = dict(dec.get(str(pl["e"]["code"]), empty))
    n90_of = lambda b, k: (b["min_dc"] if k == "dc" else b["min"]) / 90

    # 1. position average rate for each measure (pooled over everyone in the comparison group)
    pos_avg, ref = {}, {}
    for pos, wts in WEIGHTS.items():
        ref[pos] = [pl for pl in players if pl["pos"] == pos and pl["blend"]["min"] >= QUALIFY_MIN]
        pos_avg[pos] = {k: sum(pl["blend"][k] for pl in ref[pos]) / sum(n90_of(pl["blend"], k) for pl in ref[pos]) for k in wts}

    # 2. rate used = (this season + half last season + 5 games at the position average) / (90s played + 5)
    for pl in players:
        pl["used"] = {k: (pl["blend"][k] + pos_avg[pl["pos"]][k] * PRIOR_90S) / (n90_of(pl["blend"], k) + PRIOR_90S)
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

    cred = credits(gw_rows) if gw_rows else None
    by_team = defaultdict(list)
    for pl in players:
        e, tc = pl["e"], str(pl["e"]["team_code"])
        status, news = e.get("status", "a"), (e.get("news") or "").strip()
        if status == "u" and any(s in news.lower() for s in LEFT_WORDS):
            continue  # no longer at the club
        g = games.get(tc, 0)
        c = cred.get(str(e["id"])) if cred is not None else None
        b = pl["blend"]
        if dec is not None and b.get("pw"):   # recency-weighted share of his team's games
            share = b["share"]
        elif c is not None:
            counted = c["full"] + c["short_c"] + c["subs_c"]
            share = min(1.0, counted / g) if g else 0.0
        else:  # no per-match data: fall back to plain minutes
            share = min(1.0, pl["cur"]["min"] / (90 * g)) if g else 0.0
        season_share = min(1.0, (c["full"] + c["short_c"] + c["subs_c"]) / g) if (c is not None and g) else None
        by_team[team_names.get(tc, tc)].append(dict(
            id=str(e["code"]), name=e.get("web_name"), pos=pl["pos"],
            power=round(share * pl["pct"], 1), share=round(share, 3), quality=pl["pct"], q=round(pl["q"], 3),
            minutes=int(pl["cur"]["min"]), starts=int(pl["cur"]["starts"]), prev_min=int(pl["prev"]["min"]),
            season_share=None if season_share is None else round(season_share, 3),
            last5=last5.get(str(e["code"])) if dec is not None else None,
            recent_share=round(b["pc"] / b["pw"], 3) if dec is not None and b.get("pw") else None,
            long_share=round(b["long_run"], 3) if dec is not None and b.get("pw") else None,
            apps=None if c is None else [c["full"], c["short"], round(c["short_c"], 2), c["subs"], round(c["subs_c"], 2)],
            goals=int(pl["cur"]["goals"]), assists=int(pl["cur"]["assists"]),
            # per measure: [this season per 90, last season per 90, rate used, score vs position]
            m={k: [per90(pl["cur"], k), per90(pl["prev"], k), round(pl["used"][k], 3), round(pl["z"][k], 2)]
               for k in WEIGHTS[pl["pos"]]},
            status=status, news=news, chance=None if str(e.get("chance_of_playing_next_round")) in ("", "None") else int(num(e.get("chance_of_playing_next_round")))))
    for t in by_team:
        by_team[t].sort(key=lambda x: (-x["power"], -x["minutes"], -x["prev_min"]))
    team_games = {team_names.get(k, k): v for k, v in games.items()}
    return dict(teams=by_team, games=team_games, pos_stats=pos_stats,
                settings=dict(full_game_min=FULL_GAME_MIN, recency=dec is not None, quality_half_life=QUALITY_HALF_LIFE, played_half_life=PLAYED_HALF_LIFE, played_long_half_life=PLAYED_LONG_HALF_LIFE, played_long_weight=PLAYED_LONG_WEIGHT, played_summer_gap=PLAYED_SUMMER_GAP, last_season_weight=LAST_SEASON_WEIGHT, prior_90s=PRIOR_90S, qualify_min=QUALIFY_MIN))


def team_names_from(path):
    return {str(r["code"]): TEAM_NAMES.get(r["name"], r["name"]) for r in csv.DictReader(open(path))}


if __name__ == "__main__":
    import glob, os
    base = sys.argv[1] if len(sys.argv) > 1 else "fpl"
    seasons = sorted(p for p in glob.glob(f"{base}/20*") if os.path.exists(f"{p}/players_raw.csv"))
    prev, cur = seasons[-2], seasons[-1]
    names = team_names_from(f"{cur}/teams.csv")
    read = lambda p: list(csv.DictReader(open(p, encoding="utf-8", errors="replace"))) if os.path.exists(p) else None
    gw_rows = read(f"{cur}/merged_gw.csv")
    # every season with match-by-match data, oldest first (the current one last)
    hist = [(load_elements(f"{d}/players_raw.csv"), read(f"{d}/merged_gw.csv")) for d in seasons if os.path.exists(f"{d}/merged_gw.csv")]
    history = history_rows(hist) if gw_rows and len(hist) >= 2 else None
    out = build(load_elements(f"{cur}/players_raw.csv"), load_elements(f"{prev}/players_raw.csv"), names, gw_rows, history)
    out["as_of"] = sys.argv[2] if len(sys.argv) > 2 else None
    json.dump(out, open("players.json", "w"), separators=(",", ":"))
    print({p: v["n"] for p, v in out["pos_stats"].items()}, "in comparison groups")
    print(f"players: {sum(len(v) for v in out['teams'].values())} in {len(out['teams'])} squads ({os.path.basename(cur)})")
