#!/usr/bin/env bash
# After a push: did the live site actually pick it up?
#
# A run used to end at "pushed". If a GitHub Pages deploy failed or stalled, the
# run still reported healthy while readers saw the previous prices — the only
# signal was the widget's own amber "Updated 26h ago" label. This polls the live
# JSON until its scraped_at is at least the one we just committed (a skip run
# re-commits with the old scraped_at, which passes immediately; a later run's
# push counts too). On timeout it sets PUBLISH_FAILED=1 for scripts/alert.sh
# rather than failing the job — the alert is the report.
#
# Inputs (environment): PAGES_URL (required), OUTPUT (default
# docs/gas_prices.json), PYTHON (default python), TRIES (default 16), SLEEP
# seconds between tries (default 15) — ~4 minutes; Pages usually deploys in 30–90s.
set -u

: "${PAGES_URL:?PAGES_URL is required}"
OUTPUT=${OUTPUT:-docs/gas_prices.json}
PYTHON=${PYTHON:-python}
TRIES=${TRIES:-16}
SLEEP=${SLEEP:-15}

EXPECTED=$("$PYTHON" -c "import json; print(json.load(open('$OUTPUT'))['scraped_at'])")
LIVE=""
for i in $(seq 1 "$TRIES"); do
  LIVE=$(curl -fsS --max-time 20 "$PAGES_URL/gas_prices.json?t=$(date +%s)" \
         | "$PYTHON" -c "import json, sys; print(json.load(sys.stdin).get('scraped_at', ''))" 2>/dev/null || true)
  if [ -n "$LIVE" ] && [ "$("$PYTHON" -c "from datetime import datetime as d; print(int(d.fromisoformat('$LIVE') >= d.fromisoformat('$EXPECTED')))" 2>/dev/null)" = "1" ]; then
    echo "Live site serves scraped_at $LIVE (expected ≥ $EXPECTED) after $i check(s)."
    exit 0
  fi
  [ "$i" -lt "$TRIES" ] && sleep "$SLEEP"
done

echo "::error::Live site still serves scraped_at '${LIVE:-unknown}' after ~$((TRIES * SLEEP))s; expected $EXPECTED. A Pages deploy may have failed."
[ -n "${GITHUB_ENV:-}" ] && echo "PUBLISH_FAILED=1" >> "$GITHUB_ENV"
exit 0
