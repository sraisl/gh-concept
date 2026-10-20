# GitHub-concept

*Demo* for a **main → stage** promotion flow on GitHub Actions, gated by a SAST scan
with [`sraisl/polyscan-action`](https://github.com/sraisl/polyscan-action).

The app itself is a tiny Go HTTP service (`/healthz`, `/hello?name=`).

## Flow

```
push to main ──► promote-to-stage.yml
                   ├─ creates branch promote/<short-sha> at that commit
                   └─ opens PR  promote/<short-sha> → stage
                                   │
                                   ├─ ci.yml    (job: test)      go vet + go test
                                   └─ sast.yml  (job: polyscan)  semgrep, gosec, gitleaks, zizmor
```

- One PR per commit pushed to `main` (per push, the head commit is promoted).
- Skipped if `stage` already contains the commit; re-runs don't create duplicate PRs.
- If `stage` doesn't exist yet, the workflow creates it from `main`.
- Older open promote PRs are subsets of newer ones; once a newer one is merged they
  become empty and can be closed.

## Setup

### 1. `PROMOTE_TOKEN` secret

PRs opened with the default `GITHUB_TOKEN` do **not** trigger `pull_request`
workflows, so required checks would never run. The workflow therefore uses a
fine-grained personal access token:

1. <https://github.com/settings/personal-access-tokens/new>
2. Repository access: *Only select repositories* → `sraisl/gh-concept`
3. Permissions: **Contents: Read and write**, **Pull requests: Read and write**
4. Store it:

   ```sh
   gh secret set PROMOTE_TOKEN --repo sraisl/gh-concept
   ```

### 2. Ruleset for `stage`

Protects `stage` (PR required, no force-push/deletion):

```sh
gh api repos/sraisl/gh-concept/rulesets --method POST --input - <<'JSON'
{
  "name": "stage",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/heads/stage"], "exclude": [] } },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "pull_request", "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false } }
  ]
}
JSON
```

### 3. Make the SAST scan a required check (later)

Add a `required_status_checks` rule to the ruleset (get the id via
`gh api repos/sraisl/gh-concept/rulesets`):

```json
{ "type": "required_status_checks", "parameters": {
    "strict_required_status_checks_policy": false,
    "required_status_checks": [ { "context": "polyscan" }, { "context": "test" } ] } }
```

```sh
gh api repos/sraisl/gh-concept/rulesets/<id> --method PUT --input ruleset.json
```

## Local

```sh
go test ./...
go run .        # PORT=8080 by default
```
