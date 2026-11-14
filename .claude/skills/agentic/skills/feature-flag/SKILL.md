---
name: feature-flag
description: Add, roll out or remove a feature flag or kill switch the safe way (registered with owner and expiry, both paths tested, safe default). Use when shipping risky or user-visible behaviour dark, adding an emergency off-switch, or cleaning up an expired flag.
argument-hint: "[add|remove] [flag-name]"
---

# Feature flags and kill switches

Read `docs/feature-flags.md` for this repo's mechanics. Summary of the model:
- Every flag answers "is this feature enabled?" (true = on). Names are kebab-case.
- Registry: `flags.json` at the repo root is the single list of flags. CI fails on
  unregistered flags and on release flags past their `expires` date.
- Values resolve as: env override `FLAG_<UPPER_SNAKE>` (local/dev) → remote override
  (per-environment JSON in cloud storage, flipped by the `ops` workflow, no deploy needed)
  → registry default.
- Types: `release` (temporary, must have `expires`, removed after full rollout),
  `kill-switch` (permanent, default true, lets ops turn a risky feature off in seconds),
  `ops` (permanent tuning).

## Add
1. Add the entry to `flags.json`: type, default, owner, created, expires (release only,
   at most 60 days out), description.
2. Defaults: release flags default to false (ship dark). Kill switches default to true.
   When the flag system itself fails, the code must fall back to the safe value.
3. Gate at the highest sensible level (route, job, command), once. Do not scatter checks.
4. Tests for **both** states. For a kill switch, test that switching it off fully stops
   the feature, including background work and side effects, not just the UI.
5. Mention the flag in the PR's rollout section and in the plan.

## Roll out
Turn on in staging, verify, then production:
`gh workflow run ops.yml -f action=set -f env=staging -f flag=<name> -f value=true`
(`action=restore` removes the override so the registry default applies).
Production changes to flags go through the owner unless they turn a feature **off**
(`action=kill`), which is always allowed in an incident.

## Remove (release flags, after full rollout)
1. Delete the flag check and the dead branch of code and its tests.
2. Remove the entry from `flags.json` and any remote overrides.
3. One PR per flag, titled `chore(flags): remove <name>`.
