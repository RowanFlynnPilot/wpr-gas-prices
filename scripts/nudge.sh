#!/usr/bin/env bash
# The story nudge: keep exactly one "Fuel Watch: notable gas-price move" issue
# carrying the current move, for the newsroom — a heads-up, not an error alert.
#
# Runs as the update workflow's "Nudge on notable price move" step; the local
# runner (scripts/update-gas-prices.ps1) mirrors it in PowerShell with the same
# title, so the two dedup each other. tests/workflow-scripts.test.sh drives every
# branch offline against a stub `gh`.
#
# Whether the thread already reports this move is decided in one tested place:
# `scrape_gas_prices.py --nudge-check` reads the thread on stdin and the run's
# move from scrape_status.json, and answers new / same / none. "same" means the
# figure moved under 5¢ since the thread last stated it, so a daily 1¢ drift
# earns neither a comment nor, once closed, a whole new issue.
#
# Inputs (environment): OUTPUT (gas_prices.json path; its directory holds the
# status file; default docs/gas_prices.json), PYTHON (default python), GH_TOKEN.
set -u

TITLE="Fuel Watch: notable gas-price move"
OUTPUT=${OUTPUT:-docs/gas_prices.json}
PYTHON=${PYTHON:-python}
STATUS_FILE="$(dirname "$OUTPUT")/scrape_status.json"

[ -f "$STATUS_FILE" ] || { echo "No $STATUS_FILE; nothing to nudge about."; exit 0; }
TEXT=$("$PYTHON" -c "import json; m = json.load(open('$STATUS_FILE')).get('notable_move'); print(m['text'] if m else '')")
if [ -z "$TEXT" ]; then
  echo "No notable move."
  exit 0
fi

thread()  { gh issue view "$1" --json body,comments --jq '[.body] + (.comments | map(.body)) | join(" ")'; }
verdict() { thread "$1" | "$PYTHON" scrape_gas_prices.py --nudge-check --output "$OUTPUT"; }

EXISTING=$(gh issue list --state open --search "$TITLE in:title" --json number --jq '.[0].number')
if [ -n "$EXISTING" ]; then
  # An open nudge must never swallow a later move (issue #52 sat open from
  # Sep 1 and hid a 33¢/gal jump), but a 1¢ daily drift is not a later move.
  if [ "$(verdict "$EXISTING")" = "new" ]; then
    NOTE="Updated $(date -u +'%Y-%m-%d %H:%M UTC') — the figure has moved 5¢ or more since this issue last reported it."
    gh issue comment "$EXISTING" --body "$(printf '%s\n\n%s' "$TEXT" "$NOTE")"
    echo "Updated open nudge #$EXISTING with the current move."
  else
    echo "Nudge #$EXISTING already reports this move (within 5c); not repeating."
  fi
  exit 0
fi

# A closed nudge means the newsroom is done with THAT move, and the same
# week-over-week jump keeps qualifying for days. Reopen only for a real change
# of figure, not a drift.
LAST_CLOSED=$(gh issue list --state closed --search "$TITLE in:title" --limit 1 --json number --jq '.[0].number')
if [ -n "$LAST_CLOSED" ] && [ "$(verdict "$LAST_CLOSED")" != "new" ]; then
  echo "Same move (within 5c) was already nudged and closed (#$LAST_CLOSED); staying quiet."
  exit 0
fi

BODY=$(printf '%s\n' \
  "$TEXT" "" \
  "This is a story nudge, not an error — the widget and newsletter digest already show the new numbers, and the widget's Copy button has a quotable blurb. Close this issue after reading; it will fire again on the next notable move." )
gh issue create --title "$TITLE" --body "$BODY"
echo "Filed story nudge."
