<!--
Thanks for the PR. A few asks before requesting review:

1. Title uses Conventional Commits prefix: feat: / fix: / docs: / test: / refactor: / chore: / ci:
2. Branch name matches the prefix (e.g. feat/quality-toolchain).
3. CI is green (Actions tab) — including type-checks, ruff, eslint, snapshots.
4. If this introduces a CDK resource change: run `cd infra && npx jest -u` and
   commit the snapshot diff in the same PR. Reviewers will inspect the diff.
5. If this changes CLAUDE.md-described behavior: scripts/check-invariants.sh
   must still pass.
-->

## Summary
<!-- 1–3 bullets. What changed and why? -->

## Test plan
- [ ] Unit tests added/updated and passing
- [ ] Integration tests considered (if schema or boto3 boundary changed)
- [ ] Manual verification (browser / cli) — describe steps

## Risk
<!--
- Blast radius (single workspace? all users? infra?)
- Reversibility (feature flag? IaC change requires redeploy?)
- Backwards compatibility (DDB schema? API contract? frontend ↔ lambda?)
-->

## Checklist
- [ ] Conventional-commits title
- [ ] No new floating promises / unsafe-* warnings beyond baseline (eslint)
- [ ] No new `S101`/`B008` ruff suppressions
- [ ] No new sys.modules globals in tests (state pollution)
- [ ] CDK snapshot regenerated if infra changed
- [ ] CLAUDE.md updated if architecture / contracts changed
