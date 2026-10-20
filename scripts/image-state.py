#!/usr/bin/env python3
"""Check a GHCR RC tag without treating authentication failures as missing tags."""
import base64
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def request(url, headers):
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        return json.load(response), response.headers


RC_PATTERN = r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-rc\.[1-9][0-9]*"
FINAL_PATTERN = r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"


def registry_headers(repository, actor, token):
    credentials = base64.b64encode(f"{actor}:{token}".encode()).decode()
    query = urlencode({"service": "ghcr.io", "scope": f"repository:{repository}:pull,push"})
    auth, _ = request(f"https://ghcr.io/token?{query}", {"Authorization": f"Basic {credentials}"})
    return {"Authorization": f"Bearer {auth['token']}", "Accept": ", ".join([
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ])}


def image_state(repository, tag, revision, actor, token, expected_version=None):
    if not re.fullmatch(RC_PATTERN, tag) and not (
            expected_version and re.fullmatch(FINAL_PATTERN, tag)
            and re.fullmatch(RC_PATTERN, expected_version)
            and expected_version.split("-rc.")[0] == tag):
        raise ValueError("Expected an RC tag or its matching final promotion")
    if (not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
            or any(part in {".", ".."} for part in repository.split("/"))):
        raise ValueError("Expected owner/repository")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Expected a full commit SHA")
    repository = repository.lower()
    image = f"ghcr.io/{repository}:{tag}"
    headers = registry_headers(repository, actor, token)
    try:
        manifest, metadata = request(f"https://ghcr.io/v2/{repository}/manifests/{tag}", headers)
    except HTTPError as error:
        if error.code != 404:
            raise
        return {"image": image, "exists": "false", "digest": ""}
    digest = metadata.get("Docker-Content-Digest", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Registry did not return a valid manifest digest")
    config_digest = manifest["config"]["digest"]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", config_digest):
        raise ValueError("Registry did not return a valid config digest")
    config, _ = request(f"https://ghcr.io/v2/{repository}/blobs/{config_digest}", headers)
    labels = config.get("config", {}).get("Labels", {})
    if labels.get("org.opencontainers.image.revision") != revision:
        raise ValueError("Existing image belongs to a different commit; refusing to overwrite")
    if labels.get("org.opencontainers.image.version") != (expected_version or tag):
        raise ValueError("Existing image has a different version; refusing to overwrite")
    return {"image": image, "exists": "true", "digest": digest}


def promote_image(repository, rc, revision, digest, actor, token):
    """Copy the manifest bytes, preserving the tested RC digest and config."""
    if not re.fullmatch(RC_PATTERN, rc):
        raise ValueError("Expected an RC tag")
    final = rc.split("-rc.")[0]
    source = image_state(repository, rc, revision, actor, token)
    if source["exists"] != "true" or source["digest"] != digest:
        raise ValueError("RC image differs from its published release record")
    target = image_state(repository, final, revision, actor, token, expected_version=rc)
    if target["exists"] == "true":
        if target["digest"] != digest:
            raise ValueError("Final image already has a different digest")
        return target
    repository = repository.lower()
    headers = registry_headers(repository, actor, token)
    url = f"https://ghcr.io/v2/{repository}/manifests/"
    with urlopen(Request(url + digest, headers=headers), timeout=30) as response:
        manifest = response.read()
        content_type = response.headers["Content-Type"]
    if "sha256:" + hashlib.sha256(manifest).hexdigest() != digest:
        raise ValueError("Downloaded manifest does not match the RC digest")
    headers["Content-Type"] = content_type
    with urlopen(Request(url + final, data=manifest, headers=headers, method="PUT"), timeout=30) as response:
        if response.status != 201:
            raise ValueError("Registry did not confirm final manifest creation")
    target = image_state(repository, final, revision, actor, token, expected_version=rc)
    if target["exists"] != "true" or target["digest"] != digest:
        raise ValueError("Final image digest differs from RC after promotion")
    return target


if __name__ == "__main__":
    state = image_state(os.environ["GITHUB_REPOSITORY"], os.environ["TAG"],
                        os.environ["REVISION"], os.environ["GITHUB_ACTOR"],
                        os.environ["GHCR_TOKEN"])
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        for key, value in state.items():
            print(f"{key}={value}", file=output)
    print(json.dumps(state))
