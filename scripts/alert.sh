#!/usr/bin/env bash
# Decide whether the run needs a human, and keep exactly one GitHub issue about it.
#
# Runs as the update workflow's "Alert on scrape failure" step (always, even after
# a failed step). It used to live inline in the YAML, where its eight branches
# could only be exercised by a live run; tests/workflow-scripts.test.sh drives
# every branch offline against a stub `gh`.
#
# Inputs (environment):
#   STATUS_FILE      the scraper's heartbeat (default docs/scrape_status.json)
#   PYTHON           interpreter for reading it (default python)
#   CONTRACT_FAILED  "1" if the fresh data failed tests/data-contract.test.mjs
#   DIGEST_OUTCOME   the render step's outcome ("failure" when it failed)
#   PUBLISH_FAILED   "1" if the live site never served the pushed data
#   RUN_URL          link to this run; built from GITHUB_* when unset
#   GH_TOKEN         for gh (the workflow passes github.token)
set -u

TITLE="⚠️ Gas scraper needs attention"
STATUS_FILE=${STATUS_FILE:-docs/scrape_status.json}
PYTHON=${PYTHON:-python}
if [ -z "${RUN_URL:-}" ] && [ -n "${GITHUB_RUN_ID:-}" ]; then
  RUN_URL="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"
fi
RUN_URL=${RUN_URL:-"(no run link)"}

if [ -f "$STATUS_FILE" ]; then
  read OK DEG FRESH TOTAL AAA RL RLWAIT SKIPPED <<< "$("$PYTHON" -c "
import json; d = json.load(open('$STATUS_FILE'))
print(str(d.get('gasbuddy_success', False) or d.get('gasbuddy_skipped', False)).lower(),
      str(d.get('degraded', False)).lower(), d.get('cities_fresh', 0), d.get('cities_total', '?'),
      str(d.get('aaa_updated', False)).lower(), str(d.get('rate_limited', False)).lower(),
      d.get('rate_limit_wait', 0), str(d.get('gasbuddy_skipped', False)).lower())")"
  PROBLEMS=$("$PYTHON" -c "
import json; print(chr(10).join('- ' + p for p in json.load(open('$STATUS_FILE')).get('source_problems', [])))")
else
  echo "No $STATUS_FILE — the scraper crashed before writing status."
  OK=false; DEG=false; FRESH=0; TOTAL="?"; AAA=false; RL=false; RLWAIT=0; SKIPPED=false; PROBLEMS=""
fi

# Failures that are neither GasBuddy nor a stale source, but still mean someone
# should look: they join the problem list so one alert path handles them all.
add_problem() { PROBLEMS="${PROBLEMS:+$PROBLEMS
}- $1"; }
if [ "${CONTRACT_FAILED:-}" = "1" ]; then
  add_problem "Scraper output failed the widget data contract (tests/data-contract.test.mjs) — the previous data files were kept and nothing new was published; a scraper change probably altered the JSON shape"
fi
if [ "${DIGEST_OUTCOME:-}" = "failure" ]; then
  add_problem "The newsletter digest image failed to render (see the 'Render newsletter digest image' step log); the previous digest.png stays live"
fi
if [ "${PUBLISH_FAILED:-}" = "1" ]; then
  add_problem "The data was pushed but the live site never served it (see the 'Verify the live site' step) — a GitHub Pages deploy may have failed; readers are still seeing the previous prices"
fi

# How to describe the GasBuddy side: a skipped scrape has no "N/22 fresh" of
# its own — the published station data was already fresh.
if [ "$SKIPPED" = "true" ]; then GBTXT="GasBuddy scrape skipped, published station data already fresh"; else GBTXT="$FRESH/$TOTAL cities fresh"; fi

EXISTING=$(gh issue list --state open --search "$TITLE in:title" --json number --jq '.[0].number')

if [ "$OK" = "true" ] && [ "$DEG" != "true" ] && [ -z "$PROBLEMS" ]; then
  echo "Healthy run ($GBTXT, all sources current)."
  if [ -n "$EXISTING" ]; then
    gh issue comment "$EXISTING" --body "✅ Recovered — a healthy run ($GBTXT) succeeded ($RUN_URL). Auto-closing."
    gh issue close "$EXISTING"
  fi
  exit 0
fi

if [ "$OK" = "true" ] && [ "$DEG" != "true" ]; then
  # GasBuddy is fine; something that used to be invisible went wrong.
  echo "Source problem(s) — alerting."
  SUMMARY="was fine on the GasBuddy side ($GBTXT), but something else needs a look:"
  SUMMARY="$SUMMARY

$PROBLEMS"
  CAUSE="Which item is listed says where to look: AAA → their state page changed layout (parse_aaa); EIA → a renamed series code or missing EIA_API_KEY secret; a suspect city → GasBuddy's search matched somewhere else (check the address localities); data contract → run 'npm run test:widget' locally against the scraper's output; digest → the render step log; live site → the Pages deployment for this repo. The widget keeps showing the last good figures, dated, until this is fixed."
elif [ "$OK" != "true" ]; then
  echo "GasBuddy scrape failed — alerting (aaa_updated=$AAA)."
  if [ "$AAA" = "true" ]; then
    SUMMARY="could not reach GasBuddy, so station and metro prices are carried forward from the last good run. AAA's statewide trend **did** refresh, so the widget's statewide numbers and blurb are current."
  else
    SUMMARY="could not reach GasBuddy **or** AAA. Every price on the widget is carried forward from the last good run."
  fi
  CAUSE="Most common cause: Cloudflare is 403ing the GitHub Actions datacenter IP on the CSRF-token fetch (the scraper rotates TLS fingerprints and entry URLs, but cannot defeat a pure IP-reputation block). Check the 'Run scraper' log — repeated 'CSRF fetch failed ... 403' means the IP is blocked, not that the site changed. If instead the token is fetched but cities return no data, GasBuddy changed its GraphQL shape (see scrape_gas_prices.py)."
elif [ "$RL" = "true" ]; then
  # Cut short by GasBuddy's rate limit. The scraper stops on the first 429 with
  # a long Retry-After rather than retrying into the ban, so this can be a
  # near-complete run — say so instead of "mostly stale".
  echo "Rate-limited run ($FRESH/$TOTAL fresh, Retry-After ${RLWAIT}s) — alerting."
  SUMMARY="scraped $FRESH of $TOTAL cities before GasBuddy's Cloudflare rate-limited the GitHub Actions IP (Retry-After: ${RLWAIT}s). The scraper stopped there by design rather than retrying into the ban; the remaining $((TOTAL - FRESH)) carried forward from the previous run."
  CAUSE="This is the per-IP limit tripping, not a block — it usually means two full scrapes ran close together (a manual workflow_dispatch soon after the scheduled run, or cron drift bunching runs). It clears on its own; the next scheduled run is normally fine."
else
  echo "Degraded run ($FRESH/$TOTAL fresh) — alerting."
  SUMMARY="only scraped $FRESH of $TOTAL cities fresh; the rest fell back to carried-forward (stale) prices, and the statewide average was recomputed mostly from stale data."
  CAUSE="Usually transient GasBuddy rate-limiting of the GitHub Actions IP. Often self-corrects on the next run."
fi
if [ -n "$PROBLEMS" ] && { [ "$OK" != "true" ] || [ "$DEG" = "true" ]; }; then
  SUMMARY="$SUMMARY

Also needing a look:
$PROBLEMS"
fi

BODY=$(printf '%s\n' \
  "The scheduled gas-price update $SUMMARY" "" \
  "Run: $RUN_URL" "" \
  "$CAUSE This issue closes itself automatically after a healthy run." )
if [ -n "$EXISTING" ]; then
  gh issue comment "$EXISTING" --body "Still unhealthy as of $(date -u +'%Y-%m-%d %H:%M UTC') ($GBTXT). Run: $RUN_URL"
else
  gh issue create --title "$TITLE" --body "$BODY"
fi
