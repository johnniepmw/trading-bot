#!/usr/bin/env bash
# PreToolUse(Bash) guard. Blocks commands that would bypass the delivery gates, act as the
# owner, or change production directly. Exit 2 = blocked (stderr is shown to Claude).
# This is a seatbelt for mistakes, not a security boundary: CI and GitHub rules are the
# real gates. Keep rules few, precise and tested (tests/hooks.test.sh in the kit repo).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$HERE/lib.sh"

# shellcheck disable=SC2034  # read by json_field in lib.sh
HOOK_INPUT="$(cat)"
cmd="$(json_field tool_input.command)"
cwd="$(json_field cwd)"
[ -z "$cmd" ] && exit 0

# Split compound commands into segments on && || ; | and newlines, so each rule
# looks at one command at a time.
# (awk rather than sed: BSD sed on macOS doesn't turn \n into a newline.)
segments="$(printf '%s\n' "$cmd" | awk '{ gsub(/&&|\|\||;|\|/, "\n"); print }')"

current_branch() {
  git -C "${cwd:-.}" rev-parse --abbrev-ref HEAD 2>/dev/null || true
}

cd_prod=0  # set when an earlier segment cd's into a production directory
while IFS= read -r raw; do
  # Trim and collapse whitespace.
  seg="$(printf '%s' "$raw" | tr -s '[:space:]' ' ' | sed -e 's/^ //' -e 's/ $//')"
  [ -z "$seg" ] && continue
  padded=" $seg "
  if [[ "$padded" =~ ^[[:space:]](cd|pushd)[[:space:]].*prod ]]; then cd_prod=1; fi

  # ---- git: protected branches, force pushes, skipped hooks --------------------------
  if [[ "$padded" =~ [[:space:]]git[[:space:]] ]]; then
    if [[ "$padded" =~ [[:space:]]push[[:space:]] ]]; then
      if [[ "$padded" =~ [[:space:]:/+](main|master)[[:space:]] ]]; then
        block "pushing to main/master directly. Push a branch and open a PR."
      fi
      # No refspec (or HEAD) pushes the current branch: block when that is main/master.
      read -ra push_args <<<"${padded#* push }"
      positional=()
      for a in ${push_args[@]+"${push_args[@]}"}; do [[ "$a" == -* ]] || positional+=("$a"); done
      refspec="${positional[1]:-}"
      if [[ -z "$refspec" || "$refspec" == "HEAD" ]]; then
        case "$(current_branch)" in
          main|master) block "pushing while on main/master. Create a branch first." ;;
        esac
      fi
      if [[ "$padded" =~ [[:space:]]--force[[:space:]=] || "$padded" =~ [[:space:]]-[a-zA-Z]*f[a-zA-Z]*[[:space:]] || "$padded" =~ [[:space:]]\+[^[:space:]]+ ]]; then
        block "force push. Use --force-with-lease on your own feature branch only."
      fi
    fi
    if [[ "$padded" =~ [[:space:]]--no-verify[[:space:]] ]]; then
      block "--no-verify skips the repo's git hooks (secret scan, format, commit message)."
    fi
    if [[ "$padded" =~ [[:space:]]commit[[:space:]] && "$padded" =~ [[:space:]]-[a-zA-Z]*n[a-zA-Z]*[[:space:]] ]]; then
      block "git commit -n skips the repo's git hooks."
    fi
    if [[ "$padded" =~ core\.hooksPath ]] && ! [[ "$padded" =~ core\.hooksPath[[:space:]=]+\.githooks ]]; then
      block "changing core.hooksPath away from .githooks disables the repo's git hooks."
    fi
  fi

  # ---- GitHub: never act as the owner or loosen the gates -----------------------------
  if [[ "$padded" =~ [[:space:]](gh|curl)[[:space:]] && "$padded" =~ approve-risk ]]; then
    block "the /approve-risk comment is the owner's approval. Only the owner may post it."
  fi
  if [[ "$padded" =~ [[:space:]]gh[[:space:]]+pr[[:space:]]+review[[:space:]] && ( "$padded" =~ [[:space:]]--approve[[:space:]] || "$padded" =~ [[:space:]]-a[[:space:]] ) ]]; then
    block "approving pull requests. Reviews come from the reviewer agents and CI, approvals from the owner."
  fi
  if [[ "$padded" =~ [[:space:]]gh[[:space:]]+pr[[:space:]]+merge[[:space:]] && "$padded" =~ [[:space:]]--admin[[:space:]] ]]; then
    block "merging with --admin bypasses branch rules. Use --auto and let the checks decide."
  fi
  if [[ "$padded" =~ [[:space:]]gh[[:space:]]+api[[:space:]] ]]; then
    if [[ "$padded" =~ /statuses/ ]]; then
      block "writing commit statuses would let you satisfy required checks yourself."
    fi
    if [[ ( "$padded" =~ rulesets || "$padded" =~ /protection ) && ( "$padded" =~ [[:space:]](-X|--method)[[:space:]]*(POST|PUT|PATCH|DELETE) ) ]]; then
      block "changing branch protection or rulesets."
    fi
  fi
  if [[ "$padded" =~ [[:space:]]gh[[:space:]]+secret[[:space:]]+(set|delete|remove)[[:space:]] ]]; then
    block "managing repository secrets. The owner sets secrets."
  fi
  if [[ "$padded" =~ [[:space:]]gh[[:space:]]+(workflow[[:space:]]+disable|repo[[:space:]]+edit|ruleset[[:space:]]+(create|edit|delete))[[:space:]] ]]; then
    block "changing repository or workflow settings."
  fi
  if [[ "$padded" =~ [[:space:]]gh[[:space:]]+workflow[[:space:]]+run[[:space:]] ]]; then
    if [[ "$padded" =~ infra ]]; then
      block "running the infra workflow by hand. Infra changes go through a PR."
    fi
    if [[ "$padded" =~ ops ]] && [[ "$padded" =~ env=prod ]] && ! [[ "$padded" =~ action=(rollback|kill)[[:space:]] ]]; then
      block "production ops other than rollback or kill need the owner."
    fi
  fi

  # ---- Production and secrets ---------------------------------------------------------
  if [[ "$padded" =~ [[:space:]](tofu|terraform)[[:space:]] && "$padded" =~ [[:space:]](apply|destroy|import|taint|untaint|force-unlock)[[:space:]] ]]; then
    if [[ "$padded" =~ prod || "${cwd}" =~ /prod(/|$) || "$cd_prod" -eq 1 ]]; then
      block "applying infrastructure changes to production. Production infra changes only via a merged PR."
    fi
  fi
  if [[ "$padded" =~ [[:space:]](tofu|terraform)[[:space:]]+state[[:space:]]+(rm|mv|push|replace-provider)[[:space:]] ]]; then
    block "editing infrastructure state by hand."
  fi
  if [[ "$padded" =~ [[:space:]]make[[:space:]] && "$padded" =~ (infra-apply|env-up|env-down|infra-bootstrap) && ( "$padded" =~ ENV=prod || "$padded" =~ infra-bootstrap ) ]]; then
    block "owner-only make target (production infra or bootstrap)."
  fi
  if [[ "$padded" =~ [[:space:]]make[[:space:]]+(secret-set|gh-vars|github-setup)[[:space:]] ]]; then
    block "owner-only make target (secrets, repo settings or CI variables)."
  fi
  if [[ "$padded" =~ [[:space:]]gcloud[[:space:]] ]]; then
    if [[ "$padded" =~ [[:space:]]secrets[[:space:]]+versions[[:space:]]+(access|add|destroy|disable)[[:space:]] ]]; then
      block "reading or writing secret values. Secrets never pass through the agent."
    fi
    if [[ "$padded" =~ prod && "$padded" =~ [[:space:]](deploy|update|update-traffic|delete|create|replace|set-iam-policy|add-iam-policy-binding|remove-iam-policy-binding|cp|mv|rm|rsync)[[:space:]] ]]; then
      block "changing production directly with gcloud. Use the deploy pipeline or the ops workflow."
    fi
  fi
done <<<"$segments"

exit 0
