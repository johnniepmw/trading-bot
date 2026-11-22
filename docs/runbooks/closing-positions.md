# Runbook: close positions or take money out (owner only)

**When**: you want to reduce or close the bot's holdings, or withdraw cash. The bot never
does this itself, and no workflow or agent can (ADR-0009).

1. **Stop new orders first**, so the bot doesn't buy back at the next month end:
   - a pause request (kill-switch.md, "Pause request"), or
   - the kill switch: `ops` → `action=kill`, `flag=new-orders`, `env=prod`.
   Pausing the schedule alone isn't enough if you later resume it.
2. **Sell in the Trading 212 app.** Market orders, during market hours (08:00–16:30 UK).
3. **Let it settle.** While your orders are working, runs stop with `not reconciled`
   (expected); once they are filled the bot ignores them.
4. **Withdrawals look like losses.** Equity falls, and the drawdown stop (20% below the
   peak) and the daily-loss limit count it. Before withdrawing, talk to the agent: the
   high-water mark needs lowering deliberately, which is a reviewed owner step, never a
   quiet edit.
5. **Resume** only when you want the bot trading again: clear the pause (`ops` →
   `clear-pause`) or restore the kill switch (`ops` → `restore`). The next month end then
   rebalances from whatever you hold.

To stop the bot for good: stop new orders (step 1), close positions (step 2), pause the
schedule (`ops` → `pause`). The ledger stays as the record.
