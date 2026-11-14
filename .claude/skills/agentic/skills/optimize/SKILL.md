---
name: optimize
description: Karpathy-style ratchet loop for a measurable target (latency, bundle size, build or test time, memory, flake rate, a model or backtest metric). Propose, change, measure, keep if better and guards pass, otherwise revert; repeat within a budget.
argument-hint: "[metric] [target or budget]"
disable-model-invocation: true
---

# Optimize: the ratchet

Target: $ARGUMENTS

This only works with an unambiguous, cheap, automated score. Without one, stop and say
what measurement is missing.

## Set up (once, on a fresh branch `perf/<slug>`)
1. Define in `docs/exec-plans/active/<slug>.md`:
   - **Score**: the exact command that prints one number, and whether lower or higher is better.
   - **Guards**: must stay true for any kept change: `make check` green, plus any
     held-out metric (for model, strategy or backtest work: an out-of-sample period the
     loop never optimises on, to catch overfitting).
   - **Budget**: max experiments or wall-clock time (default 20 experiments).
   - **Scope**: the files the loop may change. Everything else is read-only.
2. Measure the baseline three times; record median and spread. A change must beat the
   baseline by more than the spread to count.

## Loop
For each experiment:
1. Write a one-line hypothesis in `.agentic/optimize/results.tsv`
   (id, hypothesis, score, guards, kept).
2. Make one focused change inside the scope.
3. Run guards, then the score (three runs if noisy).
4. Better and guards pass → commit (`perf: <hypothesis> (<old> → <new>)`).
   Otherwise → `git checkout -- .` (or `git reset --hard HEAD`) and log why.
5. Every five experiments, re-read the results and change strategy if you are stuck.

## Finish
Copy the results table into the plan, write what worked and what did not, and open the
PR through the normal `review` and ship steps. Never keep a change that only improves
the in-sample score while the held-out guard gets worse.
