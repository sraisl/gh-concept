# GitHub-concept

Demo for a **main → stage** promotion flow on GitHub Actions, gated by a SAST scan
with [`sraisl/polyscan-action`](https://github.com/sraisl/polyscan-action).

The app itself is a tiny Go HTTP service (`/healthz`, `/hello?name=`).

## Flow

```
push to main ──► promote-to-stage.yml
                   ├─ moves branch promote/stage to the new main commit
                   └─ opens/updates the single PR  promote/stage → stage
                      (+ highest semver:* label of the included source PRs)
                                   │
                                   ├─ ci.yml          (job: test)          go vet + go test
                                   ├─ sast.yml        (job: polyscan)      semgrep, gosec, gitleaks, zizmor
                                   └─ label-check.yml (job: semver-label)  exactly one semver:* label
                                   │
                                   ▼ merge
                                 version.yml  → tag + prerelease vX.Y.Z-rc.N
```

- There is at most **one open promotion PR**. Each push to `main` force-moves
  `promote/stage` to the new commit, so checks re-run on the latest state and the
  PR body lists all included source PRs.
- After the PR is merged, the next push to `main` opens a new one.
- Skipped if `stage` already contains the commit.
- If `stage` doesn't exist yet, the workflow creates it from `main`.
- Merge promotion PRs with a **merge commit** (enforced by the ruleset). Squash or
  rebase would rewrite the commits, so `stage` would never contain `main`'s
  history and already promoted PRs would show up again.

## Versioning

Versions are driven by PR labels, not commit messages:

| Label | Effect on merge into `stage` |
|---|---|
| `semver:major` | next major RC, e.g. `v1.4.0` → `v2.0.0-rc.1` |
| `semver:minor` | next minor RC, e.g. `v1.4.0` → `v1.5.0-rc.1` |
| `semver:patch` | next patch RC, e.g. `v1.4.0` → `v1.4.1-rc.1` |
| `semver:none`  | no tag |

- Put the label on your PR into `main`. `promote-to-stage.yml` sets the
  **highest** label of all source PRs in the promotion PR
  (`major` > `minor` > `patch` > `none`), so no bump gets lost.
- You can raise the label on the promotion PR by hand; later pushes never lower it.
- Direct pushes to `main` have no source PR. If no included PR has a label, set it
  on the promotion PR by hand (`semver-label` check fails until you do).
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

### 3. Solo branch protection

Both `main` and `stage` require pull requests, merge commits and passing `test`
and `polyscan` checks from GitHub Actions. `stage` additionally requires
`semver-label`. Strict up-to-date enforcement is disabled so the rolling
promotion branch can continue pointing at the exact `main` commit without merging
`stage` back into it. Direct pushes, force-pushes and branch deletion are blocked,
with no bypass actors. No human or code-owner approval is required.

The definitions are in [`.github/rulesets/main.json`](.github/rulesets/main.json)
and [`.github/rulesets/stage.json`](.github/rulesets/stage.json). Apply each with
`gh api repos/sraisl/gh-concept/rulesets --method POST --input <file>` for a new
ruleset, or `gh api repos/sraisl/gh-concept/rulesets/<id> --method PUT --input <file>`
to update an existing one. Read existing rules first to preserve any additional
settings. Deploy the SAST workflow with its `main` and `stage` triggers before
requiring the scan for existing PRs into `main`.

This solo setup prevents merging failed or missing checks. It does not make
workflow policy tamper-proof: a PR can modify its own workflows, and the repository
administrator can change the rules. Review workflow and release-script changes
explicitly before merging. Independent enforcement would require a policy
controlled outside the change being checked.

## Local

```sh
go test ./...
go run .        # PORT=8080 by default
```
