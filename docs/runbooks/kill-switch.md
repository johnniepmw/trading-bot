# Runbook: stop new orders (and start them again)

Three ways, from narrowest to broadest. None of them sells anything or touches positions;
closing positions is yours (closing-positions.md).

| Way | Stops | How fast | Undo |
|---|---|---|---|
| **Pause request** | new orders | the next check (before every order) | owner: `ops` → `clear-pause` |
| **Kill switch** `new-orders` | new orders | within 30 seconds | owner: `ops` → `restore` |
| **Pause the schedule** | whole runs | from the next scheduled run | owner, in the console |

All three fail closed: if the bot can't read the pause bucket or the flags file, it treats
that as stopped and alerts.

## Pause request (the one-way stop)
Any object in the environment's pause bucket stops new orders. You (and in Phase 3 the
dashboard's button) can add one; only you can clear them.
- Pause (Cloud Shell):
  ```
  echo '{"by": "owner"}' | gcloud storage cp - gs://trading-bot-515509-pause-prod/requests/owner.json
  ```
- Check: a `smoke` execution reports it:
  ```
  gcloud run jobs execute trading-bot-prod --region europe-west2 --args=smoke --wait
  gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="trading-bot-prod" AND jsonPayload.paused:*' --limit 1 --format='value(timestamp,jsonPayload.paused,jsonPayload.new_orders)'
  ```
  (`True False` = paused). A paused run also sends a `paused` alert.
- Resume (owner only): GitHub → Actions → **ops** → Run workflow → `action=clear-pause`,
  `env=prod`. Removes every request; new orders resume at the next check.

## Kill switch
- Off (agents may do this in an incident): `ops` → `action=kill`, `flag=new-orders`,
  `env=prod`, or `gh workflow run ops.yml -f action=kill -f env=prod -f flag=new-orders`.
- Back on (owner): `ops` → `action=restore` (the registry default applies) or `action=set`
  with `value=true|false`.
- The override file is `gs://trading-bot-515509-flags-prod/flags.json`; each change is
  written with a generation match, and a read error never wipes an existing override.
  Never delete the file.

## Pause the schedule
- `ops` → `action=pause` (agents may in staging; rollback and kill only in prod). No new
  runs start; a running one finishes.
- Resume (owner): console → Cloud Scheduler → `trading-bot-prod-daily` → Resume, or
  `gcloud scheduler jobs resume trading-bot-prod-daily --location europe-west2`.
- Not the same as a pause request: `clear-pause` doesn't resume the schedule.

For staging, replace `prod` with `staging` everywhere.
