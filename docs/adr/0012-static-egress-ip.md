# 0012. Static egress IP via Direct VPC egress and Cloud NAT

- **Status**: proposed (deferred to Phase 4; the owner reviews it then)
- **Date**: 2026-11-21
- **Deciders**: owner
- **Enforcement**: none yet

## Context
Trading 212's live API key can be locked to an IP address. Cloud Run jobs have no fixed
egress IP by default. The demo key used until Phase 4 needs no IP lock.

## Options considered
1. **Direct VPC egress plus Cloud NAT with a reserved static IP**: about $4 a month;
   the live key is locked to that IP.
2. **Unlocked live key**: free; a leaked key could trade from anywhere.
3. **A proxy VM with a static IP**: similar cost; more to patch.

## Decision
Proposed: option 1, built in Phase 4 before any live trading. Not accepted yet; the
owner decides at Phase 4.

## Consequences
- Good: a stolen live key is useless outside our network.
- Bad / costs: about $4 a month; more infrastructure.
- Revisit when: Phase 4 starts.
