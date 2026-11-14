# Feature flags and kill switches

## Model
- A flag answers one question: **is this feature enabled?** `true` = on.
- `flags.json` (repo root) is the registry. CI (`make flags-check`) fails if code uses an
  unregistered flag name or a release flag is past its `expires` date.
- Resolution order at runtime:
  1. Environment variable `FLAG_<UPPER_SNAKE_NAME>` (`true/false/on/off/1/0`). For local
     development and tests.
  2. Remote override: a JSON object in the environment's flags bucket
     (`gs://<project>-flags-<env>/flags.json`), re-read every 30 seconds. Written only by the
     `ops` workflow. If it can't be read, the last good value is kept; at startup, the
     registry default is used.
  3. The registry default.
- The stack pack's `flags` module implements this; app code calls
  `isEnabled("flag-name")` and nothing else.

## Types
| Type | Default | Lifetime | Example |
|---|---|---|---|
| `release` | `false` | until fully rolled out; `expires` required (≤ 60 days) | `csv-export` |
| `kill-switch` | `true` | permanent | `order-submission` |
| `ops` | varies | permanent | `background-sync` |

## Rules
- Gate at one place, as high as possible (route, job, command).
- Test both states. A kill switch test proves the feature fully stops, including
  background work and side effects.
- Release flags are removed after rollout, one PR per flag (`garden` finds them).
- Production: agents may only turn things **off** (`ops` action `kill`). Turning
  something on in production, or `restore`, is the owner's decision.

## Operating
```bash
gh workflow run ops.yml -f action=kill    -f env=prod    -f flag=order-submission
gh workflow run ops.yml -f action=set     -f env=staging -f flag=csv-export -f value=true
gh workflow run ops.yml -f action=restore -f env=prod    -f flag=order-submission   # owner
```
