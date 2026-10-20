# GitHub-concept

Demo for a **main → stage** promotion flow on GitHub Actions, gated by a SAST scan
with [`sraisl/polyscan-action`](https://github.com/sraisl/polyscan-action).

The app itself is a tiny Go HTTP service (`/healthz`, `/hello?name=`).
`stage` is a Git branch. CI builds and tests a container image; RC releases
publish their tested image to GHCR with the same version as the Git tag.
Deploying a staging environment is a separate step and is not automated here.

## Flow

```
push to main ──► promote-to-stage.yml
                   ├─ moves branch promote/stage to the new main commit
                   └─ opens/updates the single PR  promote/stage → stage
                      (+ highest semver:* label of the included source PRs)
                                   │
                                   ├─ ci.yml          (job: test)          Go checks + container tests
                                   ├─ sast.yml        (job: polyscan)      semgrep, gosec, gitleaks, zizmor, hadolint
                                   └─ label-check.yml (job: semver-label)  exactly one semver:* label
                                   │
                                   ▼ merge
                                 version.yml  → tag + draft vX.Y.Z-rc.N
                                                     │
                                                     ▼
                                 image.yml → build + test + GHCR image → publish prerelease
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

## Creating a release

### Release candidate (automatic)

1. Merge a feature/fix PR into `main` with exactly one supported semver label.
2. Wait for the rolling promotion PR into `stage` to pass all required checks,
   then merge it using **Create a merge commit**.
3. Wait for the complete `version` run, including its `image` job, to succeed.
   It prepares a draft, builds/tests/pushes the container, then publishes the RC.

`semver:none` intentionally produces no RC. Do not create Git tags or releases
using GitHub's **Draft a new release** button; the pipeline manages them.

### Final release (manual approval)

1. Test and accept the RC on stage.
2. Open **Actions → final-release → Run workflow** and select branch **main**.
3. Leave `rc_tag` empty to accept the newest eligible published RC, or enter the
   exact tag you tested, then start the workflow.
4. Wait for success and verify the final release and its image reference.

For example, `v0.1.0-rc.7` becomes `v0.1.0`: the same Git commit and the same
container digest, with no image rebuild. The workflow does not deploy it.
The detailed selection and retry rules are in [Final releases](#final-releases).

You do not need to create or upload `release.json`. The pipeline generates and
attaches this verification record automatically; normal release creation only
requires the PR merges and final approval above.

## Daily workflow

CI runs `go vet`, `go test` and the SAST scan on every branch push, including
feature, fix and documentation branches, before a PR is opened. Pushes to `stage` and the
automation-managed `promote/stage` branch are excluded; their changes are tested
through PRs into `stage`. PRs into `main` and `stage` additionally test the merge
result. An open source PR can therefore have both a branch-push run and a PR run.
New commits cancel older CI runs for the same event and ref; push and PR checks
do not cancel each other. SAST uses the same event/ref isolation; semver
validation remains a PR check because branch pushes have no PR labels.

1. Create a feature branch and open a PR into `main`. Add exactly one supported
   `semver:*` label before merging, including `semver:none` for changes that
   should not produce a release.
2. Wait for the required `test`, `polyscan` and `semver-label` checks and merge the source PR.
   These checks run on PRs into both `main` and `stage`.
3. Wait for `promote-to-stage.yml` to create or update the rolling promotion PR.
   Review the included changes and its semver label; newer merges into `main`
   update this same PR and rerun checks.
4. Merge the promotion PR only after `test`, `polyscan` and `semver-label` pass.
   Keep `promote/stage` managed by the workflow; do not merge `stage` back into
   it or add manual commits.
5. Check the `version` run, including its reusable `image` job. The RC tag must
   resolve to the promotion PR's merge commit. The release stays a draft until
   the container is tested, pushed and verified. The published prerelease includes
   its digest and a `release.json` asset. `semver:none` skips the entire release.
6. Once an RC has been accepted, run `final-release.yml` manually on `main` to
   promote that exact RC to its final version without rebuilding the image.

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
- The label check on both branches and the release workflow require exactly one of the
  four labels above. Unknown labels such as `semver:banana`, multiple semver
  labels and missing labels fail validation.
- Source PRs into `main` must pass `semver-label` before merging, including
  documentation-only PRs (`semver:none`). Historical source PRs merged before
  this requirement may still be unlabeled and are ignored when determining the
  highest bump. If no included PR has a label, set one on the promotion PR.
- Direct pushes to `main` are blocked by the solo rulesets. Historical commits
  without a source PR have no label to carry over.
- The bump is applied to the last **final** release (`vX.Y.Z`, `v0.0.0` if none).
  If an open RC line already targets the same or a higher version, its RC number
  is incremented instead: `v1.5.0-rc.1` + `patch` → `v1.5.0-rc.2`, + `major` → `v2.0.0-rc.1`.
- Each RC starts as a draft and becomes a prerelease only after image verification.
  Final releases (`vX.Y.Z`) are created by the manual final-promotion workflow;
  deployment to production is not automated here.
- RC version calculation considers matching repository tags, not just tags
  reachable from `stage`. Final baselines must also have a published final release;
  an unfinished final draft does not advance the baseline. Keep experimental tags outside the `vX.Y.Z` and
  `vX.Y.Z-rc.N` namespaces. No tag-protection rules are supplied here.
- Logic: [`.github/scripts/next-version.sh`](.github/scripts/next-version.sh)
  computes the version from its input tags. [scripts/release.py](scripts/release.py)
  filters the final baselines, manages drafts and coordinates final publication.

## Setup

For local development use Go 1.23 or newer. Administrative setup also needs an
authenticated GitHub CLI (`gh`) with permission to manage repository secrets,
labels and rulesets. Hosted workflow runners provide Bash, `jq` and `gh`.
Replace `sraisl/gh-concept` in commands and token repository access when forking.

### 1. `PROMOTE_TOKEN` secret

PRs opened with the default `GITHUB_TOKEN` do **not** trigger `pull_request`
workflows, so required checks would never run. `promote-to-stage.yml` therefore
uses a fine-grained personal access token. Release and image workflows use
`GITHUB_TOKEN` instead: the version workflow calls the image workflow directly,
so release readiness does not depend on tag/release events spawning other runs:

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

Both `main` and `stage` require pull requests, merge commits and passing `test`,
`polyscan` and `semver-label` checks from GitHub Actions.
Strict up-to-date enforcement is disabled so the rolling
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
  exactly one supported label. Label changes rerun the label check on either branch.
- **Release failed after merge:** fix the underlying token/API problem and rerun
  the failed `version` job. An existing RC tag for the same merge commit is
  reused along with its draft. The image job is called again to resume verification
  and publication; an already published release is checked against `release.json`.
  A tag without a release is reused to recreate the draft. Missing or invalid labels
  fail; `semver:none` is an intentional successful skip. Changed PR labels do not
  rewrite the original run's event payload.
- **Image publication failed:** rerun the failed jobs in the `version` run, or
  manually run `image.yml` for the RC tag. The Git tag and RC
  version stay unchanged. If the push already succeeded, the job verifies the
  existing image's commit/version labels and keeps its digest instead of
  rebuilding. Authentication and registry failures fail the job; they are not
  interpreted as missing images. An existing tag with inconsistent labels fails
  rather than being overwritten. The release remains a draft until all checks,
  the metadata upload and final publication succeed. Do not publish it manually.

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

`PORT=9090 go run .` selects a different port. Tests cover the Go HTTP handlers;
automated regression tests for the version script are still missing.

## Docker

```sh
docker build --tag gh-concept:local .
docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges \
  --publish 127.0.0.1:8080:8080 gh-concept:local
```

The multi-stage Dockerfile runs `go vet` and `go test` before compiling a static
binary. The final `scratch` image contains only that binary and runs as UID/GID
65532. It has no shell or bundled HTTP client; use `/healthz` from outside the
container for readiness checks. No writable filesystem or Linux capabilities
are needed. To change the internal listening port, pass `--env PORT=9090` and
publish `127.0.0.1:9090:9090` instead.

Run the same integration checks as CI with Docker, Bash, curl and jq installed:

```sh
bash scripts/test-container.sh
```

The script builds the image, checks its non-root user, and tests health, default
and named greetings, unsupported methods and missing routes. It runs with a
read-only filesystem and dropped capabilities, checks both the default port and
the `PORT` override, and removes its test containers. The local test image is
retained. Hadolint runs as part of the existing Polyscan check; the Dockerfile
does not disable any lint rules.

## RC container publication

[`.github/workflows/version.yml`](.github/workflows/version.yml) creates an RC tag
and draft, then calls [image.yml](.github/workflows/image.yml) as a reusable
workflow. Tag pushes and release events do not trigger image builds. Direct
workflow calls avoid duplicate runs and ensure the draft exists before the image
is built. The image workflow can also be run manually for recovery.

The image workflow checks out the exact tag, validates the RC version and
requires its commit to be in `stage` history. It builds a Linux/amd64 image,
runs the container tests against that exact local image, then pushes it as
`ghcr.io/sraisl/gh-concept:vX.Y.Z-rc.N`. OCI labels record source, commit and
version. Nothing is pushed on feature or PR test builds. There is no `latest`
alias and no automatic final-version image build.

GHCR authentication uses the workflow's `GITHUB_TOKEN` with `packages: write`;
`contents: write` permits attaching metadata and publishing the verified draft;
no additional package secret is needed. New packages default to private. If a
package already exists, grant this repository Actions access in its package
settings. See [GitHub's GHCR documentation](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

For manual recovery after this workflow is merged to `main`, use an existing
RC tag containing this workflow and its scripts:

```sh
gh workflow run image.yml --repo sraisl/gh-concept -f tag=v0.1.0-rc.6
```

Use the digest reference from the successful run's summary to deploy exactly
the tested bits, for example `ghcr.io/sraisl/gh-concept@sha256:<digest>`.
New prereleases are published only after image verification and after uploading
`release.json`. That asset records version, commit, image tag, digest, immutable
image reference and workflow URL. It is an internal verification record under
the release's **Assets**, not a file to maintain in the repository. Final
promotion checks it against Git and GHCR to preserve the originally tested
digest. Do not reconstruct missing records by hand for older releases; let the
pipeline publish a new RC instead.

Generated notes are grouped by semver labels
using [`.github/release.yml`](.github/release.yml). Include migration instructions
or known issues in the source PRs; automatic notes do not assess compatibility.

The workflow refuses to overwrite an existing version tag, but GHCR tags are
not inherently immutable: another actor with package write access can still
replace them. The workflow's concurrency lock covers only its own runs.

Publication guard tests run in CI and locally without registry credentials:

```sh
python3 -m unittest discover -s scripts -p 'test_*.py'
```

## Final releases

After testing the newest RC on stage, open **Actions → final-release → Run
workflow**, choose **main**, leave `rc_tag` empty and start the run. No version
lookup or copy/paste is needed. The workflow selects the highest Semver version
and then the highest RC number among published prereleases with `release.json`.
Drafts, old RCs without metadata, and version lines already finalized are skipped.
If no candidate remains, the accepted version may already have a final release.
For new changes, merge a new stage promotion with a release-producing semver
label; for a retry of an existing final promotion, explicitly select its RC.

The selection and links to available candidates appear in the run's job summary.
Selection happens when the run starts; if you accepted a specific RC rather than
whichever eligible RC is newest, enter its tag explicitly. GitHub's workflow
input form does not populate a dynamic list of releases.
To change the RC selected by a failed run, start a new run with the desired input;
rerunning the old run preserves its original input.

Start automatic selection from the CLI:

```sh
gh workflow run final-release.yml --repo sraisl/gh-concept --ref main
```

Or explicitly select an RC, including when retrying a completed promotion:

```sh
gh workflow run final-release.yml --repo sraisl/gh-concept --ref main -f rc_tag=v0.1.0-rc.6
```

This promotes `v0.1.0-rc.6` to `v0.1.0`; no version input is needed. The source
must be a published prerelease with a `release.json` asset from the new pipeline.
Older releases without that record cannot be promoted by this workflow. First
publish and test a new RC containing the updated pipeline.

The workflow checks the RC's Git commit, stage ancestry and recorded image digest.
It creates a final tag on the same commit and a final release draft, then copies
the exact image manifest to `ghcr.io/sraisl/gh-concept:v0.1.0`. No build occurs.
Both image tags resolve to the same digest, and the image's internal version
label intentionally retains its RC value. Only after verifying the final alias
is the final GitHub release published and marked as latest.

The final release includes `release.json`, a link to its source RC, commit and
image references, and generated notes since the preceding final tag. The first
final release aggregates the selected version line's published RC notes. Check
for breaking changes, migrations and known issues before accepting the RC.

RC creation and final promotion share a concurrency group. A failed promotion
can leave a draft/tag; it does not become a published final baseline. Retry the
same RC after fixing the cause. Existing tags are never moved; conflicting
commits or digests fail. Published release records are verified on retries and
are not silently edited. A newer final version prevents promoting an older line.
There is no automatic production deployment or `latest` image alias.
