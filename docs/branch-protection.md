# Branch protection for `main`

Goal: no commit can land on `main` unless the CI status check **`test`**
(the job in `.github/workflows/ci.yml`) has passed.

## Prerequisites

1. Push this branch and open a pull request into `main`.
2. Let CI run at least once so the `test` check appears in the repository's
   checks list.

The commands below only touch repository settings — run them yourself after CI
has run once. `owner/repo` is `ctkolb/photojournaler`.

## Option A — repository ruleset (recommended)

Rulesets work on private repositories; classic branch protection requires a
paid plan on private repos.

```sh
gh api --method POST repos/ctkolb/photojournaler/rulesets --input - <<'JSON'
{
  "name": "Protect main",
  "target": "branch",
  "enforcement": "active",
  "conditions": {
    "ref_name": { "include": ["refs/heads/main"], "exclude": [] }
  },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": true,
        "required_status_checks": [ { "context": "test" } ]
      }
    },
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false
      }
    }
  ]
}
JSON
```

- **required_status_checks** — blocks merge until `test` is green.
- **strict** — also requires the branch to be up to date with `main` first.
- **pull_request** with `0` approvals — forces a PR (and therefore the check)
  but still lets a solo maintainer self-merge once checks pass.
- **deletion / non_fast_forward** — no branch deletion or force-pushes.

## Option B — classic branch protection (fallback)

```sh
gh api --method PUT repos/ctkolb/photojournaler/branches/main/protection --input - <<'JSON'
{
  "required_status_checks": { "strict": true, "contexts": ["test"] },
  "enforce_admins": true,
  "required_pull_request_reviews": { "required_approving_review_count": 1 },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
```

Note: `required_approving_review_count` must be at least `1` here, so a solo
maintainer needs a second approver. Use Option A if that's a problem.

## Verify

```sh
# ruleset
gh api repos/ctkolb/photojournaler/rulesets

# classic protection
gh api repos/ctkolb/photojournaler/branches/main/protection
```

You can also see it in the UI under **Settings → Rules → Rulesets** (or
**Settings → Branches**).

## Confirm it works

Open a throwaway PR that fails CI (e.g. break a test) and check that the merge
button stays disabled with "Required statuses must pass before merging".
