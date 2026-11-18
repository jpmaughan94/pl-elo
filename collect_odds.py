"""Bookmaker odds for upcoming Premier League matches from The Odds API (https://the-odds-api.com).

  python collect_odds.py <data dir>          (needs ODDS_API_KEY; without it this does nothing)

The free plan gives 500 requests a month and each call here costs 1, so this only calls the API when the
last call was 6+ hours ago (about 120 a month).
Writes:
  data/odds_next.csv            median decimal odds across UK bookmakers for each upcoming match -> forecast.py
  data/odds_current_season.csv  the last odds seen before kick-off for every match this season, in the
                                same columns as odds_history.csv, so finished matches join the backtest
"""
import csv, datetime, json, os, statistics, sys, urllib.request

URL = "https://api.the-odds-api.com/v4/sports/soccer_epl/odds/?regions=uk&markets=h2h&oddsFormat=decimal&apiKey={}"
MIN_HOURS = 6
COLS = ["MatchDate", "HomeTeam", "AwayTeam", "OddHome", "OddDraw", "OddAway", "MaxHome", "MaxDraw", "MaxAway", "source"]


def main(data_dir):
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        print("ODDS_API_KEY not set: no live odds (forecasts still run)")
        return
    stamp_path = os.path.join(data_dir, "odds_last_call.txt")
    now = datetime.datetime.now(datetime.timezone.utc)
    if os.path.exists(stamp_path):
        last = datetime.datetime.fromisoformat(open(stamp_path).read().strip())
        if now - last < datetime.timedelta(hours=MIN_HOURS):
            print(f"odds fetched {(now - last).seconds // 3600}h ago; skipping")
            return
    req = urllib.request.Request(URL.format(key), headers={"User-Agent": "datacor-data/1.0"})
    with urllib.request.urlopen(req, timeout=40) as r:
        events = json.load(r)
        left = r.headers.get("x-requests-remaining")
    open(stamp_path, "w").write(now.isoformat())

    upcoming = []
    for ev in events:
        if datetime.datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00")) <= now:
            continue  # already started: in-play odds aren't pre-match odds
        prices = {"h": [], "d": [], "a": []}
        for b in ev.get("bookmakers", []):
            for mkt in b.get("markets", []):
                if mkt["key"] != "h2h":
                    continue
                for o in mkt["outcomes"]:
                    k = "h" if o["name"] == ev["home_team"] else "a" if o["name"] == ev["away_team"] else "d"
                    prices[k].append(o["price"])
        if not all(prices.values()):
            continue
        upcoming.append(dict(date=ev["commence_time"][:10], home=ev["home_team"], away=ev["away_team"],
                             odds_h=round(statistics.median(prices["h"]), 3), odds_d=round(statistics.median(prices["d"]), 3),
                             odds_a=round(statistics.median(prices["a"]), 3),
                             best_h=max(prices["h"]), best_d=max(prices["d"]), best_a=max(prices["a"]),
                             n_books=len(prices["h"]), source="The Odds API, median of UK bookmakers",
                             fetched=now.strftime("%Y-%m-%dT%H:%MZ")))
    with open(os.path.join(data_dir, "odds_next.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(upcoming[0].keys()) if upcoming else ["date", "home", "away"])
        w.writeheader()
        for u in upcoming:
            w.writerow(u)

    # keep the latest pre-match odds for every match of the season (overwritten until kick-off)
    path = os.path.join(data_dir, "odds_current_season.csv")
    keep = {}
    if os.path.exists(path):
        for r in csv.DictReader(open(path)):
            keep[(r["MatchDate"], r["HomeTeam"], r["AwayTeam"])] = r
    for u in upcoming:
        keep[(u["date"], u["home"], u["away"])] = dict(MatchDate=u["date"], HomeTeam=u["home"], AwayTeam=u["away"],
            OddHome=u["odds_h"], OddDraw=u["odds_d"], OddAway=u["odds_a"], MaxHome=u["best_h"], MaxDraw=u["best_d"],
            MaxAway=u["best_a"], source=u["source"])
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for k in sorted(keep):
            w.writerow(keep[k])
    print(f"odds: {len(upcoming)} upcoming matches; {left} API requests left this month")


if __name__ == "__main__":
    try:
        main(sys.argv[1] if len(sys.argv) > 1 else "data")
    except Exception as e:  # odds are optional; never fail the whole update over them
        print(f"odds fetch failed: {e}")
