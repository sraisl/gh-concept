"""No-network release lifecycle tests, including partial failures and retries."""
import importlib
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

release = importlib.import_module("release")
REPO = "sraisl/gh-concept"
RC = "v0.1.0-rc.6"
SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64
STATE = {"image": f"ghcr.io/{REPO}:{RC}", "digest": DIGEST, "exists": "true"}
DRAFT = {"id": 123, "tag_name": RC, "body": "## Changes\n- Feature #14",
         "draft": True, "prerelease": True, "assets": [{"name": "release.json"}]}


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"GITHUB_RUN_ID": "456"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_missing_image_does_not_publish(self):
        with patch.object(release, "api") as api:
            with self.assertRaisesRegex(ValueError, "not available"):
                release.publish_ready(REPO, RC, SHA, {**STATE, "exists": "false"})
            api.assert_not_called()

    def test_release_listing_aggregates_pages_including_drafts(self):
        with patch.object(release, "api", return_value=[[DRAFT], [{**DRAFT, "tag_name": "v0.1.0-rc.5"}]]):
            self.assertEqual(len(release.listing(REPO, "releases")), 2)

    def test_first_final_notes_include_earlier_rcs_but_not_later_ones(self):
        candidates = [{**DRAFT, "tag_name": tag, "draft": False, "body": body}
                      for tag, body in [("v0.1.0-rc.1", "Initial feature"),
                                        (RC, "Accepted fix" + release.MARKER + "old image metadata"),
                                        ("v0.1.0-rc.99", "Not accepted")]]
        with patch.object(release, "ensure_tag"), \
                patch.object(release, "release_for", return_value=None), \
                patch.object(release, "listing", side_effect=lambda repo, resource:
                             [] if resource == "tags" else candidates), \
                patch.object(release, "api", return_value={}) as api:
            release.ensure_draft(REPO, "v0.1.0", SHA, False, source_rc=RC)
            data = api.call_args.args[2]
            self.assertIn("Initial feature", data["body"])
            self.assertIn("Accepted fix", data["body"])
            self.assertNotIn("Not accepted", data["body"])
            self.assertNotIn("old image metadata", data["body"])
            self.assertTrue(data["draft"])
            self.assertFalse(data["prerelease"])

    def test_subsequent_final_notes_compare_previous_final(self):
        with patch.object(release, "ensure_tag"), \
                patch.object(release, "release_for", return_value=None), \
                patch.object(release, "listing", return_value=[{"name": "v0.0.1"}, {"name": "v0.0.2"}]), \
                patch.object(release, "api", side_effect=[{"body": "Full release changes"}, {}]) as api:
            release.ensure_draft(REPO, "v0.1.0", SHA, False, source_rc=RC)
            self.assertEqual(api.call_args_list[0].args[2]["previous_tag_name"], "v0.0.2")

    def test_draft_uploads_record_before_publishing(self):
        order = []
        def upload(args, data=None):
            self.assertEqual(args[:3], ["gh", "release", "upload"])
            record = json.loads(Path(args[4]).read_text())
            self.assertEqual(record["digest"], DIGEST)
            self.assertEqual(record["commit"], SHA)
            order.append("upload")
        def api(path, method="GET", data=None, **kwargs):
            order.append("publish" if data and data.get("draft") is False else "notes")
            self.assertEqual(data["tag_name"], RC)
            self.assertEqual(data["target_commitish"], SHA)
            return {"tag_name": RC, "draft": False}
        with patch.object(release, "release_for", return_value=DRAFT), \
                patch.object(release, "command", side_effect=upload), \
                patch.object(release, "api", side_effect=api):
            release.publish_ready(REPO, RC, SHA, STATE)
        self.assertEqual(order, ["upload", "notes", "publish"])

    def test_upload_failure_keeps_draft_unpublished(self):
        with patch.object(release, "release_for", return_value=DRAFT), \
                patch.object(release, "command", side_effect=RuntimeError("upload failed")), \
                patch.object(release, "api") as api:
            with self.assertRaises(RuntimeError):
                release.publish_ready(REPO, RC, SHA, STATE)
            api.assert_not_called()

    def test_notes_failure_does_not_publish(self):
        with patch.object(release, "release_for", return_value=DRAFT), \
                patch.object(release, "command", return_value=""), \
                patch.object(release, "api", side_effect=RuntimeError("notes failed")) as api:
            with self.assertRaises(RuntimeError):
                release.publish_ready(REPO, RC, SHA, STATE)
            self.assertEqual(api.call_count, 1)
            self.assertNotIn("draft", api.call_args.args[2])

    def test_retry_does_not_duplicate_notes(self):
        draft = {**DRAFT, "body": DRAFT["body"] + "\n" + release.MARKER + "\nold metadata"}
        with patch.object(release, "release_for", return_value=draft), \
                patch.object(release, "command", return_value=""), \
                patch.object(release, "api", return_value={"tag_name": RC, "draft": False}) as api:
            release.publish_ready(REPO, RC, SHA, STATE)
            body = api.call_args_list[0].args[2]["body"]
            self.assertEqual(body.count(release.MARKER), 1)
            self.assertIn("Feature #14", body)
            self.assertNotIn("old metadata", body)

    def test_untagged_publication_is_not_reported_as_success(self):
        with patch.object(release, "release_for", return_value=DRAFT), \
                patch.object(release, "command", return_value=""), \
                patch.object(release, "api", return_value={"tag_name": "untagged-123", "draft": False}):
            with self.assertRaisesRegex(ValueError, "requested tag"):
                release.publish_ready(REPO, RC, SHA, STATE)

    def test_final_publication_keeps_final_tag_and_records_source_rc(self):
        final = "v0.1.0"
        draft = {**DRAFT, "tag_name": final, "prerelease": False}
        state = {**STATE, "image": f"ghcr.io/{REPO}:{final}"}
        with patch.object(release, "release_for", return_value=draft), \
                patch.object(release, "command", return_value=""), \
                patch.object(release, "api", return_value={"tag_name": final, "draft": False}) as api:
            record = release.publish_ready(REPO, final, SHA, state, source_rc=RC)
            self.assertEqual(record["sourceRC"], RC)
            self.assertEqual(record["digest"], DIGEST)
            for call in api.call_args_list:
                self.assertEqual(call.args[2]["tag_name"], final)
                self.assertEqual(call.args[2]["target_commitish"], SHA)
            self.assertEqual(api.call_args.args[2]["make_latest"], "true")

    def test_published_retry_only_verifies_record(self):
        previous = release.record_for(REPO, RC, SHA, STATE)
        with patch.object(release, "release_for", return_value={**DRAFT, "draft": False}), \
                patch.object(release, "command", return_value=json.dumps(previous)), \
                patch.object(release, "api") as api:
            release.publish_ready(REPO, RC, SHA, STATE)
            api.assert_not_called()

    def test_published_release_with_different_digest_is_rejected(self):
        previous = {**release.record_for(REPO, RC, SHA, STATE), "digest": "sha256:" + "c" * 64}
        with patch.object(release, "release_for", return_value={**DRAFT, "draft": False}), \
                patch.object(release, "command", return_value=json.dumps(previous)), \
                patch.object(release, "api") as api:
            with self.assertRaisesRegex(ValueError, "differs"):
                release.publish_ready(REPO, RC, SHA, STATE)
            api.assert_not_called()

    def test_wrong_git_tag_is_never_moved(self):
        with patch.object(release, "api", return_value={"object": {"type": "commit", "sha": "c" * 40}}) as api:
            with self.assertRaisesRegex(ValueError, "different commit"):
                release.ensure_tag(REPO, RC, SHA)
            self.assertEqual(api.call_count, 1)

    def test_missing_git_tag_is_created(self):
        with patch.object(release, "api", side_effect=[release.ApiError("HTTP 404"), {}]) as api:
            release.ensure_tag(REPO, RC, SHA)
            self.assertEqual(api.call_args.args[1], "POST")
            self.assertEqual(api.call_args.args[2]["sha"], SHA)

    def test_git_api_failure_is_not_missing_tag(self):
        with patch.object(release, "api", side_effect=release.ApiError("HTTP 403")) as api:
            with self.assertRaises(release.ApiError):
                release.ensure_tag(REPO, RC, SHA)
            self.assertEqual(api.call_count, 1)

    def test_annotated_git_tag_is_dereferenced(self):
        with patch.object(release, "api", side_effect=[
                {"object": {"type": "tag", "sha": "c" * 40}},
                {"object": {"type": "commit", "sha": SHA}}]) as api:
            release.ensure_tag(REPO, RC, SHA)
            self.assertEqual(api.call_count, 2)

    def test_semver_none_creates_nothing(self):
        with patch.object(release, "listing") as listing:
            self.assertEqual(release.prepare_rc(REPO, SHA, ["semver:none"]), "")
            listing.assert_not_called()

    def test_invalid_labels_create_nothing(self):
        with patch.object(release, "listing") as listing:
            for labels in [[], ["semver:banana"], ["semver:patch", "semver:minor"]]:
                with self.subTest(labels=labels), self.assertRaises(ValueError):
                    release.prepare_rc(REPO, SHA, labels)
            listing.assert_not_called()

    def test_rc_retry_reuses_tag_and_draft(self):
        def listing(repo, resource):
            return [{"name": RC, "commit": {"sha": SHA}}] if resource == "tags" else []
        with patch.object(release, "listing", side_effect=listing), \
                patch.object(release, "ensure_draft") as draft, \
                patch.object(release, "command") as command:
            self.assertEqual(release.prepare_rc(REPO, SHA, ["semver:patch"]), RC)
            command.assert_not_called()
            draft.assert_called_once_with(REPO, RC, SHA, prerelease=True)

    def test_unpublished_final_is_not_a_version_baseline(self):
        def listing(repo, resource):
            if resource == "tags":
                return [{"name": tag, "commit": {"sha": "c" * 40}}
                        for tag in ["v0.1.0", "v9.0.0", "v0.1.1-rc.1"]]
            return [{"tag_name": "v0.1.0", "draft": False, "prerelease": False},
                    {"tag_name": "v9.0.0", "draft": True, "prerelease": False}]
        with patch.object(release, "listing", side_effect=listing), \
                patch.object(release, "ensure_draft"), \
                patch.object(release, "command", return_value="v0.1.1-rc.2\n") as command:
            release.prepare_rc(REPO, SHA, ["semver:patch"])
            self.assertNotIn("v9.0.0", command.call_args.args[1])
            self.assertIn("v0.1.0\n", command.call_args.args[1])

    def test_unpublished_rc_cannot_be_promoted(self):
        for candidate in [None, DRAFT, {**DRAFT, "draft": False, "prerelease": False}]:
            with self.subTest(candidate=candidate), patch.object(release, "release_for", return_value=candidate), \
                    patch.object(release.registry, "promote_image") as promote:
                with self.assertRaisesRegex(ValueError, "already be published"):
                    release.promote_final(REPO, RC, SHA, "actor", "token")
                promote.assert_not_called()

    def test_changed_rc_git_tag_cannot_be_promoted(self):
        record = {"tag": RC, "commit": "c" * 40}
        with patch.object(release, "release_for", return_value={**DRAFT, "draft": False}), \
                patch.object(release, "command", return_value=json.dumps(record)), \
                patch.object(release.registry, "promote_image") as promote:
            with self.assertRaisesRegex(ValueError, "differs"):
                release.promote_final(REPO, RC, SHA, "actor", "token")
            promote.assert_not_called()

    def test_legacy_rc_reports_missing_metadata_before_download(self):
        with patch.object(release, "release_for", return_value={**DRAFT, "draft": False, "assets": []}), \
                patch.object(release, "command") as command:
            with self.assertRaisesRegex(ValueError, "Leave rc_tag empty"):
                release.promote_final(REPO, RC, SHA, "actor", "token")
            command.assert_not_called()

    def test_final_promotes_recorded_digest_without_building(self):
        record = release.record_for(REPO, RC, SHA, STATE)
        state = {**STATE, "image": f"ghcr.io/{REPO}:v0.1.0"}
        with patch.object(release, "release_for", return_value={**DRAFT, "draft": False}), \
                patch.object(release, "command", return_value=json.dumps(record)) as command, \
                patch.object(release, "listing", return_value=[]), \
                patch.object(release, "ensure_draft") as draft, \
                patch.object(release.registry, "promote_image", return_value=state) as promote, \
                patch.object(release, "publish_ready", return_value=record) as publish:
            release.promote_final(REPO, RC, SHA, "actor", "token")
            draft.assert_called_once_with(REPO, "v0.1.0", SHA, prerelease=False, source_rc=RC)
            promote.assert_called_once_with(REPO, RC, SHA, DIGEST, "actor", "token")
            publish.assert_called_once_with(REPO, "v0.1.0", SHA, state, source_rc=RC)
            self.assertEqual(command.call_count, 1)  # Download release record only.

    def test_failed_image_promotion_does_not_publish_final(self):
        record = release.record_for(REPO, RC, SHA, STATE)
        with patch.object(release, "release_for", return_value={**DRAFT, "draft": False}), \
                patch.object(release, "command", return_value=json.dumps(record)), \
                patch.object(release, "listing", return_value=[]), \
                patch.object(release, "ensure_draft"), \
                patch.object(release.registry, "promote_image", side_effect=ValueError("wrong digest")), \
                patch.object(release, "publish_ready") as publish:
            with self.assertRaises(ValueError):
                release.promote_final(REPO, RC, SHA, "actor", "token")
            publish.assert_not_called()


class RcSelectionTests(unittest.TestCase):
    @staticmethod
    def candidate(tag, **overrides):
        return {**DRAFT, "tag_name": tag, "draft": False, **overrides}

    def select(self, releases, requested=""):
        with patch.object(release, "listing", return_value=releases):
            return release.select_rc(REPO, requested)

    def test_automatic_selection_sorts_rc_numbers_numerically(self):
        selected, candidates = self.select([self.candidate(tag) for tag in
                                            ["v0.1.0-rc.9", "v0.1.0-rc.10", "v0.1.0-rc.6"]])
        self.assertEqual(selected, "v0.1.0-rc.10")
        self.assertEqual(candidates[0], selected)

    def test_version_line_takes_precedence_over_rc_count(self):
        selected, _ = self.select([self.candidate(tag) for tag in
                                  ["v0.9.0-rc.99", "v0.10.0-rc.1"]])
        self.assertEqual(selected, "v0.10.0-rc.1")

    def test_drafts_legacy_and_non_rc_releases_are_excluded(self):
        selected, candidates = self.select([
            self.candidate("v9.0.0-rc.1", draft=True),
            self.candidate("v8.0.0-rc.1", assets=[]),
            self.candidate("v7.0.0-rc.1", prerelease=False),
            self.candidate("untagged-123"), self.candidate(RC)])
        self.assertEqual(selected, RC)
        self.assertEqual(candidates, [RC])

    def test_published_final_lines_are_skipped_automatically(self):
        selected, candidates = self.select([
            self.candidate("v0.1.0-rc.6"), self.candidate("v0.1.1-rc.1"),
            self.candidate("v0.1.0", prerelease=False)])
        self.assertEqual(selected, "v0.1.1-rc.1")
        self.assertEqual(candidates, [selected])

    def test_explicit_rc_can_be_selected_for_retry(self):
        selected, _ = self.select([
            self.candidate(RC), self.candidate("v0.1.0", prerelease=False)], requested=RC)
        self.assertEqual(selected, RC)

    def test_unknown_or_legacy_explicit_rc_lists_eligible_alternatives(self):
        with self.assertRaisesRegex(ValueError, "Available: " + RC):
            self.select([self.candidate(RC)], requested="v0.1.0-rc.5")

    def test_no_candidates_requires_a_new_rc(self):
        with self.assertRaisesRegex(ValueError, "Publish a new RC"):
            self.select([self.candidate(RC, assets=[])])

    def test_finalized_only_line_is_not_silently_promoted_again(self):
        with self.assertRaisesRegex(ValueError, "specify an RC explicitly"):
            self.select([self.candidate(RC), self.candidate("v0.1.0", prerelease=False)])


if __name__ == "__main__":
    unittest.main()
