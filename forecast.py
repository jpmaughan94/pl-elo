"""Match forecasts: how Elo and the attack/defence model compare with the bookmakers, and a preview of the
next round.

Two models give win/draw/loss chances before every match:
  Elo      expected score E = 1 / (1 + 10^(-(R_home + 65 - R_away)/400));
           draw = c * (1 - (2E - 1)^2)  (draws are likeliest between evenly matched sides);
           home win = E - draw/2, away win = 1 - E - draw/2.  c is fitted on 2010-12 only.
  Goals    the attack/defence model's expected goals for each side, turned into every scoreline's
           probability with two independent Poisson distributions, then summed into win/draw/loss.
The market: Bet365's pre-match odds, turned into probabilities and scaled to add up to 100%
(removing the bookmaker's margin).

Backtest, 2012-13 onwards (after two seasons of warm-up), scored on:
  log loss   -log(probability given to what actually happened); lower is better
  Brier      squared error across the three outcomes; lower is better
  RPS        ranked probability score: like Brier, but a home win forecast is "closer" to a draw than an away win
plus calibration (when a model says 60%, does it happen 60% of the time?) and a simple betting test:
back any outcome where the model's chance x the best available price > 1.05, one unit a time.

  python forecast.py <data dir with en.1.*.json, supplement_*.csv, churn.json> <odds csv> [upcoming fixtures csv] [upcoming odds csv]
"""
import csv, datetime, json, math, os, sys
from collections import defaultdict
import elo, attdef

HFA = elo.PARAMS["home_adv"]
SCORE_FROM = "2012-13"


# ---------- probabilities ----------
def elo_expected(rh, ra):
    return 1 / (1 + 10 ** (-(rh + HFA - ra) / 400))


def elo_probs(rh, ra, c):
    e = elo_expected(rh, ra)
    pd = c * (1 - (2 * e - 1) ** 2)
    ph, pa = e - pd / 2, 1 - e - pd / 2
    ph, pa = max(ph, 0.001), max(pa, 0.001)
    s = ph + pd + pa
    return ph / s, pd / s, pa / s


def pois(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def score_matrix(lh, la, n=10):
    return [[pois(i, lh) * pois(j, la) for j in range(n + 1)] for i in range(n + 1)]


def goal_probs(lh, la):
    m = score_matrix(lh, la)
    ph = sum(m[i][j] for i in range(len(m)) for j in range(len(m)) if i > j)
    pd = sum(m[i][i] for i in range(len(m)))
    pa = 1 - ph - pd
    return ph, pd, pa


def market_probs(oh, od, oa):
    inv = [1 / oh, 1 / od, 1 / oa]
    s = sum(inv)
    return tuple(x / s for x in inv), s - 1   # probabilities, bookmaker margin


# ---------- scoring ----------
def logloss(p, k):
    return -math.log(max(p[k], 1e-9))


def brier(p, k):
    return sum((p[i] - (1 if i == k else 0)) ** 2 for i in range(3))


def rps(p, k):
    o = [1 if i == k else 0 for i in range(3)]
    c1, c2 = p[0] - o[0], (p[0] + p[1]) - (o[0] + o[1])
    return (c1 ** 2 + c2 ** 2) / 2


def season_of(date):
    y, m = int(date[:4]), int(date[5:7])
    y = y if m >= 8 else y - 1
    return f"{y}-{(y + 1) % 100:02d}"


def load_odds(path):
    out = {}
    for r in csv.DictReader(open(path)):
        try:
            oh, od, oa = float(r["OddHome"]), float(r["OddDraw"]), float(r["OddAway"])
        except (ValueError, KeyError):
            continue
        best = []
        for k in ("MaxHome", "MaxDraw", "MaxAway"):
            try:
                best.append(float(r[k]))
            except (ValueError, KeyError):
                best.append(None)
        key = (season_of(r["MatchDate"]), elo.norm(r["HomeTeam"]), elo.norm(r["AwayTeam"]))
        out[key] = dict(odds=(oh, od, oa), best=tuple(best))
    return out


# ---------- pre-match forecasts for every match ----------
def all_forecasts(matches, churn):
    _, history, _, _ = elo.run(matches)
    pre = {}
    for t, hs in history.items():
        for x in hs:
            if "res" in x and x["venue"] == "H":
                pre[(x["season"], t, x["opp"], x["date"])] = x["r"] - x["d"]
    away_pre = {}
    for t, hs in history.items():
        for x in hs:
            if "res" in x and x["venue"] == "A":
                away_pre[(x["season"], x["opp"], t, x["date"])] = x["r"] - x["d"]
    preds = []
    attdef.run(matches, churn, attdef.DEFAULTS, preds=preds)
    out = []
    for m, lh, la in preds:
        k = (m["season"], m["home"], m["away"], m["date"])
        res = 0 if m["hg"] > m["ag"] else 1 if m["hg"] == m["ag"] else 2
        out.append(dict(m=m, rh=pre[k], ra=away_pre[k], lh=lh, la=la, res=res))
    return out


def fit_draw_c(fc):
    train = [f for f in fc if f["m"]["season"] < SCORE_FROM]
    best = None
    for c in [x / 100 for x in range(15, 41)]:
        ll = sum(logloss(elo_probs(f["rh"], f["ra"], c), f["res"]) for f in train)
        if best is None or ll < best[0]:
            best = (ll, c)
    return best[1]


def backtest(fc, odds, c):
    rows = []
    for f in fc:
        m = f["m"]
        if m["season"] < SCORE_FROM:
            continue
        o = odds.get((m["season"], m["home"], m["away"]))
        if not o:
            continue
        mk, margin = market_probs(*o["odds"])
        rows.append(dict(season=m["season"], res=f["res"], elo=elo_probs(f["rh"], f["ra"], c),
                         goals=goal_probs(f["lh"], f["la"]), market=mk, margin=margin, best=o["best"]))
    models = ["elo", "goals", "market"]
    summary = {}
    for mod in models:
        n = len(rows)
        summary[mod] = dict(
            logloss=round(sum(logloss(r[mod], r["res"]) for r in rows) / n, 4),
            brier=round(sum(brier(r[mod], r["res"]) for r in rows) / n, 4),
            rps=round(sum(rps(r[mod], r["res"]) for r in rows) / n, 4),
            accuracy=round(sum(1 for r in rows if max(range(3), key=lambda i: r[mod][i]) == r["res"]) / n, 4))
    base = [sum(1 for r in rows if r["res"] == k) / len(rows) for k in range(3)]
    summary["base_rate"] = dict(logloss=round(-sum(math.log(base[r["res"]]) for r in rows) / len(rows), 4),
                                home=round(base[0], 3), draw=round(base[1], 3), away=round(base[2], 3))
    # by season (RPS)
    by_season = defaultdict(lambda: defaultdict(list))
    for r in rows:
        for mod in models:
            by_season[r["season"]][mod].append(rps(r[mod], r["res"]))
    seasons = [dict(season=s, n=len(v["elo"]), **{mod: round(sum(v[mod]) / len(v[mod]), 4) for mod in models})
               for s, v in sorted(by_season.items())]
    # calibration: every outcome forecast, binned by predicted chance
    calib = {}
    for mod in models:
        bins = defaultdict(lambda: [0, 0, 0.0])
        for r in rows:
            for k in range(3):
                b = min(9, int(r[mod][k] * 10))
                bins[b][0] += 1
                bins[b][1] += 1 if r["res"] == k else 0
                bins[b][2] += r[mod][k]
        calib[mod] = [dict(bin=b, n=v[0], predicted=round(v[2] / v[0], 3), actual=round(v[1] / v[0], 3))
                      for b, v in sorted(bins.items()) if v[0] >= 30]
    # betting test at the best available price
    bets = {}
    for mod in ("elo", "goals"):
        staked = ret = 0.0
        n = wins = 0
        for r in rows:
            for k in range(3):
                price = r["best"][k]
                if price and r[mod][k] * price > 1.05:
                    staked += 1
                    n += 1
                    if r["res"] == k:
                        ret += price
                        wins += 1
        bets[mod] = dict(bets=n, won=wins, roi=round((ret - staked) / staked, 4) if staked else None)
    # how often do the models disagree with the market by 10+ points on the home win?
    disagree = defaultdict(int)
    for r in rows:
        for mod in ("elo", "goals"):
            if abs(r[mod][0] - r["market"][0]) >= 0.10:
                disagree[mod] += 1
    return dict(n=len(rows), from_season=SCORE_FROM, draw_c=c, summary=summary, seasons=seasons, calibration=calib,
                bets=bets, disagree={k: round(v / len(rows), 3) for k, v in disagree.items()},
                avg_margin=round(sum(r["margin"] for r in rows) / len(rows), 4))


# ---------- next round ----------
def head_to_head(matches, home, away, n=5):
    games = [m for m in matches if {m["home"], m["away"]} == {home, away}]
    w = d = l = 0
    for m in games:
        gf, ga = (m["hg"], m["ag"]) if m["home"] == home else (m["ag"], m["hg"])
        w, d, l = w + (gf > ga), d + (gf == ga), l + (gf < ga)
    last = [dict(date=m["date"], home=m["home"], away=m["away"], hg=m["hg"], ag=m["ag"]) for m in games[-n:]][::-1]
    return dict(played=len(games), wins=w, draws=d, losses=l, last=last)


def previews(matches, churn, fixtures, upcoming_odds, c):
    ratings, _, _, _ = elo.run(matches)
    st = attdef.run(matches, churn, attdef.DEFAULTS)
    out = []
    for fx in fixtures:
        h, a = elo.norm(fx["home"]), elo.norm(fx["away"])
        if h not in ratings or a not in ratings or h not in st["a"] or a not in st["a"]:
            continue
        pe = elo_probs(ratings[h], ratings[a], c)
        lh = math.exp(st["mu"] + st["h"] + st["a"][h] - st["d"][a] + (st["Pa"][h] + st["Pd"][a]) / 2)
        la = math.exp(st["mu"] + st["a"][a] - st["d"][h] + (st["Pa"][a] + st["Pd"][h]) / 2)
        pg = goal_probs(lh, la)
        sm = score_matrix(lh, la, 6)
        scores = sorted(((sm[i][j], i, j) for i in range(7) for j in range(7)), reverse=True)[:5]
        o = upcoming_odds.get((h, a))
        mk = None
        if o:
            probs, margin = market_probs(*o["odds"])
            mk = dict(odds=o["odds"], probs=[round(x, 4) for x in probs], margin=round(margin, 4), source=o["source"])
        out.append(dict(date=fx["date"], time=fx.get("time"), home=h, away=a,
                        elo=dict(home=round(ratings[h], 1), away=round(ratings[a], 1), probs=[round(x, 4) for x in pe],
                                 expected=round(elo_expected(ratings[h], ratings[a]), 4)),
                        goals=dict(xg_home=round(lh, 2), xg_away=round(la, 2), probs=[round(x, 4) for x in pg],
                                   scores=[dict(h=i, a=j, p=round(p, 4)) for p, i, j in scores]),
                        market=mk, h2h=head_to_head(matches, h, a)))
    return out


def next_round_from_openfootball(path, after):
    """Fallback fixture list: the openfootball season file (dates, times, teams) for the next round."""
    ms = [m for m in json.load(open(path))["matches"] if m["date"] > after and not m.get("score")]
    if not ms:
        return []
    rnd = ms[0]["round"]
    return [dict(date=m["date"], time=m.get("time"), home=m["team1"], away=m["team2"]) for m in ms if m["round"] == rnd]


def load_fixtures(path):
    return list(csv.DictReader(open(path))) if path and os.path.exists(path) else []


def load_upcoming_odds(path):
    out = {}
    if path and os.path.exists(path):
        for r in csv.DictReader(open(path)):
            out[(elo.norm(r["home"]), elo.norm(r["away"]))] = dict(
                odds=(float(r["odds_h"]), float(r["odds_d"]), float(r["odds_a"])), source=r.get("source", ""))
    return out


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else "."
    odds_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(d, "odds_history.csv")
    fixtures_path = sys.argv[3] if len(sys.argv) > 3 else os.path.join(d, "fixtures_next.csv")
    up_odds_path = sys.argv[4] if len(sys.argv) > 4 else os.path.join(d, "odds_next.csv")
    cwd = os.getcwd()
    os.chdir(d)
    matches = elo.load_matches(".")
    churn = attdef.load_churn("churn.json")
    os.chdir(cwd)
    fc = all_forecasts(matches, churn)
    c = fit_draw_c(fc)
    odds = load_odds(odds_path)
    cur = os.path.join(d, "odds_current_season.csv")
    if os.path.exists(cur):
        odds.update(load_odds(cur))
    bt = backtest(fc, odds, c)
    fixtures = load_fixtures(fixtures_path)
    pv = previews(matches, churn, fixtures, load_upcoming_odds(up_odds_path), c)
    json.dump(dict(backtest=bt, previews=pv), open(os.path.join(d, "forecast.json"), "w"), separators=(",", ":"))
    s = bt["summary"]
    print(f"backtest {bt['n']} matches from {bt['from_season']}, draw c={c}, avg bookmaker margin {bt['avg_margin']:.1%}")
    for mod in ("elo", "goals", "market"):
        print(f"  {mod:<7} log loss {s[mod]['logloss']:.4f}  Brier {s[mod]['brier']:.4f}  RPS {s[mod]['rps']:.4f}  accuracy {s[mod]['accuracy']:.1%}")
    print(f"  base rate log loss {s['base_rate']['logloss']}  (H {s['base_rate']['home']}, D {s['base_rate']['draw']}, A {s['base_rate']['away']})")
    print("  bets:", bt["bets"], " disagree by 10+ pts:", bt["disagree"])
    print(f"previews: {len(pv)}")
