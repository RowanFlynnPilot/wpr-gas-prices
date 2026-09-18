<#
.SYNOPSIS
    Scrape gas prices, re-render the newsletter digest, commit, and push.

.DESCRIPTION
    A standby runner from your residential IP, scheduled twice a day (7am / 7pm
    Central) by Windows Task Scheduler.

    It was the PRIMARY when GasBuddy's Cloudflare hard-403'd the GitHub Actions IP
    (that block froze the widget at 07/27 prices for five days in mid-2026). CI has
    since scraped all 22 cities reliably, so this script now checks first and exits
    when the published data is already fresh -- scraping on top of CI is what got
    this IP rate-limited on 2026-09-17. It still takes over whenever CI stops
    delivering, and that needs no intervention: the freshness check notices.

    Steps: recover any stuck rebase -> pull --rebase -> exit early if the data is
    already fresh -> scrape -> render digest PNG -> commit -> push (with one
    rebase-and-retry if CI pushed underneath us). Anything short of a healthy run
    files a GitHub issue, since Task Scheduler swallows this console output.

    NOTE: keep this file pure ASCII. PowerShell 5.1 reads BOM-less scripts as ANSI,
    where a UTF-8 em-dash decodes to a smart quote and silently terminates strings.

.PARAMETER NoPush
    Do everything but commit and push. Useful for dry-runs.

.PARAMETER SkipDigest
    Skip the Playwright digest render (faster when you only want the JSON).

.EXAMPLE
    .\scripts\update-gas-prices.ps1

.EXAMPLE
    .\scripts\update-gas-prices.ps1 -NoPush
#>

[CmdletBinding()]
param(
    [switch]$NoPush,
    [switch]$SkipDigest
)

$ErrorActionPreference = "Stop"

# -- Locate the project root regardless of where Task Scheduler invokes us -----
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

# -- Failure alerting -----------------------------------------------------------
# Task Scheduler swallows this script's console output, so a fatal exit here is
# invisible (the runner once died silently for two days on a stuck rebase).
# Fatal paths file/refresh one GitHub issue -- the same place CI failures
# surface -- and a healthy run closes it. Best-effort: alerting must never mask
# the real failure.
$AlertTitle = "Local gas-price runner failing"
function Publish-FailureAlert {
    param([string]$Reason)
    try {
        if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { return }
        $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm'
        $body = "The local Task Scheduler run at $stamp Central did not complete cleanly.`n`n$Reason`n`nSee scripts/update-gas-prices.ps1. This issue closes itself after the next healthy local run."
        $existing = gh issue list --state open --search "$AlertTitle in:title" --json number --jq ".[0].number"
        if ($existing) { gh issue comment $existing --body $body | Out-Null }
        else { gh issue create --title $AlertTitle --body $body | Out-Null }
        Write-Host "[alert] Filed failure alert on GitHub." -ForegroundColor Yellow
    } catch {
        Write-Host "[warn] Could not file failure alert: $_" -ForegroundColor Yellow
    }
}
# Everything written on an issue (body + every comment), reduced to letters and
# digits. Matching on that key means a move still counts as "already reported"
# after a human reply pushes it down the thread, or after one encoding hop turns
# its cent sign into mojibake.
function Get-IssueThreadKey {
    param([string]$Number)
    try {
        $issue = gh issue view $Number --json body,comments | ConvertFrom-Json
        $text  = $issue.body + ' ' + (($issue.comments | ForEach-Object { $_.body }) -join ' ')
        return ($text -replace '[^a-zA-Z0-9]', '')
    } catch {
        return ''
    }
}

# gh gets its body from a UTF-8 file, never as an argument: PowerShell 5.1 encodes
# native-command arguments with the console codepage, so a cent sign in the price
# blurb reaches GitHub as mojibake. --body-file is read as UTF-8 by gh.
function Publish-GhBody {
    param([string]$Body, [string[]]$GhArgs)
    $tmp = [IO.Path]::GetTempFileName()
    try {
        [IO.File]::WriteAllText($tmp, $Body, (New-Object Text.UTF8Encoding($false)))
        & gh @GhArgs --body-file $tmp | Out-Null
    } finally {
        Remove-Item $tmp -ErrorAction SilentlyContinue
    }
}

function Close-FailureAlert {
    try {
        if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { return }
        $existing = gh issue list --state open --search "$AlertTitle in:title" --json number --jq ".[0].number"
        if ($existing) {
            gh issue close $existing --comment "Healthy local run at $(Get-Date -Format 'yyyy-MM-dd HH:mm') Central." | Out-Null
        }
    } catch { }
}

Write-Host ""
Write-Host "-- update-gas-prices.ps1 -------------------------------------------" -ForegroundColor Cyan
Write-Host "Project: $ProjectRoot"
Write-Host "Started: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host ""

# -- Preconditions: fail fast rather than publishing from the wrong place ------
$Python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Host "[error] Python venv not found at $Python" -ForegroundColor Red
    Publish-FailureAlert "Python venv not found at $Python."
    exit 2
}
if (-not (Test-Path .\scrape_gas_prices.py)) {
    Write-Host "[error] scrape_gas_prices.py not found in $ProjectRoot" -ForegroundColor Red
    Publish-FailureAlert "scrape_gas_prices.py not found in $ProjectRoot."
    exit 2
}

$Branch = (git rev-parse --abbrev-ref HEAD).Trim()
if ($Branch -ne "main") {
    Write-Host "[error] On branch '$Branch', not 'main'. Refusing to publish." -ForegroundColor Red
    Write-Host "        Switch to main before scheduling this task." -ForegroundColor Red
    Publish-FailureAlert "Repo is on branch '$Branch', not 'main'."
    exit 2
}

# The scraper skips EIA silently without a key, which would quietly drop the trend
# chart and context strip. Say so rather than publishing a thinner file.
if (-not $env:EIA_API_KEY) {
    Write-Host "[warn] EIA_API_KEY not set. EIA trend/context will be skipped." -ForegroundColor Yellow
}

# -- Recover from a previous run's interrupted rebase ---------------------------
# GitHub's cron drifts by hours under load (observed 6-12h, Aug 2026), so the CI
# backup can push mid-run despite the nominal schedule separation. If a past
# run's rebase was left half-done, every later run would die at the pull below
# (this dead-ended the runner for two days once). Abort it: the commit it was
# replaying is regenerated data, and the -X theirs pull below re-lands it.
if ((Test-Path .git\rebase-merge) -or (Test-Path .git\rebase-apply)) {
    Write-Host "[git] Interrupted rebase found. Aborting it..." -ForegroundColor Yellow
    git rebase --abort
    if ((Test-Path .git\rebase-merge) -or (Test-Path .git\rebase-apply)) {
        git rebase --quit
    }
}

# -- Sync with origin before scraping ------------------------------------------
# git writes informational output to stderr; don't redirect it in PowerShell or it
# surfaces as ErrorRecords.
# -X ours: during a rebase "ours" is origin/main. Conflicts here only happen
# when a past run left a data commit it never pushed -- that data is stale by
# definition, so origin wins and the leftover commit shrinks to whatever
# doesn't conflict (usually nothing). The scrape below regenerates everything
# regardless. Automation can't produce real code conflicts -- CI never edits
# source files.
Write-Host "[git] Pulling latest from origin/main..." -ForegroundColor DarkGray
git pull --rebase -X ours origin main
if ($LASTEXITCODE -ne 0) {
    git rebase --abort
    Write-Host "[error] Could not pull cleanly. Resolve manually before publishing." -ForegroundColor Red
    Publish-FailureAlert "git pull --rebase failed; the run was skipped."
    exit 1
}

# -- Skip the scrape when CI has already published fresh data -------------------
# This script was written when GasBuddy's Cloudflare hard-403'd the GitHub Actions
# IP, so local was the only way to get station data. That changed: CI has scraped
# all 22 cities on every run since mid-August. Scraping anyway meant four full
# scrapes a day across two IPs, and on 2026-09-17 GasBuddy rate-limited this one
# (HTTP 429, Retry-After 441s, only 4/22 cities). Running only when the published
# file is actually stale or incomplete makes this a real backup and halves this
# IP's footprint. The condition is self-correcting: if CI stops delivering,
# scraped_at stops advancing (or cities come back stale) and this run goes ahead.
$FreshHours = 10
if (Test-Path .\docs\gas_prices.json) {
    try {
        $published  = Get-Content .\docs\gas_prices.json -Raw -Encoding UTF8 | ConvertFrom-Json
        $ageHours   = ([datetimeoffset]::UtcNow - [datetimeoffset]::Parse($published.scraped_at)).TotalHours
        $metroProps = @($published.metros.PSObject.Properties)
        $staleCount = @($metroProps | Where-Object { $_.Value.stale }).Count
        if ($ageHours -lt $FreshHours -and $staleCount -eq 0) {
            Write-Host ("[skip] Published data is {0:N1}h old with all {1} metros fresh - nothing to do." -f $ageHours, $metroProps.Count) -ForegroundColor Green
            Write-Host "       (This runner scrapes only when the data is older than $FreshHours h or has stale metros.)"
            Close-FailureAlert
            exit 0
        }
        Write-Host ("[run] Published data is {0:N1}h old with {1} stale metro(s) - scraping." -f $ageHours, $staleCount) -ForegroundColor Cyan
    } catch {
        Write-Host "[warn] Could not read published freshness; scraping anyway. $_" -ForegroundColor Yellow
    }
}

# -- Scrape --------------------------------------------------------------------
Write-Host ""
Write-Host "[scrape] Running scrape_gas_prices.py (~8-10 min: rate-limit batching)" -ForegroundColor Cyan
& $Python .\scrape_gas_prices.py --output docs\gas_prices.json
if ($LASTEXITCODE -ne 0) {
    Write-Host "[error] Scraper exited $LASTEXITCODE." -ForegroundColor Red
    Publish-FailureAlert "scrape_gas_prices.py exited $LASTEXITCODE."
    exit 1
}

# -- Report what the run actually achieved -------------------------------------
# docs/scrape_status.json is the scraper's per-run heartbeat (gitignored).
if (Test-Path .\docs\scrape_status.json) {
    # -Encoding UTF8: PS 5.1 otherwise reads this as ANSI and the blurb's cent
    # sign arrives as "Ac" mojibake (it reached issue #52 that way once).
    $status = Get-Content .\docs\scrape_status.json -Raw -Encoding UTF8 | ConvertFrom-Json
    $fresh = "$($status.cities_fresh)/$($status.cities_total)"
    # Task Scheduler swallows this console output, so anything short of a healthy
    # run also files the GitHub issue - a degraded run used to exit 0 in silence
    # and was only noticed because someone happened to see the window.
    $rateNote = if ($status.rate_limited) { " GasBuddy rate-limited this IP and the run stopped early rather than retrying into the ban." } else { "" }
    if ($status.gasbuddy_success -and -not $status.degraded) {
        Write-Host "[ok] Healthy run. $fresh cities fresh." -ForegroundColor Green
    } elseif ($status.gasbuddy_success) {
        Write-Host "[warn] Degraded run: only $fresh cities fresh; rest carried forward." -ForegroundColor Yellow
        Publish-FailureAlert "Degraded run: only $fresh cities scraped fresh; the rest were carried forward, so the statewide average is mostly stale.$rateNote"
    } elseif ($status.aaa_only) {
        Write-Host "[warn] GasBuddy unreachable even locally. AAA trend refreshed, station prices held." -ForegroundColor Yellow
        Publish-FailureAlert "GasBuddy was unreachable from this machine ($fresh cities fresh). The AAA statewide trend refreshed; station and metro prices are carried forward.$rateNote"
    } else {
        Write-Host "[warn] GasBuddy and AAA both unreachable. Nothing new to publish." -ForegroundColor Yellow
        Publish-FailureAlert "Neither GasBuddy nor AAA could be reached; nothing new was published.$rateNote"
    }

    # Story nudge: the scraper flags statewide moves past its thresholds (see
    # detect_notable_move). One open issue means the newsroom has already been
    # nudged; closing it re-arms the nudge. Same title as the CI step, so the
    # two runners dedup each other. Best-effort.
    if ($status.notable_move -and $status.notable_move.text) {
        Write-Host "[news] $($status.notable_move.text)" -ForegroundColor Cyan
        try {
            if (Get-Command gh -ErrorAction SilentlyContinue) {
                $newsTitle = "Fuel Watch: notable gas-price move"
                $moveText = $status.notable_move.text
                # Compare on letters and digits only: a cent sign that survived one
                # encoding hop as mojibake must still count as the same move.
                $moveKey = ($moveText -replace '[^a-zA-Z0-9]', '')
                $open = gh issue list --state open --search "$newsTitle in:title" --json number --jq ".[0].number"
                if ($open) {
                    # An open nudge must never swallow a later move: issue 52 sat
                    # open from Sep 1 and suppressed a 33c/gal jump two weeks on.
                    # Comment when the move has changed; stay quiet when it hasn't.
                    if ((Get-IssueThreadKey $open) -notlike "*$moveKey*") {
                        $body = "$moveText`n`nUpdated $(Get-Date -Format 'yyyy-MM-dd HH:mm') Central - the move has changed since this issue was opened."
                        Publish-GhBody -Body $body -GhArgs @('issue', 'comment', $open)
                        Write-Host "[news] Updated the open story nudge (#$open)." -ForegroundColor Cyan
                    }
                } else {
                    # A closed nudge means the newsroom is done with THAT move. The
                    # same week-over-week jump keeps qualifying for days, so don't
                    # raise it again just because the issue was closed.
                    $lastClosed = gh issue list --state closed --search "$newsTitle in:title" --limit 1 --json number --jq ".[0].number"
                    if ($lastClosed) {
                        if ((Get-IssueThreadKey $lastClosed) -like "*$moveKey*") {
                            Write-Host "[news] Same move was already nudged and closed (#$lastClosed); staying quiet." -ForegroundColor DarkGray
                            $moveText = $null
                        }
                    }
                }
                if ($moveText -and -not $open) {
                    $newsBody = "$moveText`n`nThis is a story nudge, not an error - the widget and newsletter digest already show the new numbers, and the widget's Copy button has a quotable blurb. Close this issue after reading; it will fire again on the next notable move."
                    Publish-GhBody -Body $newsBody -GhArgs @('issue', 'create', '--title', $newsTitle)
                    Write-Host "[news] Filed story nudge on GitHub." -ForegroundColor Cyan
                }
            }
        } catch { }
    }
}

# -- Re-render the newsletter digest PNG ---------------------------------------
# Email can't embed the live widget, so the digest is a baked image. It reads the
# JSON over HTTP (fetch won't work from file://), same as the CI job does.
if (-not $SkipDigest) {
    Write-Host ""
    Write-Host "[digest] Rendering docs/digest.png..." -ForegroundColor Cyan
    $server = $null
    try {
        $server = Start-Process -FilePath $Python `
            -ArgumentList '-m', 'http.server', '4180', '--bind', '127.0.0.1', '--directory', 'docs' `
            -PassThru -WindowStyle Hidden

        $ready = $false
        foreach ($i in 1..20) {
            Start-Sleep -Milliseconds 500
            try {
                Invoke-WebRequest -Uri 'http://127.0.0.1:4180/digest.html' -UseBasicParsing -TimeoutSec 3 | Out-Null
                $ready = $true
                break
            } catch { }
        }

        if (-not $ready) {
            Write-Host "[warn] Local preview server never came up; skipping digest." -ForegroundColor Yellow
        } else {
            node .\scripts\render-digest.mjs 'http://127.0.0.1:4180/digest.html' 'docs/digest.png'
            if ($LASTEXITCODE -ne 0) {
                Write-Host "[warn] Digest render failed; publishing JSON anyway." -ForegroundColor Yellow
            }
        }
    } finally {
        if ($server -and -not $server.HasExited) {
            Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

# -- Commit + push -------------------------------------------------------------
$Tracked = @(
    'docs/gas_prices.json',
    'docs/gas_prices_history.json',
    'docs/eia_weekly.json',
    'docs/eia_context.json',
    'docs/digest.png'
)
foreach ($f in $Tracked) {
    if (Test-Path $f) { git add $f }
}

git diff --staged --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "[done] No changes to publish." -ForegroundColor Green
    Close-FailureAlert
    exit 0
}

if ($NoPush) {
    Write-Host ""
    Write-Host "[skip] -NoPush set; staged but not committing." -ForegroundColor Yellow
    git diff --staged --stat
    exit 0
}

Write-Host ""
Write-Host "[git] Committing and pushing..." -ForegroundColor Cyan
# [skip ci] keeps a data-only commit from spending Actions minutes on the test
# workflow; the scheduled update job is cron-triggered and unaffected either way.
$stamp = (Get-Date -Format 'yyyy-MM-dd HH:mm')
git commit -m "Update gas prices - $stamp Central (local) [skip ci]"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[warn] git commit failed or nothing to commit." -ForegroundColor Yellow
    exit 0
}

git push origin main
if ($LASTEXITCODE -ne 0) {
    # The CI backup job may have pushed while we were scraping (its cron drifts
    # by hours, so this is a normal event, not an anomaly). Rebase onto it with
    # -X theirs -- our data commit is minutes old and should win any collision
    # on the generated files -- and retry once. If even that fails, abort the
    # rebase so the NEXT run starts clean instead of inheriting a stuck state.
    Write-Host "[warn] Push rejected. Rebasing onto origin/main and retrying..." -ForegroundColor Yellow
    git pull --rebase -X theirs origin main
    if ($LASTEXITCODE -ne 0) {
        git rebase --abort
        Write-Host "[error] Rebase failed. Resolve manually." -ForegroundColor Red
        Publish-FailureAlert "Post-push rebase failed; today's data was scraped but not published."
        exit 1
    }
    git push origin main
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[error] git push failed after retry." -ForegroundColor Red
        Publish-FailureAlert "git push failed after a rebase retry; today's data was scraped but not published."
        exit 1
    }
}

Write-Host ""
Write-Host "[done] Published. Pages will redeploy in about a minute." -ForegroundColor Green
Write-Host "       https://rowanflynnpilot.github.io/wpr-gas-prices/"
Write-Host ""
Close-FailureAlert
exit 0
