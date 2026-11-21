# Runbook: turn a feature off (or back on)

**When**: one feature misbehaves and the rest of the app is fine. Faster and narrower
than a rollback; works without a deploy.

1. Find the flag in `flags.json` (type `kill-switch` or `release`).
2. Off: `gh workflow run ops.yml -f action=kill -f env=prod -f flag=<name>`
   (agents may do this in an incident).
3. Within about 30 seconds every instance re-reads the override. Confirm in logs or by
   using the app.
4. Back on (owner): `gh workflow run ops.yml -f action=restore -f env=prod -f flag=<name>`
   removes the override so the registry default applies, or `action=set` with
   `value=true|false` to force a value.

The kill switch fails closed: if the bot can't read the override file (or it is missing
or malformed) it places no orders and logs `kill switch closed`. Never delete the file.

The override file is `gs://<project>-flags-prod/flags.json`; the workflow keeps a
timestamped history of each change in the run log.
