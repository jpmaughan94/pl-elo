"""Fetch the live Fantasy Premier League API and write it in the formats the models read.

  python collect_fpl.py <data dir>

Writes, for the current season (e.g. 2026-27):
  data/supplement_<season>.csv          every finished result      -> elo.py, attdef.py
  data/fpl/<season>/players_raw.csv     season totals + injury news -> players.py
  data/fpl/<season>/teams.csv
  data/fpl/<season>/merged_gw.csv       one row per player per finished match -> matches.py
  data/as_of.txt                        date of the latest finished match
  data/fixtures_next.csv                the next round's fixtures with UK kick-off times -> forecast.py
  raw/<season>/<UTC time>/*.json.gz     the untouched API responses, saved only when results change
Every file changes only when something real happens (a result, a match's stats, injury news), so the
GitHub Action commits only then.

Endpoints (no key needed):
  bootstrap-static/   players, teams, gameweeks
  fixtures/           every match, with scores once played
  event/<gw>/live/    every player's stats in that gameweek, split by fixture
  element-summary/<id>/  a player's match-by-match history (only used for double gameweeks)

If FPL can't be reached and FOOTBALL_DATA_API_KEY is set, results still update from football-data.org
(player data then stays as it was).
"""
import csv, datetime, gzip, hashlib, json, os, sys, time, urllib.request

FPL = "https://fantasy.premierleague.com/api/"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; datacor-data/1.0; +https://datacor.netlify.app)",
           "Accept": "application/json"}
POSITION = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
# Only the player fields the models use. Prices, transfers and ownership change every few minutes
# and would make every run look like new data.
KEEP = ["id", "code", "team", "team_code", "element_type", "web_name", "first_name", "second_name",
        "status", "news", "chance_of_playing_next_round", "minutes", "starts", "goals_scored", "assists",
        "expected_goals", "expected_assists", "expected_goal_involvements", "expected_goals_conceded",
        "goals_conceded", "saves", "bps", "defensive_contribution", "clean_sheets"]


def get(url, tries=4, headers=None):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers or HEADERS)
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except Exception as e:  # network blips, 429s, 5xx
            if i == tries - 1:
                raise
            print(f"  retry {i + 1} for {url}: {e}")
            time.sleep(5 * 2 ** i)


def fpl(path):
    return get(FPL + path)


def season_of(bootstrap):
    first = min(e["deadline_time"] for e in bootstrap["events"])
    y = int(first[:4])
    return f"{y}-{(y + 1) % 100:02d}"


def write_csv(path, rows, fields=None):
    fields = fields or sorted({k for r in rows for k in r})
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fields})


def uk_time(iso_utc):
    """'2026-11-21T12:30:00Z' -> UK local 'YYYY-MM-DD HH:MM' (BST from the last Sunday of March to the last Sunday of October)."""
    t = datetime.datetime.strptime(iso_utc[:19], "%Y-%m-%dT%H:%M:%S")
    def last_sunday(y, m):
        d = datetime.date(y, m, 31)
        return d - datetime.timedelta(days=(d.weekday() + 1) % 7)
    bst = datetime.datetime.combine(last_sunday(t.year, 3), datetime.time(1)) <= t < datetime.datetime.combine(last_sunday(t.year, 10), datetime.time(1))
    return (t + datetime.timedelta(hours=1 if bst else 0)).strftime("%Y-%m-%d %H:%M")


def is_done(fx):
    return (fx.get("finished") or fx.get("finished_provisional")) and fx.get("team_h_score") is not None


def collect(data_dir, fetch=fpl):
    boot = fetch("bootstrap-static/")
    fixtures = fetch("fixtures/")
    season = season_of(boot)
    teams = {t["id"]: t for t in boot["teams"]}
    elements = {e["id"]: e for e in boot["elements"]}
    done = sorted((f for f in fixtures if is_done(f)), key=lambda f: f["kickoff_time"])
    sdir = os.path.join(data_dir, "fpl", season)

    # 1. results
    results = [dict(date=f["kickoff_time"][:10], home=teams[f["team_h"]]["name"], away=teams[f["team_a"]]["name"],
                    hg=f["team_h_score"], ag=f["team_a_score"], source="fantasy.premierleague.com") for f in done]
    write_csv(os.path.join(data_dir, f"supplement_{season}.csv"), results, ["date", "home", "away", "hg", "ag", "source"])

    # 2. season totals and teams (same columns as vaastav's players_raw.csv / teams.csv)
    write_csv(os.path.join(sdir, "players_raw.csv"), list(elements.values()), KEEP)
    write_csv(os.path.join(sdir, "teams.csv"), list(teams.values()), ["id", "code", "name", "short_name"])

    # 3. one row per player per finished match
    fx_by_id = {f["id"]: f for f in done}
    gws = sorted({f["event"] for f in done if f.get("event")})
    rows, lives = [], {}
    for gw in gws:
        live = fetch(f"event/{gw}/live/")
        lives[gw] = live
        for el in live["elements"]:
            e = elements.get(el["id"])
            if not e:
                continue
            fixture_ids = [x["fixture"] for x in el.get("explain", []) if x["fixture"] in fx_by_id]
            if not fixture_ids:
                continue
            if len(fixture_ids) == 1:
                per_fixture = {fixture_ids[0]: el["stats"]}
            else:  # double gameweek: the live feed sums both games, so take each game from the player's history
                hist = fetch(f"element-summary/{el['id']}/")["history"]
                per_fixture = {h["fixture"]: h for h in hist if h["fixture"] in fixture_ids}
            for fid, st in per_fixture.items():
                f = fx_by_id[fid]
                home = e["team"] == f["team_h"] if e["team"] in (f["team_h"], f["team_a"]) else st.get("was_home", False)
                rows.append(dict(
                    name=f'{e["first_name"]} {e["second_name"]}'.strip(), position=POSITION.get(e["element_type"], "MID"),
                    team=teams[f["team_h"] if home else f["team_a"]]["name"], element=e["id"], fixture=fid, GW=gw,
                    kickoff_time=f["kickoff_time"], was_home=str(bool(home)),
                    team_h_score=f["team_h_score"], team_a_score=f["team_a_score"],
                    minutes=st.get("minutes", 0), starts=st.get("starts", 0), goals_scored=st.get("goals_scored", 0),
                    assists=st.get("assists", 0), expected_goals=st.get("expected_goals", 0),
                    expected_assists=st.get("expected_assists", 0),
                    expected_goal_involvements=st.get("expected_goal_involvements", 0),
                    expected_goals_conceded=st.get("expected_goals_conceded", 0),
                    goals_conceded=st.get("goals_conceded", 0), saves=st.get("saves", 0), bps=st.get("bps", 0),
                    bonus=st.get("bonus", 0), defensive_contribution=st.get("defensive_contribution", 0),
                    clean_sheets=st.get("clean_sheets", 0), yellow_cards=st.get("yellow_cards", 0),
                    red_cards=st.get("red_cards", 0)))
    write_csv(os.path.join(sdir, "merged_gw.csv"), rows)
    # 3b. the next round: unplayed fixtures in the earliest gameweek that still has any
    upcoming = sorted((f for f in fixtures if not is_done(f) and f.get("event") and f.get("kickoff_time")),
                      key=lambda f: f["kickoff_time"])
    nxt = [f for f in upcoming if f["event"] == upcoming[0]["event"]] if upcoming else []
    write_csv(os.path.join(data_dir, "fixtures_next.csv"),
              [dict(date=uk_time(f["kickoff_time"])[:10], time=uk_time(f["kickoff_time"])[11:16], gw=f["event"],
                    home=teams[f["team_h"]]["name"], away=teams[f["team_a"]]["name"], kickoff_utc=f["kickoff_time"]) for f in nxt],
              ["date", "time", "gw", "home", "away", "kickoff_utc"])
    as_of = done[-1]["kickoff_time"][:10] if done else datetime.date.today().isoformat()
    open(os.path.join(data_dir, "as_of.txt"), "w").write(as_of)

    # 4. keep the untouched responses whenever the set of finished results changes
    raw_root = os.path.join(os.path.dirname(os.path.abspath(data_dir)), "raw", season)
    fingerprint = hashlib.sha1(json.dumps(results, sort_keys=True).encode()).hexdigest()
    marker = os.path.join(raw_root, "last_fingerprint.txt")
    if not os.path.exists(marker) or open(marker).read().strip() != fingerprint:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%MZ")
        out = os.path.join(raw_root, stamp)
        os.makedirs(out, exist_ok=True)
        for name, obj in [("bootstrap-static", boot), ("fixtures", fixtures)] + [(f"event-{g}-live", lives[g]) for g in gws[-2:]]:
            with gzip.open(os.path.join(out, f"{name}.json.gz"), "wt", encoding="utf-8") as fh:
                json.dump(obj, fh)
        open(marker, "w").write(fingerprint)
        print(f"  saved raw snapshot {stamp}")
    print(f"FPL {season}: {len(done)} finished matches (to {as_of}), {len(rows)} player-match rows, "
          f"{len(elements)} players")
    return season


def results_from_football_data(data_dir, token):
    """Fallback for results only: football-data.org v4 (needs FOOTBALL_DATA_API_KEY)."""
    d = get("https://api.football-data.org/v4/competitions/PL/matches?status=FINISHED",
            headers={"X-Auth-Token": token, "User-Agent": HEADERS["User-Agent"]})
    y = d["filters"].get("season") or d["matches"][0]["season"]["startDate"][:4]
    season = f"{int(y)}-{(int(y) + 1) % 100:02d}"
    rows = [dict(date=m["utcDate"][:10], home=m["homeTeam"]["name"], away=m["awayTeam"]["name"],
                 hg=m["score"]["fullTime"]["home"], ag=m["score"]["fullTime"]["away"], source="football-data.org")
            for m in d["matches"]]
    write_csv(os.path.join(data_dir, f"supplement_{season}.csv"), rows, ["date", "home", "away", "hg", "ag", "source"])
    print(f"football-data.org fallback: {len(rows)} results for {season}")


if __name__ == "__main__":
    data_dir = sys.argv[1] if len(sys.argv) > 1 else "data"
    try:
        collect(data_dir)
    except Exception as e:
        print(f"FPL fetch failed: {e}")
        token = os.environ.get("FOOTBALL_DATA_API_KEY")
        if not token:
            raise
        results_from_football_data(data_dir, token)
