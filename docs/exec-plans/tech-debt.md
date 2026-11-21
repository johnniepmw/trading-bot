# Tech debt

Things noticed but deliberately left out of scope. One line each, newest first.
Format: `- YYYY-MM-DD <area>: <problem> — <impact> — <suggested fix>`

- 2026-11-21 deploy: `ci.yml` preview, `deploy.yml`, `scripts/smoke.sh`, `cloudrun-*.sh` and `make logs` assume a Cloud Run service with a URL — they can't deploy or check the job — build the job variant with the Phase 2 job module (ADR-0011)
- 2026-11-21 kit: the bootstrap skill's owner checklist still says "GitHub Pro" for every repo — misleading for public repos like this one — change it upstream in the kit repo, then `scripts/kit-sync.sh`
- 2026-11-21 github-setup: `scripts/github-setup.sh` doesn't set secret scanning, push protection or fork-PR approval — the owner sets them by hand (first-time-setup.md) — script them when next touching the file
- 2026-11-21 hooks: in a session that loads the kit as a plugin *and* reads `.claude/settings.json`, each hook runs twice — the stop gate runs `make check-fast` twice — drop the settings entries if cloud sessions start loading repo plugins
- 2026-11-21 toolchain: `mise install` can't reach GitHub releases or python downloads from cloud sessions — gitleaks, actionlint and shellcheck are missing there — add the hosts to the cloud environment or install them in its setup script
