# GitHub-concept

*Demo* for a **main → stage** promotion flow on GitHub Actions, gated by a SAST scan
with [`sraisl/polyscan-action`](https://github.com/sraisl/polyscan-action).

The app itself is a tiny Go HTTP service (`/healthz`, `/hello?name=`).

## Flow

```
push to main ──► promote-to-stage.yml
                   ├─ creates branch promote/<short-sha> at that commit
                   └─ opens PR  promote/<short-sha> → stage  (+ semver:* label of the source PR)
                                   │
                                   ├─ ci.yml          (job: test)          go vet + go test
                                   ├─ sast.yml        (job: polyscan)      semgrep, gosec, gitleaks, zizmor
                                   └─ label-check.yml (job: semver-label)  exactly one semver:* label
                                   │
                                   ▼ merge
                                 version.yml  → tag + prerelease vX.Y.Z-rc.N
```

- One PR per commit pushed to `main` (per push, the head commit is promoted).
- Skipped if `stage` already contains the commit; re-runs don't create duplicate PRs.
- If `stage` doesn't exist yet, the workflow creates it from `main`.
- Older open promote PRs are subsets of newer ones; once a newer one is merged they
  become empty and can be closed.

## Versioning

Versions are driven by PR labels, not commit messages:

| Label | Effect on merge into `stage` |
|---|---|
| `semver:major` | next major RC, e.g. `v1.4.0` → `v2.0.0-rc.1` |
| `semver:minor` | next minor RC, e.g. `v1.4.0` → `v1.5.0-rc.1` |
| `semver:patch` | next patch RC, e.g. `v1.4.0` → `v1.4.1-rc.1` |
| `semver:none`  | no tag |

- Put the label on your PR into `main`; `promote-to-stage.yml` copies it to the
  promotion PR. Direct pushes to `main` have no source PR — set the label on the
  promotion PR by hand (`semver-label` check fails until you do).
- The bump is applied to the last **final** release (`vX.Y.Z`, `v0.0.0` if none).
  If an open RC line already targets the same or a higher version, its RC number
  is incremented instead: `v1.5.0-rc.1` + `patch` → `v1.5.0-rc.2`, + `major` → `v2.0.0-rc.1`.
- Each RC is published as a GitHub prerelease with generated notes. Final
  releases (`vX.Y.Z`) are cut later, e.g. on promotion to production.
- Logic: [`.github/scripts/next-version.sh`](.github/scripts/next-version.sh)
  (`gh api repos/sraisl/gh-concept/tags --jq '.[].name' | .github/scripts/next-version.sh minor`).

## Setup

### 1. `PROMOTE_TOKEN` secret

PRs opened with the default `GITHUB_TOKEN` do **not** trigger `pull_request`
workflows, so required checks would never run. `promote-to-stage.yml` therefore
uses a fine-grained personal access token. `version.yml` uses it as well, so that
RC tags and releases can trigger follow-up workflows (e.g. a build on tag push):

1. <https://github.com/settings/personal-access-tokens/new>
2. Repository access: *Only select repositories* → `sraisl/gh-concept`
3. Permissions: **Contents: Read and write**, **Pull requests: Read and write**
4. Store it:

   ```sh
   gh secret set PROMOTE_TOKEN --repo sraisl/gh-concept
   ```

### 2. Labels

```sh
gh label create semver:major --color B60205 --description "Breaking change → next major RC"
gh label create semver:minor --color FBCA04 --description "New feature → next minor RC"
gh label create semver:patch --color 0E8A16 --description "Fix → next patch RC"
gh label create semver:none  --color CCCCCC --description "No release"
```

### 3. Ruleset for `stage`

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

### 4. Make SAST scan, tests and label check required (later)

Appends a `required_status_checks` rule to the existing `stage` ruleset (a `PUT`
replaces the whole ruleset, so the current rules are read first):

```sh
id=$(gh api repos/sraisl/gh-concept/rulesets --jq '.[] | select(.name=="stage") | .id')
gh api repos/sraisl/gh-concept/rulesets/$id \
  | jq '{name, target, enforcement, conditions, rules: (.rules + [{
      type: "required_status_checks",
      parameters: {
        strict_required_status_checks_policy: false,
        required_status_checks: [{context: "polyscan"}, {context: "test"}, {context: "semver-label"}]
      }}])}' \
  | gh api repos/sraisl/gh-concept/rulesets/$id --method PUT --input -
```

Optional: a similar ruleset on `main` requiring PRs ensures every commit has a
source PR whose `semver:*` label can be carried over.

## Local

```sh
go test ./...
go run .        # PORT=8080 by default
```
