"""Regression tests for safe retries of GHCR publication (no network required)."""
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

spec = importlib.util.spec_from_file_location("image_state", Path(__file__).with_name("image-state.py"))
image_state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(image_state)

REVISION = "a" * 40
DIGEST = "sha256:" + "b" * 64
CONFIG = "sha256:" + "c" * 64
TAG = "v0.1.0-rc.5"


class ImageStateTests(unittest.TestCase):
    def check(self, **overrides):
        args = dict(repository="Sraisl/gh-concept", tag=TAG, revision=REVISION,
                    actor="actor", token="secret")
        args.update(overrides)
        return image_state.image_state(**args)

    @staticmethod
    def replies(revision=REVISION, version=TAG):
        return [({"token": "bearer"}, {}),
                ({"config": {"digest": CONFIG}}, {"Docker-Content-Digest": DIGEST}),
                ({"config": {"Labels": {"org.opencontainers.image.revision": revision,
                                         "org.opencontainers.image.version": version}}}, {})]

    def test_missing_manifest_can_be_published(self):
        with patch.object(image_state, "request", side_effect=[
                ({"token": "bearer"}, {}), HTTPError("registry", 404, "missing", {}, None)]):
            self.assertEqual(self.check(), {"image": f"ghcr.io/sraisl/gh-concept:{TAG}",
                                            "exists": "false", "digest": ""})

    def test_matching_image_is_reused_by_digest(self):
        with patch.object(image_state, "request", side_effect=self.replies()) as call:
            self.assertEqual(self.check()["digest"], DIGEST)
            self.assertIn("/blobs/" + CONFIG, call.call_args.args[0])

    def test_existing_image_marks_publication_unnecessary(self):
        with patch.object(image_state, "request", side_effect=self.replies()):
            self.assertEqual(self.check()["exists"], "true")

    def test_wrong_commit_is_not_overwritten(self):
        with patch.object(image_state, "request", side_effect=self.replies(revision="d" * 40)):
            with self.assertRaisesRegex(ValueError, "different commit"):
                self.check()

    def test_wrong_version_is_not_overwritten(self):
        with patch.object(image_state, "request", side_effect=self.replies(version="v1.0.0-rc.1")):
            with self.assertRaisesRegex(ValueError, "different version"):
                self.check()

    def test_missing_digest_is_not_accepted(self):
        replies = self.replies()
        replies[1] = (replies[1][0], {})
        with patch.object(image_state, "request", side_effect=replies):
            with self.assertRaisesRegex(ValueError, "manifest digest"):
                self.check()

    def test_authentication_failure_is_not_treated_as_missing_image(self):
        for code in [401, 403, 429, 500]:
            with self.subTest(code=code):
                with patch.object(image_state, "request", side_effect=[
                        ({"token": "bearer"}, {}), HTTPError("registry", code, "failed", {}, None)]):
                    with self.assertRaises(HTTPError):
                        self.check()

    def test_token_failure_does_not_permit_publication(self):
        with patch.object(image_state, "request", side_effect=HTTPError("token", 403, "denied", {}, None)):
            with self.assertRaises(HTTPError):
                self.check()

    def test_invalid_inputs_are_rejected_before_network_access(self):
        for args in [{"tag": "main"}, {"tag": "v1.2.3"}, {"tag": "v1.2.3-rc.0"},
                     {"tag": "v01.2.3-rc.1"}, {"tag": TAG + "\n"},
                     {"repository": "../other"}, {"revision": "short"}]:
            with self.subTest(args=args), patch.object(image_state, "request") as call:
                with self.assertRaises(ValueError):
                    self.check(**args)
                call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
