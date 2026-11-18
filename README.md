# Premier League Elo, live

Every 30 minutes a GitHub Action fetches the official Fantasy Premier League API, reruns the models and publishes
`pl/public/pl-elo.json`. The Datacor page `games/pl-elo.html` loads that file when it opens.

```
FPL API ──► collect_fpl.py ──► elo.py ─────► team Elo (history since 2010)
 (no key)        │          ├─► attdef.py ──► attack/defence with uncertainty
                 │          ├─► players.py ─► squad power rankings
                 │          ├─► matches.py ─► match ratings
                 │          └─► forecast.py ► next-round previews + backtest against the bookmakers
                 └─► raw/ (untouched API responses, kept whenever results change)
The Odds API ─► collect_odds.py ──┘ (optional key; bookmaker odds for the next round)
                                     build_data.py ──► public/pl-elo.json ──► Datacor page
```

## Set-up (once)

1. **Add the files to `datacor-data`.** Put this `pl/` folder at the top of the repo, plus `.github/workflows/pl-elo.yml`.
   The `.github` folder is hidden, so use github.dev: press `.` on the repo page, drag both folders into the
   Explorer, then Commit & Push. The regular upload page flattens folders.
2. **Let the Action push.** Repo → Settings → Actions → General → Workflow permissions → *Read and write permissions* → Save.
3. **First run.** Actions → *PL Elo (live)* → *Run workflow*. The log should say something like
   `FPL 2026-27: 110 finished matches (to 2026-11-08) …`, then commit `pl/public/pl-elo.json`.
4. **Point the page at it.** In `games/pl-elo.html` (site repo), change `YOUR-GITHUB-USERNAME` in `DATA_URL` to your
   GitHub username, then deploy as usual.

5. **Bookmaker odds (optional).** Sign up for a free key at the-odds-api.com (500 requests a month), then add it
   as a repo secret called `ODDS_API_KEY` (Settings → Secrets and variables → Actions → New repository secret).
   The collector calls it at most every 6 hours (about 120 requests a month). Without it, the page still shows
   our forecasts and the head-to-heads, just without the bookmakers' column for the next round.

The optional `FOOTBALL_DATA_API_KEY` secret (already set for the pressure index) is used as a fallback: if FPL
can't be reached, results still update from football-data.org and the player data stays as it was.

## How fresh is it?

| What | Updates |
|---|---|
| Results, Elo, attack/defence | within ~30–60 min of full time (FPL marks a match `finished_provisional` shortly after the whistle) |
| Match ratings | same run; BPS can move slightly when FPL confirms bonus points about an hour later, and the next run picks that up |
| Squad power rankings, injury news | every run (FPL's season totals and news) |
| Next-round previews | every run; kick-off times from FPL; bookmaker odds every 6 hours |

`raw.githubusercontent.com` caches files for up to 5 minutes. GitHub can delay scheduled runs at busy times.

## Files

| File | What it does |
|---|---|
| `run.sh` | runs everything in order (this is what the Action calls) |
| `collect_fpl.py` | live API → `data/supplement_<season>.csv` (results), `data/fpl/<season>/players_raw.csv`, `teams.csv`, `merged_gw.csv` (one row per player per match), `data/as_of.txt`; untouched responses go to `raw/` |
| `elo.py`, `attdef.py`, `players.py`, `matches.py` | the models (standard library only, no installs) |
| `collect_odds.py` | The Odds API → `data/odds_next.csv` (median UK odds per upcoming match) and `data/odds_current_season.csv` (last pre-match odds for every match, so this season joins the backtest) |
| `forecast.py` | win/draw/loss chances from Elo and the goals model, the backtest against Bet365 since 2012-13 (log loss, Brier, RPS, accuracy, calibration, a betting test) and next-round previews with head-to-heads → `data/forecast.json` |
| `data/odds_history.csv` | Bet365 and best-available odds for every match 2010-11 to Aug 2026 (football-data.co.uk, via github.com/xgabora/Club-Football-Match-Data-2000-2025) |
| `build_data.py` | combines everything into `public/pl-elo.json` and rewrites it only when the content changes |
| `build_page.py`, `template.html` | rebuild the page with a fallback snapshot embedded |
| `data/en.1.*.json` | results 2010-11 to 2025-26 (openfootball, public domain) |
| `data/fpl/2025-26/` | last season's player totals (for blending and churn) |
| `data/churn.json` | squad turnover by club and season (from `churn.py` + vaastav/Fantasy-Premier-League) |

Only fields that change when something real happens are kept (no prices or transfer counts), so the Action
commits only after a match, a stats correction or injury news.

## Season rollover (August)

Nothing to do: when FPL switches to the new season, `collect_fpl.py` writes to a new `data/fpl/<new season>/`
folder, and last season's files are already committed, so they become the history. Two things are worth doing
once each summer:
- run `churn.py` with last season's minutes to add the new summer's turnover to `data/churn.json` (until then,
  every club gets the median);
- check that newly promoted clubs' FPL names are in `ALIASES` in `elo.py` and `TEAM_NAMES` in `players.py`.

GitHub turns off scheduled workflows in public repos after 60 days without activity. Matchday commits keep it
alive in season. If it stops over the summer, re-enable it under Actions.

## Testing without the network

`collect_fpl.collect(data_dir, fetch=...)` takes any function that returns the API's JSON for a path, so it can
be fed saved responses (for example from `raw/`) instead of the live API.
