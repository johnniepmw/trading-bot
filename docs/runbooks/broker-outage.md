# Runbook: the broker is down or misbehaving

**When**: runs stop with `broker error`, `outcome unknown`, `not reconciled`, or the
Trading 212 app or API is down.

**What the bot does by itself**: every broker error stops the run with no new orders and
an alert. An order whose outcome is uncertain (a timeout after sending) is marked UNKNOWN
and never resent; each later run first reconciles with the broker's order list and only
trades again once every earlier order is accounted for. Sells are never blocked by the
risk layer, but nothing is sent while the broker can't be trusted.

1. **Short outage (hours)**: nothing to do. The next run reconciles and carries on. If it
   was a month-end day, that month's rebalance may be skipped (cycle-alerts.md,
   "month end skipped").
2. **Repeated `not reconciled`**: open the Trading 212 app and look at today's orders.
   - An order still working: wait for it to fill or cancel; the bot settles it.
   - An order you placed by hand: the bot stops while it is working, then ignores it once
     it is filled or cancelled. Tell the agent if positions changed.
   - An order the bot sent that the app doesn't show: tell the agent (the run ID is in
     the alert's log line, `make logs ENV=prod`).
3. **Long outage (days) or the API changed**: pause the schedule (`ops` → `pause`) so
   alerts stop repeating, and tell the agent. Positions stay as they are; the strategy
   holds long-term funds, so a few days without rebalancing is normal.
4. **You want out now**: see closing-positions.md. The bot never sells everything on its
   own.

After an outage of more than 5 days, the first run has no day-start equity and blocks buys
for that day (an `account_veto` alert): expected.
