"""Attack/defence ratings with uncertainty (a dynamic Poisson model, updated match by match).

Each club has two numbers, each with its own uncertainty (variance):
  attack a  : how many goals it scores against an average side
  defence d : how few it concedes (higher = better)
Goals:  home ~ Poisson(exp(mu + h + a_home - d_away)),  away ~ Poisson(exp(mu + a_away - d_home))

After every match the ratings move by (goals - expected goals), weighted by how unsure we are
(an extended Kalman filter step for a Poisson count). The less certain a rating, the more one
match moves it, and every match makes it more certain.

Uncertainty grows:
  * a little every day (form drifts),
  * a lot each summer, scaled by squad churn: the share of last season's league minutes
    played by players who have since left the club,
  * most for promoted clubs, which we have no Premier League evidence on.
Summer also pulls each rating towards average, more strongly for high-churn squads.

The settings (drift, summer reset, churn effect) are chosen by one-step-ahead predictive
log-likelihood: how well the model predicts each match before it is played.
"""
import json, math, sys
from collections import defaultdict
import elo

FPL_CODE_NAMES = {"110": "Stoke City", "80": "Swansea City", "25": "Middlesbrough",
                  "38": "Huddersfield Town", "97": "Cardiff City"}
FPL_TO_OURS = {"Man City": "Manchester City", "Man Utd": "Manchester United", "Spurs": "Tottenham Hotspur",
               "Nott'm Forest": "Nottingham Forest", "Newcastle": "Newcastle United", "Leeds": "Leeds United",
               "Brighton": "Brighton & Hove Albion", "Wolves": "Wolverhampton Wanderers",
               "Sheffield Utd": "Sheffield United", "West Brom": "West Bromwich Albion", "Luton": "Luton Town",
               "Ipswich": "Ipswich Town", "Leicester": "Leicester City", "Norwich": "Norwich City",
               "Bournemouth": "AFC Bournemouth", "West Ham": "West Ham United", "Burnley": "Burnley",
               "Hull": "Hull City", "Sunderland": "Sunderland", "Watford": "Watford", "Southampton": "Southampton"}

DEFAULTS = dict(
    var0=0.04,          # starting variance in 2010
    q_day=4e-5,         # daily drift variance
    summer_var=0.006,   # summer variance added for an average squad (churn = median)
    churn_var=0.06,     # extra summer variance per unit of churn above zero
    summer_shrink=0.15, # share of each rating pulled to average each summer ...
    churn_shrink=0.8,   # ... plus this much per unit of churn
    promoted_var=0.03,  # summer variance for promoted clubs
)


def load_churn(path):
    c = json.load(open(path))
    names = {**c["names"], **FPL_CODE_NAMES}
    out = {}
    for season, d in c["churn"].items():
        key = season.split(":")[0]
        field = "new_share" if ":" in season else "left"
        for code, v in d.items():
            n = names.get(code, code)
            n = FPL_TO_OURS.get(n, n)
            out.setdefault(key, {}).setdefault(n, {})[field] = v
    return out


def run(matches, churn, p=DEFAULTS, record=False, preds=None):
    """preds: optional list; each match's pre-match expected goals (home, away) are appended to it."""
    seasons = sorted({m["season"] for m in matches})
    by_season = defaultdict(list)
    for m in matches:
        by_season[m["season"]].append(m)
    # league goal rates (fixed over the whole history)
    hg = sum(m["hg"] for m in matches) / len(matches)
    ag = sum(m["ag"] for m in matches) / len(matches)
    mu, h = math.log(ag), math.log(hg / ag)
    med_churn = 0.111

    a, d, Pa, Pd, last = {}, {}, {}, {}, {}
    hist = defaultdict(list)
    ll, n_ll = 0.0, 0
    prev = set()
    for si, season in enumerate(seasons):
        games = by_season[season]
        teams = {m["home"] for m in games} | {m["away"] for m in games}
        start = games[0]["date"]
        if si == 0:
            for t in teams:
                a[t] = d[t] = 0.0
                Pa[t] = Pd[t] = p["var0"]
        else:
            rel = prev - teams
            ra = sum(a[t] for t in rel) / len(rel)
            rd = sum(d[t] for t in rel) / len(rel)
            for t in teams:
                if t in prev:
                    ch = churn.get(season, {}).get(t, {}).get("left", med_churn)
                    s = min(0.9, p["summer_shrink"] + p["churn_shrink"] * ch)
                    a[t] *= 1 - s
                    d[t] *= 1 - s
                    extra = p["summer_var"] + p["churn_var"] * max(0.0, ch - med_churn)
                    Pa[t] += extra
                    Pd[t] += extra
                else:
                    a[t], d[t] = ra, rd
                    Pa[t] = p["promoted_var"]
                    Pd[t] = Pa[t]
        for t in teams:
            last[t] = start
            if record:
                hist[t].append([start, a[t], d[t], Pa[t], Pd[t], None, None, None, None, season])
        mean_a = sum(a[t] for t in teams) / len(teams)
        mean_d = sum(d[t] for t in teams) / len(teams)
        for t in teams:  # keep the league centred so mu stays meaningful
            a[t] -= mean_a
            d[t] -= mean_d

        for m in games:
            H, A = m["home"], m["away"]
            for t in (H, A):  # daily drift since the team's last match
                gap = max(0, (elo_day(m["date"]) - elo_day(last[t])))
                Pa[t] += p["q_day"] * gap
                Pd[t] += p["q_day"] * gap
                last[t] = m["date"]
            if preds is not None:
                eh = mu + h + a[H] - d[A] + (Pa[H] + Pd[A]) / 2
                ea = mu + a[A] - d[H] + (Pa[A] + Pd[H]) / 2
                preds.append((m, math.exp(eh), math.exp(ea)))
            for att, dfn, y, base in ((H, A, m["hg"], mu + h), (A, H, m["ag"], mu)):
                eta = base + a[att] - d[dfn]
                S = Pa[att] + Pd[dfn]
                lam = math.exp(eta + S / 2)        # predictive mean (log-normal correction)
                ll += y * math.log(lam) - lam - math.lgamma(y + 1)
                n_ll += 1
                lam0 = math.exp(eta)
                g = (y - lam0) / (1 + S * lam0)
                ka, kd = Pa[att], Pd[dfn]
                a[att] += ka * g
                d[dfn] -= kd * g
                Pa[att] -= ka * ka * lam0 / (1 + S * lam0)
                Pd[dfn] -= kd * kd * lam0 / (1 + S * lam0)
            if record:
                for t, opp, gf, ga in ((H, A, m["hg"], m["ag"]), (A, H, m["ag"], m["hg"])):
                    hist[t].append([m["date"], a[t], d[t], Pa[t], Pd[t], opp, gf, ga, "H" if t == H else "A", season])
        prev = teams
    return dict(mu=mu, h=h, a=a, d=d, Pa=Pa, Pd=Pd, ll=ll / n_ll, teams=prev, hist=hist, seasons=seasons)


_days = {}
def elo_day(s):
    if s not in _days:
        y, mth, dd = map(int, s.split("-"))
        _days[s] = (__import__("datetime").date(y, mth, dd) - __import__("datetime").date(2010, 1, 1)).days
    return _days[s]


def fit(matches, churn):
    """Coordinate search on predictive log-likelihood, scored on 2012-13 onwards (after burn-in)."""
    scored = [m for m in matches]
    best = dict(DEFAULTS)
    def score(pp):
        r = run(scored, churn, pp)
        return r["ll"]
    cur = score(best)
    grid = {"q_day": [1e-5, 2e-5, 4e-5, 8e-5, 1.5e-4],
            "summer_var": [0.0, 0.003, 0.006, 0.012, 0.02],
            "churn_var": [0.0, 0.02, 0.06, 0.12, 0.25],
            "summer_shrink": [0.0, 0.08, 0.15, 0.25],
            "churn_shrink": [0.0, 0.4, 0.8, 1.2],
            "promoted_var": [0.01, 0.03, 0.06, 0.1]}
    for _ in range(2):
        for k, vals in grid.items():
            for v in vals:
                trial = {**best, k: v}
                s = score(trial)
                if s > cur + 1e-7:
                    cur, best = s, trial
    return best, cur


def summarise(r, churn, p):
    mu, h = r["mu"], r["h"]
    base = mu + h / 2   # neutral venue
    z = 1.2816          # 80% interval
    cur_season = r["seasons"][-1]
    rows = []
    for t in r["teams"]:
        a, d, Pa, Pd = r["a"][t], r["d"][t], r["Pa"][t], r["Pd"][t]
        gf = math.exp(base + a)
        ga = math.exp(base - d)
        sa, sd = math.sqrt(Pa), math.sqrt(Pd)
        # net goal difference per game vs an average side; sd by the delta method
        net = gf - ga
        net_sd = math.sqrt((gf * sa) ** 2 + (ga * sd) ** 2)
        ch = churn.get(cur_season, {}).get(t, {})
        rows.append(dict(team=t, gf=round(gf, 2), gf_lo=round(math.exp(base + a - z * sa), 2), gf_hi=round(math.exp(base + a + z * sa), 2),
                         ga=round(ga, 2), ga_lo=round(math.exp(base - d - z * sd), 2), ga_hi=round(math.exp(base - d + z * sd), 2),
                         net=round(net, 2), net_lo=round(net - z * net_sd, 2), net_hi=round(net + z * net_sd, 2),
                         unc=round(z * net_sd, 2), churn=ch.get("left"), new_share=ch.get("new_share")))
    rows.sort(key=lambda x: -x["net"])
    return rows


def churn_finding(r, churn, rows):
    """Do high-turnover squads change more over a season? Compare thirds of club-seasons by churn."""
    base = r["mu"] + r["h"] / 2
    net = lambda e: math.exp(base + e[1]) - math.exp(base - e[2])
    data = []
    for t, hs in r["hist"].items():
        by = defaultdict(list)
        for e in hs:
            by[e[9]].append(e)
        for s, es in by.items():
            ch = churn.get(s, {}).get(t, {}).get("left")
            if ch is None or len(es) < 30 or s == r["seasons"][-1]:
                continue
            data.append((ch, abs(net(es[-1]) - net(es[0]))))
    data.sort()
    q = len(data) // 3
    lo, hi = data[:q], data[-q:]
    mean = lambda xs: sum(xs) / len(xs)
    by_team = {x["team"]: x for x in rows}
    nu, ars = by_team.get("Newcastle United"), by_team.get("Arsenal")
    top = sorted((x for x in rows if x["churn"] is not None), key=lambda x: -x["churn"])[:3]
    example = ""
    if nu and ars:
        example = (f"Newcastle lost {round(nu['churn']*100)}% of last season’s minutes "
                   f"(the most: {', '.join(f'{short_name(x['team'])} {round(x['churn']*100)}%' for x in top)}). "
                   f"Their range now is ±{nu['unc']:.2f} goals a game, against Arsenal’s ±{ars['unc']:.2f}.")
    return dict(n=len(data), low_churn=round(mean([c for c, _ in lo]), 3), high_churn=round(mean([c for c, _ in hi]), 3),
                low_move=round(mean([m for _, m in lo]), 3), high_move=round(mean([m for _, m in hi]), 3), example=example)


def short_name(t):
    return {"Aston Villa": "Villa", "Newcastle United": "Newcastle", "Brighton & Hove Albion": "Brighton"}.get(t, t)


if __name__ == "__main__":
    matches = elo.load_matches(".")
    churn = load_churn("churn.json")
    best, ll = fit(matches, churn)
    # refit the summer settings with churn switched off, for a fair comparison
    p_nc = dict(best, churn_var=0.0, churn_shrink=0.0)
    for k, vals in {"summer_var": [0.0, 0.003, 0.006, 0.012, 0.02, 0.03], "summer_shrink": [0.0, 0.08, 0.15, 0.25, 0.35]}.items():
        bestv = None
        for v in vals:
            s = run(matches, churn, {**p_nc, k: v})["ll"]
            if bestv is None or s > bestv[0]:
                bestv = (s, v)
        p_nc[k] = bestv[1]
    ll_nc = run(matches, churn, p_nc)["ll"]
    print("fitted:", best)
    print(f"log-lik per goal count: with churn {ll:.5f}, without churn {ll_nc:.5f}, gain {ll - ll_nc:+.5f} per observation, x{len(matches)*2} obs = {(ll-ll_nc)*len(matches)*2:+.1f} total")
    r = run(matches, churn, best, record=True)
    rows = summarise(r, churn, best)
    for x in rows[:0]:
        print(f"{x['team']:<26} GF {x['gf']:.2f} ({x['gf_lo']:.2f}-{x['gf_hi']:.2f})  GA {x['ga']:.2f} ({x['ga_lo']:.2f}-{x['ga_hi']:.2f})  net {x['net']:+.2f} ±{x['unc']:.2f}  churn {x['churn']}")
    finding = churn_finding(r, churn, rows)
    print(finding)
    json.dump(dict(params=best, ll=ll, ll_no_churn=ll_nc, mu=r["mu"], h=r["h"], rows=rows, finding=finding,
                   hist={t: [[e[0], round(e[1], 4), round(e[2], 4), round(e[3], 5), round(e[4], 5), e[9]] for e in hs] for t, hs in r["hist"].items()}),
              open("attdef.json", "w"), separators=(",", ":"))
