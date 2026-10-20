# GitHub-concept

Demo for a **main → stage** promotion flow on GitHub Actions, gated by a SAST scan
with [`sraisl/polyscan-action`](https://github.com/sraisl/polyscan-action).

The app itself is a tiny Go HTTP service (`/healthz`, `/hello?name=`).
`stage` is a Git branch; this repository does not build release binaries or
deploy a staging environment. A release currently contains a source tag and
generated release notes.

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
- If `stage` doesn't exist yet, the workflow creates it from `main` and exits
  without a promotion PR or RC. Treat this as a deliberate bootstrap from a
  known-good commit, before applying branch rules that require checks on creation.
- Merge promotion PRs with a **merge commit** (enforced by the ruleset). Squash or
  rebase would rewrite the commits, so `stage` would never contain `main`'s
  history and already promoted PRs would show up again.

## Daily workflow

1. Create a feature branch and open a PR into `main`. Add exactly one supported
   `semver:*` label before merging, including `semver:none` for changes that
   should not produce a release.
2. Wait for the required `test` and `polyscan` checks and merge the source PR.
   These checks run on PRs into both `main` and `stage`.
3. Wait for `promote-to-stage.yml` to create or update the rolling promotion PR.
   Review the included changes and its semver label; newer merges into `main`
   update this same PR and rerun checks.
4. Merge the promotion PR only after `test`, `polyscan` and `semver-label` pass.
   Keep `promote/stage` managed by the workflow; do not merge `stage` back into
   it or add manual commits.
5. Check the `version` workflow and the resulting prerelease. The RC tag must
   resolve to the promotion PR's merge commit, not merely its head commit.
   `semver:none` intentionally skips publication.

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
  (`major` > `minor` > `patch` > `none`).
- You can raise the label on the promotion PR by hand; later pushes never lower it.
- The stage label check and the release workflow require exactly one of the
  four labels above. Unknown labels such as `semver:banana`, multiple semver
  labels and missing labels fail validation.
- Source PR labels are a convention, not a required check on `main`. An unlabeled
  source PR is ignored when determining the highest bump; another PR's label
  can therefore hide missing classification. Review every included PR. If no
  included PR has a label, set one on the promotion PR before merging.
- Direct pushes to `main` are blocked by the solo rulesets. Historical commits
  without a source PR have no label to carry over.
- The bump is applied to the last **final** release (`vX.Y.Z`, `v0.0.0` if none).
  If an open RC line already targets the same or a higher version, its RC number
  is incremented instead: `v1.5.0-rc.1` + `patch` → `v1.5.0-rc.2`, + `major` → `v2.0.0-rc.1`.
- Each RC is published as a GitHub prerelease with generated notes. Final
  releases (`vX.Y.Z`) and production promotion are not automated here.
- Version calculation considers all matching repository tags, not just tags
  reachable from `stage`. Keep experimental tags outside the `vX.Y.Z` and
  `vX.Y.Z-rc.N` namespaces. No tag-protection rules are supplied here.
- Logic: [`.github/scripts/next-version.sh`](.github/scripts/next-version.sh)
  (`gh api repos/sraisl/gh-concept/tags --paginate --jq '.[].name' | bash .github/scripts/next-version.sh minor`).

## Setup

For local development use Go 1.23 or newer. Administrative setup also needs an
authenticated GitHub CLI (`gh`) with permission to manage repository secrets,
labels and rulesets. Hosted workflow runners provide Bash, `jq` and `gh`.
Replace `sraisl/gh-concept` in commands and token repository access when forking.

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

Keep the token valid and rotate the secret before expiry. It must be able to
update `promote/stage` and PR metadata; do not grant it a branch-rule bypass.

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

## Operations and recovery

Use the Actions UI or the CLI to inspect failures before retrying:

```sh
gh run list --repo sraisl/gh-concept --limit 10
gh run view <run-id> --repo sraisl/gh-concept --log-failed
gh run rerun <run-id> --repo sraisl/gh-concept --failed
```

- **Promotion failed:** check token validity, repository permissions and label
  existence. Check that `stage` and `promote/stage` still point to the expected
  history. Retry the latest `main` promotion run; retrying an older push can move
  the rolling branch backwards.
- **Required check missing:** verify the workflow's PR branch triggers and that
  automation uses `PROMOTE_TOKEN`. A check must be reported by GitHub Actions
  with the exact name configured in the ruleset. Do not remove required checks
  just to unblock a merge.
- **Label check failed:** remove unsupported or duplicate semver labels and set
  exactly one supported label. Label changes rerun the stage label check.
- **Release failed after merge:** fix the underlying token/API problem and rerun
  the failed `version` job. An existing RC tag for the same merge commit is
  reused; if its release already exists, the job exits successfully. A tag
  without a release is reused to resume publication. Missing or invalid labels
  fail; `semver:none` is an intentional successful skip. Changed PR labels do not
  rewrite the original run's event payload.

Release jobs run one at a time. `queue: max` permits up to 100 pending runs;
additional runs are canceled. Queue order is based on when runs start waiting,
not guaranteed merge chronology. See [GitHub's concurrency documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).
The promotion workflow instead keeps a single pending run, coalescing pushes.

Current limits: promotion-head updates and label updates are separate API calls;
the label is not bound to a checked head SHA. Arbitrary PR sources into `stage`
are not rejected automatically. Use only `promote/stage` from this repository,
and verify the final label after synchronization completes. Keep hotfixes flowing
through `main`; a separate stage-only hotfix/backport process is not implemented.

## Local

```sh
go test ./...
go vet ./...
go run .        # PORT=8080 by default
```

In another terminal:

```sh
curl http://localhost:8080/healthz
curl 'http://localhost:8080/hello?name=stage'
```

`PORT=9090 go run .` selects a different port. Tests currently cover the Go HTTP
handlers; automated regression tests for the version script are still missing.
