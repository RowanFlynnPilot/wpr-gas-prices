#!/usr/bin/env bash
# Offline tests for scripts/alert.sh and scripts/nudge.sh: every branch, driven
# against a stub `gh` that answers canned queries and records every write.
#
# Run from the repo root:  bash tests/workflow-scripts.test.sh
# (PYTHON=./.venv/Scripts/python.exe on Windows; CI job "workflow-scripts")
set -u
cd "$(dirname "$0")/.."
export PYTHON=${PYTHON:-python}

WORK_POSIX=$(mktemp -d)
trap 'rm -rf "$WORK_POSIX"' EXIT
# Under Git Bash on Windows, mktemp gives a /tmp/... path that the Windows Python
# the scripts call cannot open; cygpath -m gives the C:/... form Python and bash
# file tests both accept. PATH, however, must stay POSIX — bash does not resolve
# C:/ entries there, and the real gh would silently take over.
WORK=$WORK_POSIX
if command -v cygpath >/dev/null 2>&1; then WORK=$(cygpath -m "$WORK_POSIX"); fi
mkdir -p "$WORK/bin" "$WORK/docs"

# ── stub gh ────────────────────────────────────────────────────────────────
# GH_OPEN / GH_CLOSED: issue numbers `gh issue list` reports (empty = none).
# GH_THREAD: what `gh issue view` prints for a thread. Writes append to GH_LOG.
cat > "$WORK/bin/gh" <<'EOF'
#!/usr/bin/env bash
case "$1 $2" in
  "issue list")
    if printf '%s\n' "$@" | grep -qx -- closed; then printf '%s\n' "${GH_CLOSED:-}"; else printf '%s\n' "${GH_OPEN:-}"; fi ;;
  "issue view") printf '%s\n' "${GH_THREAD:-}" ;;
  *) printf '%s\n' "$*" >> "$GH_LOG" ;;
esac
EOF
chmod +x "$WORK/bin/gh"
export PATH="$WORK_POSIX/bin:$PATH"
# Never run a single case unless the stub is the gh that will answer: these
# scripts post to the real repository otherwise.
case "$(command -v gh)" in
  "$WORK_POSIX/bin/gh") ;;
  *) echo "ABORT: stub gh is not first on PATH ($(command -v gh)); refusing to run against the real gh"; exit 2 ;;
esac
export GH_LOG="$WORK/gh.log"

PASS=0; FAIL=0
begin() { : > "$GH_LOG"; export GH_OPEN="" GH_CLOSED="" GH_THREAD=""; unset CONTRACT_FAILED DIGEST_OUTCOME PUBLISH_FAILED; CASE="$1"; }
status() { printf '%s' "$1" > "$WORK/docs/scrape_status.json"; }
ok()   { PASS=$((PASS+1)); echo "  ok   $CASE — $1"; }
fail() { FAIL=$((FAIL+1)); echo "  FAIL $CASE — $1"; echo "       log: $(tr '\n' '|' < "$GH_LOG")"; echo "       out: $(tr '\n' '|' < "$WORK/out")"; }
expect_log()     { if grep -qF -- "$1" "$GH_LOG"; then ok "gh got: $1"; else fail "gh should have got: $1"; fi; }
expect_no_log()  { if grep -qF -- "$1" "$GH_LOG"; then fail "gh should NOT have got: $1"; else ok "gh did not get: $1"; fi; }
expect_silent()  { if [ -s "$GH_LOG" ]; then fail "gh should not have been written to"; else ok "no gh writes"; fi; }
expect_out()     { if grep -qF -- "$1" "$WORK/out"; then ok "said: $1"; else fail "should have said: $1"; fi; }
run_alert() { STATUS_FILE="$WORK/docs/scrape_status.json" RUN_URL="https://example/run/1" bash scripts/alert.sh > "$WORK/out" 2>&1; }
run_nudge() { OUTPUT="$WORK/docs/gas_prices.json" bash scripts/nudge.sh > "$WORK/out" 2>&1; }

HEALTHY='{"gasbuddy_success":true,"degraded":false,"cities_fresh":22,"cities_total":22,"aaa_updated":true,"rate_limited":false,"rate_limit_wait":0,"gasbuddy_skipped":false,"source_problems":[]}'

echo "alert.sh"
begin "healthy, nothing open";           status "$HEALTHY"; run_alert; expect_out "Healthy run (22/22 cities fresh, all sources current)"; expect_silent
begin "healthy, closes the open issue";  status "$HEALTHY"; GH_OPEN=41; run_alert; expect_log "issue comment 41 --body ✅ Recovered — a healthy run (22/22 cities fresh)"; expect_log "issue close 41"
begin "skipped scrape is healthy";       status "${HEALTHY/\"gasbuddy_success\":true/\"gasbuddy_success\":false}"; status "$(printf '%s' "$HEALTHY" | sed 's/"gasbuddy_success":true/"gasbuddy_success":false/; s/"gasbuddy_skipped":false/"gasbuddy_skipped":true/; s/"cities_fresh":22/"cities_fresh":0/')"; run_alert; expect_out "Healthy run (GasBuddy scrape skipped, published station data already fresh"; expect_silent
begin "source problem on a healthy scrape"; status "${HEALTHY/\[\]/[\"AAA statewide trend: last updated 09\/20\/26 (6 days ago; limit 3)\"]}"; run_alert; expect_out "Source problem(s)"; expect_log "issue create --title ⚠️ Gas scraper needs attention"; expect_log "- AAA statewide trend: last updated 09/20/26"; expect_log "was fine on the GasBuddy side (22/22 cities fresh)"
begin "contract failure joins the problems"; status "$HEALTHY"; CONTRACT_FAILED=1 run_alert; expect_log "failed the widget data contract"
begin "digest failure joins the problems";   status "$HEALTHY"; DIGEST_OUTCOME=failure run_alert; expect_log "digest image failed to render"
begin "publish failure joins the problems";  status "$HEALTHY"; PUBLISH_FAILED=1 run_alert; expect_log "live site never served it"
begin "gasbuddy down, AAA up";           status "$(printf '%s' "$HEALTHY" | sed 's/"gasbuddy_success":true/"gasbuddy_success":false/; s/"cities_fresh":22/"cities_fresh":0/')"; run_alert; expect_out "GasBuddy scrape failed"; expect_log "could not reach GasBuddy, so station and metro prices are carried forward"; expect_log "AAA's statewide trend **did** refresh"
begin "gasbuddy and AAA both down";      status "$(printf '%s' "$HEALTHY" | sed 's/"gasbuddy_success":true/"gasbuddy_success":false/; s/"aaa_updated":true/"aaa_updated":false/')"; run_alert; expect_log "could not reach GasBuddy **or** AAA"
begin "rate-limited near-complete run";  status "$(printf '%s' "$HEALTHY" | sed 's/"degraded":false/"degraded":true/; s/"cities_fresh":22/"cities_fresh":21/; s/"rate_limited":false/"rate_limited":true/; s/"rate_limit_wait":0/"rate_limit_wait":561/')"; run_alert; expect_out "Rate-limited run (21/22 fresh, Retry-After 561s)"; expect_log "scraped 21 of 22 cities before GasBuddy's Cloudflare rate-limited"; expect_log "the remaining 1 carried forward"
begin "plain degraded run";              status "$(printf '%s' "$HEALTHY" | sed 's/"degraded":false/"degraded":true/; s/"cities_fresh":22/"cities_fresh":4/')"; run_alert; expect_log "only scraped 4 of 22 cities fresh"
begin "unhealthy with an issue already open"; status "$(printf '%s' "$HEALTHY" | sed 's/"degraded":false/"degraded":true/; s/"cities_fresh":22/"cities_fresh":4/')"; GH_OPEN=41; run_alert; expect_log "issue comment 41 --body Still unhealthy as of"; expect_no_log "issue create"
begin "degraded AND a source problem";   status "$(printf '%s' "$HEALTHY" | sed 's/"degraded":false/"degraded":true/; s/"cities_fresh":22/"cities_fresh":4/; s/\[\]/["EIA Midwest weekly: no data"]/')"; run_alert; expect_log "Also needing a look:"; expect_log "- EIA Midwest weekly: no data"
begin "no status file at all";           rm -f "$WORK/docs/scrape_status.json"; run_alert; expect_out "crashed before writing status"; expect_log "issue create"; expect_log "could not reach GasBuddy **or** AAA"

echo "nudge.sh"
MOVE='{"notable_move":{"period":"week","delta":0.33,"text":"Wisconsin regular jumped 33¢ in the past week (AAA statewide: $4.01 -> $4.34)."}}'
begin "no notable move";                 status '{"notable_move":null}'; run_nudge; expect_out "No notable move"; expect_silent
begin "open issue already says it (1c drift)"; status "$MOVE"; GH_OPEN=53; GH_THREAD="Wisconsin regular jumped 34¢ in the past week (AAA statewide: ...)."; run_nudge; expect_out "already reports this move (within 5c)"; expect_silent
begin "open issue, figure moved 8c";     status "$MOVE"; GH_OPEN=53; GH_THREAD="Wisconsin regular jumped 25¢ in the past week (...). Closed by Rowan."; run_nudge; expect_log "issue comment 53 --body Wisconsin regular jumped 33¢"; expect_log "moved 5¢ or more"
begin "open issue, different kind of move"; status "$MOVE"; GH_OPEN=53; GH_THREAD="Wisconsin regular jumped 33¢ since yesterday (...)"; run_nudge; expect_log "issue comment 53"
begin "closed issue already says it";    status "$MOVE"; GH_CLOSED=52; GH_THREAD="Wisconsin regular jumped 31Â¢ in the past week (...)."; run_nudge; expect_out "already nudged and closed (#52)"; expect_silent
begin "closed issue said something else"; status "$MOVE"; GH_CLOSED=52; GH_THREAD="Wisconsin regular dropped 10¢ in the past week (...)."; run_nudge; expect_log "issue create --title Fuel Watch: notable gas-price move --body Wisconsin regular jumped 33¢"
begin "no issues at all";                status "$MOVE"; run_nudge; expect_log "issue create --title Fuel Watch: notable gas-price move"; expect_out "Filed story nudge"

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
