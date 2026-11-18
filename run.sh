#!/usr/bin/env bash
# Rebuild everything from the live FPL API. Run from anywhere: bash run.sh
set -euo pipefail
cd "$(dirname "$0")/data"
export PYTHONPATH=..
python ../collect_fpl.py .                       # live data -> results, player totals, player-match rows
AS_OF=$(cat as_of.txt)
python ../elo.py . elo.json                      # team Elo, full history recomputed
python ../attdef.py                              # attack/defence with uncertainty (reads churn.json)
python ../players.py fpl "$AS_OF"                # squad power rankings
python ../matches.py "fpl/$(ls fpl | sort | tail -1)/merged_gw.csv" "$AS_OF"   # match ratings
python ../collect_odds.py .                      # bookmaker odds for the next round (optional: ODDS_API_KEY)
python ../forecast.py . odds_history.csv fixtures_next.csv odds_next.csv   # backtest vs the market + next-round previews
python ../build_data.py . ../public/pl-elo.json  # one file for the page
