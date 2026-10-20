#!/usr/bin/env python3
"""Prepare drafts and publish releases only after their image is verified."""
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

registry = importlib.import_module("image-state")
MARKER = "<!-- verified-container -->"


class ApiError(RuntimeError):
    def __init__(self, message):
        super().__init__(message)
        match = re.search(r"HTTP (\d{3})", message)
        self.status = int(match.group(1)) if match else None


def command(args, data=None):
    result = subprocess.run(args, input=data, text=True, capture_output=True)
    if result.returncode:
        raise ApiError(result.stderr)
    return result.stdout


def api(path, method="GET", data=None, paginate=False):
    args = ["gh", "api", path, "--method", method]
    if paginate:
        args += ["--paginate", "--slurp"]
    if data is not None:
        args += ["--input", "-"]
    return json.loads(command(args, json.dumps(data) if data is not None else None))


def listing(repo, resource):
    return [item for page in api(f"repos/{repo}/{resource}?per_page=100", paginate=True)
            for item in page]


def release_for(repo, tag):
    matches = [release for release in listing(repo, "releases") if release["tag_name"] == tag]
    if len(matches) > 1:
        raise ValueError("Multiple releases for the same tag")
    return matches[0] if matches else None


def select_rc(repo, requested=""):
    releases = listing(repo, "releases")
    candidates = [item["tag_name"] for item in releases
                  if not item["draft"] and item["prerelease"]
                  and re.fullmatch(registry.RC_PATTERN, item["tag_name"])
                  and any(asset["name"] == "release.json" for asset in item.get("assets", []))]
    candidates.sort(key=lambda tag: (*version_tuple(tag.split("-rc.")[0]),
                                    int(tag.split("-rc.")[1])), reverse=True)
    if requested:
        if requested not in candidates:
            raise ValueError("Selected RC must be published and contain release.json. "
                             "Available: " + (", ".join(candidates) or "none"))
        return requested, candidates
    finals = [version_tuple(item["tag_name"]) for item in releases
              if not item["draft"] and not item["prerelease"]
              and re.fullmatch(registry.FINAL_PATTERN, item["tag_name"])]
    available = [tag for tag in candidates
                 if not finals or version_tuple(tag.split("-rc.")[0]) > max(finals)]
    if not available:
        raise ValueError("No unpublished final version has an eligible RC with release.json. "
                         "Publish a new RC or specify an RC explicitly to retry a promotion.")
    return available[0], available


def ensure_tag(repo, tag, revision):
    try:
        ref = api(f"repos/{repo}/git/ref/tags/{tag}")["object"]
    except ApiError as error:
        if error.status != 404:
            raise
        api(f"repos/{repo}/git/refs", "POST", {"ref": f"refs/tags/{tag}", "sha": revision})
        return
    while ref["type"] == "tag":
        ref = api(f"repos/{repo}/git/tags/{ref['sha']}")["object"]
    if ref["type"] != "commit" or ref["sha"] != revision:
        raise ValueError("Git tag points to a different commit; refusing to move it")


def ensure_draft(repo, tag, revision, prerelease, source_rc=None):
    ensure_tag(repo, tag, revision)
    existing = release_for(repo, tag)
    if existing:
        if existing["prerelease"] != prerelease:
            raise ValueError("Existing release has a different release type")
        return existing
    params = {"tag_name": tag, "target_commitish": revision}
    initial_notes = None
    if not prerelease:
        finals = [item["name"] for item in listing(repo, "tags")
                  if re.fullmatch(registry.FINAL_PATTERN, item["name"]) and item["name"] != tag]
        # Final notes compare the full release cycle, rather than only the last RC.
        older = [name for name in finals if version_tuple(name) < version_tuple(tag)]
        if older:
            params["previous_tag_name"] = max(older, key=version_tuple)
        elif source_rc:
            candidates = [item for item in listing(repo, "releases")
                          if not item["draft"] and item["prerelease"]
                          and re.fullmatch(registry.RC_PATTERN, item["tag_name"])
                          and item["tag_name"].split("-rc.")[0] == tag
                          and int(item["tag_name"].split("-rc.")[1]) <= int(source_rc.split("-rc.")[1])]
            candidates.sort(key=lambda item: int(item["tag_name"].split("-rc.")[1]))
            initial_notes = "## Initial release\n\n" + "\n\n".join(
                f"### {item['tag_name']}\n\n{(item.get('body') or '').split(MARKER)[0].strip()}"
                for item in candidates)
    notes = ({"body": initial_notes} if initial_notes is not None else
             api(f"repos/{repo}/releases/generate-notes", "POST", params))
    return api(f"repos/{repo}/releases", "POST", {
        "tag_name": tag, "target_commitish": revision, "name": tag,
        "body": notes["body"], "draft": True, "prerelease": prerelease,
    })


def version_tuple(tag):
    return tuple(int(value) for value in tag[1:].split("."))


def prepare_rc(repo, revision, labels):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Expected a full commit SHA")
    semver = [label for label in labels if label.startswith("semver:")]
    if len(semver) != 1 or semver[0] not in {
            "semver:none", "semver:patch", "semver:minor", "semver:major"}:
        raise ValueError("Exactly one supported semver label is required")
    bump = semver[0].split(":")[1]
    if bump == "none":
        return ""
    tags = listing(repo, "tags")
    existing = [item["name"] for item in tags
                if item["commit"]["sha"] == revision and re.fullmatch(registry.RC_PATTERN, item["name"])]
    if len(existing) > 1:
        raise ValueError("Multiple RC tags for the same commit; resolve ambiguity manually")
    published_finals = {item["tag_name"] for item in listing(repo, "releases")
                        if not item["draft"] and not item["prerelease"]}
    # A failed final promotion may leave a tag/draft; it is not a released baseline.
    version_tags = [item["name"] for item in tags
                    if not re.fullmatch(registry.FINAL_PATTERN, item["name"])
                    or item["name"] in published_finals]
    tag = existing[0] if existing else command(
        ["bash", ".github/scripts/next-version.sh", bump],
        "\n".join(version_tags) + "\n").strip()
    if not re.fullmatch(registry.RC_PATTERN, tag):
        raise ValueError("Version calculation did not return an RC tag")
    ensure_draft(repo, tag, revision, prerelease=True)
    return tag


def record_for(repo, tag, revision, state, source_rc=None):
    if state["exists"] != "true":
        raise ValueError("Image is not available")
    return {"schemaVersion": 1, "tag": tag, "commit": revision,
            "image": state["image"], "digest": state["digest"],
            "immutableImage": f"ghcr.io/{repo.lower()}@{state['digest']}",
            "sourceRC": source_rc,
            "workflow": f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}"
                        f"/actions/runs/{os.environ['GITHUB_RUN_ID']}"}


def publish_ready(repo, tag, revision, state, source_rc=None):
    record = record_for(repo, tag, revision, state, source_rc)
    release = release_for(repo, tag)
    if not release:
        raise ValueError("Release draft is missing; prepare it before publishing")
    if release["prerelease"] != (source_rc is None):
        raise ValueError("Unexpected release type")
    if not release["draft"]:
        previous = json.loads(command(["gh", "release", "download", tag, "--repo", repo,
                                       "--pattern", "release.json", "--output", "-"]))
        for key in ["tag", "commit", "image", "digest", "sourceRC"]:
            if previous[key] != record[key]:
                raise ValueError("Published release record differs; refusing to change it")
        print(f"Release {tag} already published and verified")
        return record
    body = release.get("body", "").split(MARKER)[0].rstrip()
    body += (f"\n\n{MARKER}\n## Verified container\n\n"
             f"- Commit: `{revision}`\n- Image: `{record['image']}`\n"
             f"- Digest reference: `{record['immutableImage']}`\n"
             f"- Build/promotion: {record['workflow']}\n")
    if source_rc:
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        body += (f"- Promoted from: [{source_rc}]({server}/{repo}/releases/tag/{source_rc})"
                 " (same image digest; no rebuild)\n")
    with tempfile.TemporaryDirectory() as directory:
        manifest = Path(directory) / "release.json"
        manifest.write_text(json.dumps(record, indent=2) + "\n")
        command(["gh", "release", "upload", tag, str(manifest), "--repo", repo, "--clobber"])
    # Updating notes may be retried safely while the release remains a draft.
    identity = {"tag_name": tag, "target_commitish": revision, "name": tag}
    api(f"repos/{repo}/releases/{release['id']}", "PATCH", {**identity, "body": body})
    published = api(f"repos/{repo}/releases/{release['id']}", "PATCH", {
        **identity, "draft": False, "make_latest": "false" if source_rc is None else "true"})
    if published["tag_name"] != tag or published["draft"]:
        raise ValueError("GitHub did not publish the requested tag; inspect the release before retrying")
    print(f"Published {tag}: {record['immutableImage']}")
    return record


def promote_final(repo, rc, revision, actor, token):
    if not re.fullmatch(registry.RC_PATTERN, rc):
        raise ValueError("Select an RC tag")
    release = release_for(repo, rc)
    if not release or release["draft"] or not release["prerelease"]:
        raise ValueError("Source RC must already be published")
    if not any(asset["name"] == "release.json" for asset in release.get("assets", [])):
        raise ValueError("Selected RC predates verified release metadata (release.json). "
                         "Leave rc_tag empty to select an eligible RC automatically.")
    recorded = json.loads(command(["gh", "release", "download", rc, "--repo", repo,
                                   "--pattern", "release.json", "--output", "-"]))
    if recorded["tag"] != rc or recorded["commit"] != revision:
        raise ValueError("RC tag differs from its published release record")
    final = rc.split("-rc.")[0]
    # Reject historical final promotions that would make version selection ambiguous.
    finals = [item["name"] for item in listing(repo, "tags")
              if re.fullmatch(registry.FINAL_PATTERN, item["name"])]
    if any(version_tuple(tag) > version_tuple(final) for tag in finals):
        raise ValueError("A newer final version already exists")
    ensure_draft(repo, final, revision, prerelease=False, source_rc=rc)
    state = registry.promote_image(repo, rc, revision, recorded["digest"], actor, token)
    return publish_ready(repo, final, revision, state, source_rc=rc)


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Invalid repository")
    mode = sys.argv[1]
    if mode == "select-rc":
        selected, candidates = select_rc(repo, os.environ.get("RC_TAG", "").strip())
        with Path(os.environ["GITHUB_ENV"]).open("a") as env:
            print(f"RC_TAG={selected}", file=env)
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as summary:
            print(f"### RC selection\n\nSelected: **{selected}**\n\nAvailable RCs:\n", file=summary)
            for candidate in candidates:
                print(f"- [{candidate}](https://github.com/{repo}/releases/tag/{candidate})", file=summary)
        print(f"Selected {selected}; available RCs: {', '.join(candidates)}")
    elif mode == "prepare-rc":
        tag = prepare_rc(repo, os.environ["SHA"], json.loads(os.environ["LABELS"]))
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
            print(f"tag={tag}", file=output)
    elif mode == "complete-rc":
        tag, revision = os.environ["TAG"], os.environ["REVISION"]
        state = registry.image_state(repo, tag, revision, os.environ["GITHUB_ACTOR"],
                                     os.environ["GHCR_TOKEN"])
        publish_ready(repo, tag, revision, state)
    elif mode == "promote-final":
        record = promote_final(repo, os.environ["RC_TAG"], os.environ["REVISION"],
                               os.environ["GITHUB_ACTOR"], os.environ["GHCR_TOKEN"])
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as summary:
            print(f"### {record['tag']}\n\n`{record['immutableImage']}`", file=summary)
    else:
        raise ValueError("Unknown release operation")


if __name__ == "__main__":
    main()
