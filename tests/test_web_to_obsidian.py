import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest import mock

import yaml

import web_to_obsidian as clip


SUCCESS = {
    "ok": True,
    "title": "An Article",
    "author": "Ada",
    "published": "2026-07-23",
    "description": "A useful page",
    "site": "Example",
    "canonicalUrl": "https://example.com/article",
    "url": "https://example.com/article",
    "keywords": ["security", "clipping"],
    "markdown": "# An Article\r\n\r\nBody\r\n",
    "wordCount": 3,
    "method": "readability",
}


class ArgsTests(unittest.TestCase):
    def test_accepts_one_http_url_and_supported_flags(self):
        parsed = clip.parse_clip_args(
            '"https://example.com/a?x=1&y=2" --no-browser --no-git'
        )
        self.assertEqual(parsed.url, "https://example.com/a?x=1&y=2")
        self.assertTrue(parsed.no_browser)
        self.assertTrue(parsed.no_git)
        self.assertEqual(parsed.save_images, "ask")

    def test_accepts_save_images_modes(self):
        for mode in ("yes", "no", "ask"):
            with self.subTest(mode=mode):
                parsed = clip.parse_clip_args(
                    f"https://example.com/article --save-images {mode}"
                )
                self.assertEqual(parsed.save_images, mode)

    def test_rejects_unknown_option_extra_url_and_non_http_url(self):
        invalid = (
            "https://example.com --wat",
            "https://example.com https://other.example",
            "file:///etc/passwd",
            "",
            "https://example.com --save-images maybe",
        )
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(clip.ClipError):
                clip.parse_clip_args(raw)

    def test_rejects_malformed_shell_quoting(self):
        with self.assertRaises(clip.ClipError):
            clip.parse_clip_args("'https://example.com")


class ConfigAndFilenameTests(unittest.TestCase):
    def test_destination_must_resolve_inside_vault(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            with self.assertRaises(clip.ClipError):
                clip.ClipConfig.from_env(
                    {
                        "WEB_TO_OBSIDIAN_VAULT": str(vault),
                        "WEB_TO_OBSIDIAN_DEST": "../escape",
                    }
                )

    def test_toml_config_loads_and_lock_must_remain_outside_vault(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            vault.mkdir()
            config_path = root / "config.toml"
            config_path.write_text(
                "[clip]\n"
                f'vault = "{vault}"\n'
                'destination = "Inbox"\n'
                'images = "images"\n'
                'sync_branch = "master"\n'
                f'lock_file = "{root / "vault.lock"}"\n',
                encoding="utf-8",
            )
            config = clip.ClipConfig.from_file(config_path)
            self.assertEqual(config.destination, vault / "Inbox")
            self.assertEqual(config.sync_branch, "master")

            with self.assertRaisesRegex(clip.ClipError, "lock file"):
                clip.ClipConfig.from_env(
                    {
                        "WEB_TO_OBSIDIAN_VAULT": str(vault),
                        "WEB_TO_OBSIDIAN_LOCK_FILE": str(vault / "bad.lock"),
                    }
                )

    def test_default_destination_is_inbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            config = clip.ClipConfig.from_env({"WEB_TO_OBSIDIAN_VAULT": str(vault)})
            self.assertEqual(config.destination, vault / "Inbox")

    def test_filename_removes_traversal_controls_and_forbidden_characters(self):
        name = clip.safe_filename("  ../A:<B>\\C/\x00\n...  ", "https://example.com/x")
        self.assertTrue(name.endswith(".md"))
        self.assertNotIn("/", name)
        self.assertNotIn("\\", name)
        self.assertNotIn("..", name)
        self.assertNotIn(":", name)
        self.assertNotIn("\x00", name)

    def test_filename_avoids_reserved_dos_basename(self):
        for title in ("CON", "con.txt", "LPT1", "aux"):
            with self.subTest(title=title):
                name = clip.safe_filename(title, "https://example.com/reserved")
                self.assertFalse(name.removesuffix(".md").split(".", 1)[0].upper() in clip.DOS_RESERVED)

    def test_filename_caps_utf8_without_splitting_unicode(self):
        name = clip.safe_filename("界" * 200, "https://example.com/unicode", max_bytes=47)
        name.encode("utf-8")
        self.assertLessEqual(len(name.encode("utf-8")), 47)
        self.assertTrue(name.endswith(".md"))

    def test_empty_sanitized_title_uses_hash_fallback(self):
        first = clip.safe_filename("////", "https://example.com/fallback")
        second = clip.safe_filename("////", "https://example.com/fallback")
        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{12}\.md$")


class RenderingTests(unittest.TestCase):
    def test_yaml_quotes_injection_strings_and_normalizes_line_endings(self):
        data = dict(SUCCESS)
        data["title"] = 'Title"\n---\nevil: true'
        data["description"] = "line one\r\nline two"
        note = clip.render_note(data, created="2026-07-23T12:00:00+00:00")

        self.assertNotIn("\r", note)
        self.assertTrue(note.startswith("---\n"))
        frontmatter, _ = note[4:].split("\n---\n", 1)
        parsed = yaml.safe_load(frontmatter)
        self.assertEqual(
            list(parsed),
            [
                "title",
                "url",
                "author",
                "site",
                "description",
                "keywords",
                "tags",
                "original_url",
                "original_host",
                "extraction_method",
                "status",
                "category",
                "word_count",
                "webclip_id",
                "source_content_hash",
                "content_hash",
                "published",
                "created",
            ],
        )
        self.assertEqual(parsed["title"], data["title"])
        self.assertEqual(parsed["url"], "https://example.com/article")
        self.assertEqual(parsed["original_url"], "https://example.com/article")
        self.assertEqual(parsed["original_host"], "example.com")
        self.assertEqual(parsed["keywords"], ["security", "clipping"])
        self.assertEqual(parsed["category"], "Inbox")
        self.assertEqual(parsed["created"], "2026-07-23T12:00:00+00:00")
        self.assertEqual(parsed["extraction_method"], "readability")
        self.assertEqual(parsed["tags"], ["web-clip"])
        self.assertNotIn("image_mode", parsed)
        self.assertEqual(parsed["source_content_hash"], parsed["content_hash"])
        self.assertNotIn("source", parsed)
        self.assertNotIn("source_host", parsed)
        self.assertEqual(note.count("\n---\n"), 1)

    def test_render_note_accepts_prevalidated_extractor_payload_without_ok_flag(self):
        validated = clip._validate_success_payload(SUCCESS)
        note = clip.render_note(validated, created="2026-07-23T12:00:00+00:00")
        self.assertIn("title: An Article", note)
        self.assertIn("keywords:", note)
        self.assertEqual(note.count("\n# An Article\n\n"), 1)

    def test_render_note_includes_remote_image_mode_when_requested(self):
        note = clip.render_note(
            SUCCESS,
            created="2026-07-23T12:00:00+00:00",
            image_mode="remote",
        )

        frontmatter, _ = note[4:].split("\n---\n", 1)
        parsed = yaml.safe_load(frontmatter)
        self.assertEqual(parsed["image_mode"], "remote")

    def test_render_note_includes_local_image_mode_when_requested(self):
        note = clip.render_note(
            SUCCESS,
            created="2026-07-23T12:00:00+00:00",
            image_mode="local",
        )

        frontmatter, _ = note[4:].split("\n---\n", 1)
        parsed = yaml.safe_load(frontmatter)
        self.assertEqual(parsed["image_mode"], "local")

    def test_render_note_injects_h1_when_markdown_has_no_h1(self):
        data = dict(SUCCESS, markdown="## Intro\n\nBody\n")
        note = clip.render_note(data, created="2026-07-23T12:00:00+00:00")
        self.assertIn(
            "<!-- webclip:managed:start -->\n# An Article\n\n## Intro\n\nBody\n",
            note,
        )

    def test_render_note_sanitizes_and_single_lines_injected_h1_title(self):
        data = dict(
            SUCCESS,
            title="Bad [[vault]]\n<script>alert(1)</script>",
            markdown="## Intro\n\nBody\n",
        )
        note = clip.render_note(data, created="2026-07-23T12:00:00+00:00")
        managed = note.split("<!-- webclip:managed:start -->\n", 1)[1].split(
            "\n<!-- webclip:managed:end -->", 1
        )[0]
        self.assertEqual(managed, "# Bad \\[\\[vault]]\n\n## Intro\n\nBody")
        self.assertNotIn("[[vault]]", managed)
        self.assertNotIn("<script>", managed)
        self.assertNotIn("# Bad \\[\\[vault]]\n<script>", managed)

    def test_render_note_preserves_existing_h1_without_duplicate_insertion(self):
        note = clip.render_note(SUCCESS, created="2026-07-23T12:00:00+00:00")
        self.assertIn("<!-- webclip:managed:start -->\n# An Article\n\nBody\n", note)
        self.assertEqual(note.count("\n# An Article\n\n"), 1)

    def test_render_note_preserves_setext_h1_without_duplicate_insertion(self):
        data = dict(SUCCESS, markdown="An Article\n===\n\nBody\n")
        note = clip.render_note(data, created="2026-07-23T12:00:00+00:00")
        self.assertIn(
            "<!-- webclip:managed:start -->\nAn Article\n===\n\nBody\n",
            note,
        )
        self.assertEqual(note.count("\n# An Article\n\n"), 0)

    def test_render_note_ignores_indented_code_when_deciding_to_inject_h1(self):
        data = dict(SUCCESS, markdown="    # not a heading\n\nBody\n")
        note = clip.render_note(data, created="2026-07-23T12:00:00+00:00")
        self.assertIn(
            "<!-- webclip:managed:start -->\n# An Article\n\n    # not a heading\n\nBody\n",
            note,
        )

    def test_render_note_preserves_fetched_url_when_request_url_differs(self):
        data = dict(
            SUCCESS,
            canonicalUrl="https://example.com/article",
            url="https://example.com/from-redirect",
        )
        note = clip.render_note(data, created="2026-07-23T12:00:00+00:00")

        frontmatter, _ = note[4:].split("\n---\n", 1)
        parsed = yaml.safe_load(frontmatter)
        self.assertEqual(
            list(parsed),
            [
                "title",
                "url",
                "author",
                "site",
                "description",
                "keywords",
                "tags",
                "original_url",
                "original_host",
                "fetched_url",
                "extraction_method",
                "status",
                "category",
                "word_count",
                "webclip_id",
                "source_content_hash",
                "content_hash",
                "published",
                "created",
            ],
        )
        self.assertEqual(parsed["fetched_url"], "https://example.com/from-redirect")
        self.assertNotIn("image_mode", parsed)


class ImageWorkflowHelpersTests(unittest.TestCase):
    def test_detects_remote_images_only_from_native_markdown_image_syntax(self):
        markdown = (
            "![one](https://cdn.example/a.png)\n"
            "<img src=\"https://cdn.example/b.jpeg\" alt=\"two\">\n"
            "![local](images/a.png)\n"
            "[link](https://cdn.example/not-an-image)\n"
        )
        self.assertEqual(
            clip.find_remote_images(markdown),
            ["https://cdn.example/a.png"],
        )

    def test_sanitize_markdown_keeps_html_images_out_of_remote_detection(self):
        sanitized = clip.sanitize_markdown(
            '<p>Lead</p><img src="https://cdn.example/hero.png" alt="Hero image">'
        )
        self.assertNotIn("![Hero image](https://cdn.example/hero.png)", sanitized)
        self.assertIn('&lt;img src="https://cdn.example/hero.png" alt="Hero image">', sanitized)
        self.assertEqual(clip.find_remote_images(sanitized), [])

    def test_localize_images_preserves_date_prefix_in_note_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            images_root = root / "images"
            inbox = root / "Inbox"
            inbox.mkdir()
            note_one = inbox / "2026-07-25-Same Title.md"
            note_two = inbox / "2026-07-25-Same Title-deadbeef.md"
            payloads = iter((b"first-image", b"second-image"))

            def fake_download(url: str, destination_dir: Path, index: int) -> Path:
                self.assertEqual(url, "https://cdn.example/image.png")
                destination_dir.mkdir(parents=True, exist_ok=True)
                target = destination_dir / f"{index:02d}-image.png"
                target.write_bytes(next(payloads))
                return target

            markdown = "![x](https://cdn.example/image.png)\n"
            with mock.patch.object(clip, "download_remote_image", side_effect=fake_download):
                rewritten_one, paths_one = clip._localize_images(
                    markdown,
                    "Same Title",
                    "https://example.com/one",
                    images_root,
                    note_one,
                )
                rewritten_two, paths_two = clip._localize_images(
                    markdown,
                    "Same Title",
                    "https://example.com/two",
                    images_root,
                    note_two,
                )

            self.assertEqual(paths_one[0].parent.name, "2026-07-25-same-title")
            self.assertEqual(paths_two[0].parent.name, "2026-07-25-same-title-deadbeef")
            self.assertNotEqual(paths_one[0], paths_two[0])
            self.assertEqual(paths_one[0].read_bytes(), b"first-image")
            self.assertEqual(paths_two[0].read_bytes(), b"second-image")
            self.assertIn("![x](../images/2026-07-25-same-title/01-image.png)", rewritten_one)
            self.assertIn("![x](../images/2026-07-25-same-title-deadbeef/01-image.png)", rewritten_two)

    def test_builds_github_blob_url_for_master_branch(self):
        self.assertEqual(
            clip.github_blob_url(
                "https://github.com/shijistar/obsidian.git",
                "master",
                "Inbox/2026-07-25-An Article.md",
            ),
            "https://github.com/shijistar/obsidian/blob/master/Inbox/2026-07-25-An%20Article.md",
        )


class TargetAndAtomicWriteTests(unittest.TestCase):
    def _note(self, source, title="Old"):
        data = dict(SUCCESS, title=title, canonicalUrl=source, url=source)
        return clip.render_note(data, created="2026-07-23T12:00:00+00:00")

    def test_same_normalized_source_overwrites_same_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp)
            target = destination / clip.safe_filename("An Article", SUCCESS["canonicalUrl"])
            target.write_text(self._note("HTTPS://EXAMPLE.COM:443/article#old"), encoding="utf-8")

            chosen = clip.choose_target(
                destination, "An Article", "https://example.com/article"
            )
            self.assertEqual(chosen, target)

    def test_new_target_uses_capture_date_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp)
            chosen = clip.choose_target(
                destination,
                "An Article",
                "https://example.com/article",
                capture_date="2026-07-23",
            )
            self.assertEqual(chosen.name, "2026-07-23-An Article.md")

    def test_same_source_on_a_later_date_reuses_existing_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp)
            existing = destination / "2026-07-22-An Article.md"
            existing.write_text(
                self._note("https://example.com/article"), encoding="utf-8"
            )

            chosen = clip.choose_target(
                destination,
                "Renamed Article",
                "https://example.com/article",
                capture_date="2026-07-23",
            )
            self.assertEqual(chosen, existing)

    def test_choose_target_accepts_existing_notes_with_original_url_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp)
            existing = destination / "2026-07-22-An Article.md"
            existing.write_text(
                "---\n"
                "title: An Article\n"
                "original_url: https://example.com/article\n"
                "---\n\n"
                "# An Article\n",
                encoding="utf-8",
            )

            chosen = clip.choose_target(
                destination,
                "An Article",
                "https://example.com/article",
                capture_date="2026-07-23",
            )
            self.assertEqual(chosen, existing)

    def test_choose_target_accepts_existing_notes_with_legacy_source_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp)
            existing = destination / "2026-07-22-An Article.md"
            existing.write_text(
                "---\n"
                "title: An Article\n"
                "source: https://example.com/article\n"
                "---\n\n"
                "# An Article\n",
                encoding="utf-8",
            )

            chosen = clip.choose_target(
                destination,
                "An Article",
                "https://example.com/article",
                capture_date="2026-07-23",
            )
            self.assertEqual(chosen, existing)

    def test_multiple_existing_notes_for_same_source_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp)
            for day in ("2026-07-21", "2026-07-22"):
                (destination / f"{day}-An Article.md").write_text(
                    self._note("https://example.com/article"), encoding="utf-8"
                )

            with self.assertRaisesRegex(clip.ClipError, "multiple"):
                clip.choose_target(
                    destination,
                    "An Article",
                    "https://example.com/article",
                    capture_date="2026-07-23",
                )

    def test_title_collision_with_different_source_adds_url_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp)
            base = destination / clip.safe_filename(
                "2026-07-23-An Article", "https://one.example"
            )
            base.write_text(self._note("https://one.example/"), encoding="utf-8")

            chosen1 = clip.choose_target(
                destination,
                "An Article",
                "https://two.example",
                capture_date="2026-07-23",
            )
            chosen2 = clip.choose_target(
                destination,
                "An Article",
                "https://two.example",
                capture_date="2026-07-23",
            )
            self.assertEqual(chosen1, chosen2)
            self.assertNotEqual(chosen1, base)
            self.assertRegex(
                chosen1.name, r"^2026-07-23-An Article-[0-9a-f]{8}\.md$"
            )
            self.assertEqual(chosen1.parent, destination)

    def test_existing_symlink_escaping_destination_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = root / "dest"
            destination.mkdir()
            outside = root / "outside.md"
            outside.write_text(self._note("https://example.com/article"), encoding="utf-8")
            target = destination / clip.safe_filename("An Article", SUCCESS["canonicalUrl"])
            target.symlink_to(outside)
            with self.assertRaises(clip.ClipError):
                clip.choose_target(destination, "An Article", SUCCESS["canonicalUrl"])

    def test_atomic_write_replaces_target_and_uses_same_directory_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "note.md"
            target.write_text("old", encoding="utf-8")
            real_replace = os.replace
            calls = []

            def recording_replace(source, destination):
                calls.append((Path(source), Path(destination)))
                return real_replace(source, destination)

            with mock.patch.object(clip.os, "replace", side_effect=recording_replace):
                clip.atomic_write(target, "new\n")

            self.assertEqual(target.read_text(encoding="utf-8"), "new\n")
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0].parent, target.parent)
            self.assertEqual(calls[0][1], target)
            self.assertFalse(any(p.name.startswith(".clip-") for p in target.parent.iterdir()))


class ExtractorTests(unittest.TestCase):
    def test_node_command_is_a_list_and_never_uses_shell(self):
        process = mock.Mock()
        process.stdout = io.BytesIO(json.dumps(SUCCESS).encode())
        process.stderr = io.BytesIO(b"")
        process.wait.return_value = 0
        process.poll.return_value = 0

        with mock.patch.object(clip.subprocess, "Popen", return_value=process) as popen:
            result = clip.run_extractor(
                Path("/plugins/web-to-obsidian/plugin"),
                "https://example.com/article",
                no_browser=True,
            )

        args, kwargs = popen.call_args
        self.assertIsInstance(args[0], list)
        self.assertEqual(
            args[0],
            [
                "node",
                "/plugins/web-to-obsidian/extractor/src/cli.mjs",
                "https://example.com/article",
                "--no-browser",
            ],
        )
        self.assertIs(kwargs["shell"], False)
        self.assertLessEqual(kwargs.get("timeout", clip.EXTRACTOR_TIMEOUT), 120)
        self.assertEqual(result["title"], "An Article")

    def test_malformed_or_oversized_extractor_json_is_rejected_without_stderr(self):
        malformed = clip.ProcessResult(0, b"not json", b"SECRET traceback")
        with mock.patch.object(clip, "_run_bounded", return_value=malformed):
            with self.assertRaises(clip.ClipError) as caught:
                clip.run_extractor(Path("/plugin"), "https://example.com")
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertNotIn("traceback", str(caught.exception).lower())

    def test_extractor_success_payload_requires_all_typed_fields(self):
        bad = dict(SUCCESS, wordCount="three")
        result = clip.ProcessResult(0, json.dumps(bad).encode(), b"")
        with mock.patch.object(clip, "_run_bounded", return_value=result):
            with self.assertRaises(clip.ClipError):
                clip.run_extractor(Path("/plugin"), "https://example.com")

    def test_extractor_success_payload_requires_keywords_to_be_a_string_list(self):
        bad = dict(SUCCESS, keywords=["ok", 1])
        result = clip.ProcessResult(0, json.dumps(bad).encode(), b"")
        with mock.patch.object(clip, "_run_bounded", return_value=result):
            with self.assertRaises(clip.ClipError):
                clip.run_extractor(Path("/plugin"), "https://example.com")


class GitSafetyTests(unittest.TestCase):
    def _git(self, root, *args):
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_refuses_dirty_worktree_before_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._git(repo, "init", "-b", "feature/clip")
            (repo / "dirty.txt").write_text("dirty", encoding="utf-8")
            with self.assertRaises(clip.ClipError) as caught:
                clip.GitSync.preflight(repo)
            self.assertIn("clean", str(caught.exception).lower())

    def test_allows_master_when_configured_branch_is_master(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote.git"
            repo = root / "vault"
            self._git(root, "init", "--bare", str(remote))
            self._git(root, "init", "-b", "master", str(repo))
            self._git(repo, "config", "user.name", "Clip Test")
            self._git(repo, "config", "user.email", "clip@example.invalid")
            self._git(repo, "remote", "add", "origin", str(remote))
            (repo / ".gitkeep").write_text("", encoding="utf-8")
            self._git(repo, "add", ".gitkeep")
            self._git(repo, "commit", "-m", "initial")
            self._git(repo, "push", "-u", "origin", "master")

            sync = clip.GitSync.preflight(repo, expected_branch="master")
            self.assertEqual(sync.branch, "master")

    def test_rejects_when_vault_is_not_on_configured_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote.git"
            repo = root / "vault"
            self._git(root, "init", "--bare", str(remote))
            self._git(root, "init", "-b", "feature/clip", str(repo))
            self._git(repo, "config", "user.name", "Clip Test")
            self._git(repo, "config", "user.email", "clip@example.invalid")
            self._git(repo, "remote", "add", "origin", str(remote))
            (repo / ".gitkeep").write_text("", encoding="utf-8")
            self._git(repo, "add", ".gitkeep")
            self._git(repo, "commit", "-m", "initial")
            self._git(repo, "push", "-u", "origin", "feature/clip")

            with self.assertRaisesRegex(clip.ClipError, "configured clip sync branch"):
                clip.GitSync.preflight(repo, expected_branch="master")

    def test_refuses_cherry_pick_and_revert_states(self):
        for state_name in ("CHERRY_PICK_HEAD", "REVERT_HEAD"):
            with self.subTest(state=state_name), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                self._git(repo, "init", "-b", "feature/clip")
                self._git(repo, "config", "user.name", "Clip Test")
                self._git(repo, "config", "user.email", "clip@example.invalid")
                (repo / ".gitkeep").write_text("", encoding="utf-8")
                self._git(repo, "add", ".gitkeep")
                self._git(repo, "commit", "-m", "initial")
                state_path = Path(
                    self._git(repo, "rev-parse", "--git-path", state_name)
                    .stdout.decode()
                    .strip()
                )
                if not state_path.is_absolute():
                    state_path = repo / state_path
                state_path.write_text(
                    self._git(repo, "rev-parse", "HEAD").stdout.decode(),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(clip.ClipError, "operation"):
                    clip.GitSync.preflight(repo)

    def test_stages_only_generated_path_commits_and_pushes_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote.git"
            repo = root / "vault"
            self._git(root, "init", "--bare", str(remote))
            self._git(root, "init", "-b", "feature/clip", str(repo))
            self._git(repo, "config", "user.name", "Clip Test")
            self._git(repo, "config", "user.email", "clip@example.invalid")
            self._git(repo, "remote", "add", "origin", str(remote))
            (repo / ".gitkeep").write_text("", encoding="utf-8")
            self._git(repo, "add", ".gitkeep")
            self._git(repo, "commit", "-m", "initial")

            sync = clip.GitSync.preflight(repo)
            dest = repo / "Unclassified"
            dest.mkdir()
            note = dest / "Article.md"
            note.write_text("body\n", encoding="utf-8")
            outcome = sync.finalize([note])

            self.assertEqual(outcome.commit_state, "committed")
            self.assertEqual(outcome.push_state, "pushed")
            changed = self._git(repo, "show", "--pretty=", "--name-only", "HEAD").stdout
            self.assertEqual(changed.decode().strip(), "Unclassified/Article.md")
            upstream = self._git(repo, "rev-parse", "--abbrev-ref", "@{upstream}").stdout
            self.assertEqual(upstream.decode().strip(), "origin/feature/clip")

    def test_post_commit_verification_blocks_hook_added_paths_from_push(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote.git"
            repo = root / "vault"
            self._git(root, "init", "--bare", str(remote))
            self._git(root, "init", "-b", "feature/clip", str(repo))
            self._git(repo, "config", "user.name", "Clip Test")
            self._git(repo, "config", "user.email", "clip@example.invalid")
            self._git(repo, "remote", "add", "origin", str(remote))
            (repo / ".gitkeep").write_text("", encoding="utf-8")
            self._git(repo, "add", ".gitkeep")
            self._git(repo, "commit", "-m", "initial")

            hook_path = repo / ".git" / "hooks" / "pre-commit"
            hook_path.write_text(
                "#!/bin/sh\n"
                "printf 'hook data\\n' > hook-added.txt\n"
                "git add -- hook-added.txt\n",
                encoding="utf-8",
            )
            hook_path.chmod(0o700)

            sync = clip.GitSync.preflight(repo)
            destination = repo / "Unclassified"
            destination.mkdir()
            note = destination / "Article.md"
            note.write_text("body\n", encoding="utf-8")
            outcome = sync.finalize([note])

            self.assertEqual(outcome.commit_state, "committed_unverified")
            self.assertEqual(outcome.push_state, "not_attempted")
            remote_branch = subprocess.run(
                [
                    "git",
                    f"--git-dir={remote}",
                    "show-ref",
                    "--verify",
                    "refs/heads/feature/clip",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(remote_branch.returncode, 0)

    def test_finalize_allows_image_only_changes_within_generated_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote.git"
            repo = root / "vault"
            self._git(root, "init", "--bare", str(remote))
            self._git(root, "init", "-b", "master", str(repo))
            self._git(repo, "config", "user.name", "Clip Test")
            self._git(repo, "config", "user.email", "clip@example.invalid")
            self._git(repo, "remote", "add", "origin", str(remote))
            (repo / "Inbox").mkdir()
            (repo / "images" / "2026-07-25-an-article").mkdir(parents=True)
            note = repo / "Inbox" / "2026-07-25-An Article.md"
            image = repo / "images" / "2026-07-25-an-article" / "01-image.png"
            note.write_text("same note\n", encoding="utf-8")
            image.write_bytes(b"old")
            self._git(
                repo,
                "add",
                "Inbox/2026-07-25-An Article.md",
                "images/2026-07-25-an-article/01-image.png",
            )
            self._git(repo, "commit", "-m", "initial")
            self._git(repo, "push", "-u", "origin", "master")

            sync = clip.GitSync.preflight(repo, expected_branch="master")
            image.write_bytes(b"new-bytes")
            outcome = sync.finalize([note, image])

            self.assertEqual(outcome.commit_state, "committed")
            self.assertEqual(outcome.push_state, "pushed")
            changed = self._git(repo, "show", "--pretty=", "--name-only", "HEAD").stdout
            self.assertEqual(changed.decode().strip(), "images/2026-07-25-an-article/01-image.png")

    def test_finalize_uses_custom_commit_message_when_provided(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote.git"
            repo = root / "vault"
            self._git(root, "init", "--bare", str(remote))
            self._git(root, "init", "-b", "master", str(repo))
            self._git(repo, "config", "user.name", "Clip Test")
            self._git(repo, "config", "user.email", "clip@example.invalid")
            self._git(repo, "remote", "add", "origin", str(remote))
            (repo / ".gitkeep").write_text("", encoding="utf-8")
            self._git(repo, "add", ".gitkeep")
            self._git(repo, "commit", "-m", "initial")
            self._git(repo, "push", "-u", "origin", "master")

            sync = clip.GitSync.preflight(repo, expected_branch="master")
            dest = repo / "Inbox"
            dest.mkdir()
            note = dest / "My Article.md"
            note.write_text("body\n", encoding="utf-8")
            outcome = sync.finalize([note], commit_message="clip: My Article")

            self.assertEqual(outcome.commit_state, "committed")
            self.assertEqual(outcome.push_state, "pushed")
            log_msg = self._git(repo, "log", "-1", "--pretty=%s").stdout.decode().strip()
            self.assertEqual(log_msg, "clip: My Article")

    def test_finalize_falls_back_to_default_commit_message_when_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote.git"
            repo = root / "vault"
            self._git(root, "init", "--bare", str(remote))
            self._git(root, "init", "-b", "master", str(repo))
            self._git(repo, "config", "user.name", "Clip Test")
            self._git(repo, "config", "user.email", "clip@example.invalid")
            self._git(repo, "remote", "add", "origin", str(remote))
            (repo / ".gitkeep").write_text("", encoding="utf-8")
            self._git(repo, "add", ".gitkeep")
            self._git(repo, "commit", "-m", "initial")
            self._git(repo, "push", "-u", "origin", "master")

            sync = clip.GitSync.preflight(repo, expected_branch="master")
            dest = repo / "Inbox"
            dest.mkdir()
            note = dest / "Article.md"
            note.write_text("body\n", encoding="utf-8")
            outcome = sync.finalize([note])

            self.assertEqual(outcome.commit_state, "committed")
            log_msg = self._git(repo, "log", "-1", "--pretty=%s").stdout.decode().strip()
            self.assertEqual(log_msg, "clip: save web article")

    def test_finalize_truncates_long_commit_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote.git"
            repo = root / "vault"
            self._git(root, "init", "--bare", str(remote))
            self._git(root, "init", "-b", "master", str(repo))
            self._git(repo, "config", "user.name", "Clip Test")
            self._git(repo, "config", "user.email", "clip@example.invalid")
            self._git(repo, "remote", "add", "origin", str(remote))
            (repo / ".gitkeep").write_text("", encoding="utf-8")
            self._git(repo, "add", ".gitkeep")
            self._git(repo, "commit", "-m", "initial")
            self._git(repo, "push", "-u", "origin", "master")

            sync = clip.GitSync.preflight(repo, expected_branch="master")
            dest = repo / "Inbox"
            dest.mkdir()
            note = dest / "Article.md"
            note.write_text("body\n", encoding="utf-8")
            long_title = "A" * 100
            outcome = sync.finalize([note], commit_message=f"clip: {long_title}")

            self.assertEqual(outcome.commit_state, "committed")
            log_msg = self._git(repo, "log", "-1", "--pretty=%s").stdout.decode().strip()
            self.assertTrue(log_msg.startswith("clip: A"))


class RemoteAssetDownloadTests(unittest.TestCase):
    def test_download_remote_image_rejects_blocked_addresses_before_request(self):
        def blocked_resolver(host, port, *, type=0, proto=0):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port)),
            ]

        request_impl = mock.Mock()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(clip.ClipError, "blocked by network policy"):
                clip.download_remote_image(
                    "http://example.com/image.png",
                    Path(tmp),
                    1,
                    resolver=blocked_resolver,
                    request_impl=request_impl,
                )
        request_impl.assert_not_called()

    def test_download_remote_image_revalidates_redirect_destinations(self):
        def resolver(host, port, *, type=0, proto=0):
            if host == "public.example":
                return [
                    (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port)),
                ]
            if host == "blocked.example":
                return [
                    (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port)),
                ]
            raise AssertionError(f"unexpected host: {host}")

        def request_impl(approved, *, timeout, max_bytes):
            self.assertEqual(approved.hostname, "public.example")
            return clip._PinnedRemoteResponse(
                status_code=302,
                headers={"Location": "http://blocked.example/image.png"},
                body=b"",
            )

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(clip.ClipError, "blocked by network policy"):
                clip.download_remote_image(
                    "http://public.example/image.png",
                    Path(tmp),
                    1,
                    resolver=resolver,
                    request_impl=request_impl,
                )


class WeChatExtractionTests(unittest.TestCase):
    def test_is_wechat_url(self):
        self.assertTrue(clip._is_wechat_url("https://mp.weixin.qq.com/s/abc123"))
        self.assertTrue(clip._is_wechat_url("https://weixin.qq.com/s/abc123"))
        self.assertFalse(clip._is_wechat_url("https://example.com/article"))
        self.assertFalse(clip._is_wechat_url("https://mp.notweixin.qq.com/s/x"))

    def test_parse_wechat_html_extracts_metadata(self):
        html = (
            '<html><script>'
            'var msg_title = "Test Article Title";'
            'var nickname = "Test Author";'
            'var ct = "1700000000";'
            '</script>'
            '<div id="js_content"><p>Hello world</p></div>'
            '<script>var other = 1;</script></html>'
        )
        result = clip._parse_wechat_html(html, "https://mp.weixin.qq.com/s/test")
        self.assertEqual(result["title"], "Test Article Title")
        self.assertEqual(result["author"], "Test Author")
        self.assertEqual(result["published"], "2023-11-14")
        self.assertEqual(result["site"], "mp.weixin.qq.com")
        self.assertEqual(result["method"], "wechat-curl")
        self.assertIn("Hello world", result["markdown"])

    def test_parse_wechat_html_rejects_missing_title(self):
        html = '<div id="js_content"><p>Body</p></div>'
        with self.assertRaises(clip.ClipError):
            clip._parse_wechat_html(html, "https://mp.weixin.qq.com/s/x")

    def test_parse_wechat_html_rejects_missing_body(self):
        html = '<script>var msg_title = "Title";</script>'
        with self.assertRaises(clip.ClipError):
            clip._parse_wechat_html(html, "https://mp.weixin.qq.com/s/x")

    def test_wechat_html_to_markdown_images(self):
        html = '<p><img data-src="https://example.com/img.png" alt="pic"/></p>'
        md = clip._wechat_html_to_markdown(html)
        self.assertIn("![pic](https://example.com/img.png)", md)

    def test_wechat_html_to_markdown_headings_and_formatting(self):
        html = '<h2>Title</h2><p><strong>Bold</strong> and <em>italic</em></p>'
        md = clip._wechat_html_to_markdown(html)
        self.assertIn("## Title", md)
        self.assertIn("**Bold**", md)
        self.assertIn("*italic*", md)

    def test_wechat_html_to_markdown_strips_tags_and_entities(self):
        html = "<p>Hello &amp; world</p><div>inner</div>"
        md = clip._wechat_html_to_markdown(html)
        self.assertIn("Hello & world", md)
        self.assertNotIn("<p>", md)
        self.assertNotIn("<div>", md)

    def test_count_words_cjk(self):
        self.assertEqual(clip._count_words("你好世界"), 4)
        self.assertEqual(clip._count_words("Hello World"), 2)
        self.assertEqual(clip._count_words(""), 0)

    def test_run_extractor_with_fallback_falls_back_for_wechat(self):
        """When Node.js fails and URL is WeChat, curl fallback is used."""
        mock_html = (
            '<html><script>'
            'var msg_title = "Fallback Article";'
            'var nickname = "Author";'
            'var ct = "1700000000";'
            '</script>'
            '<div id="js_content"><p>Fallback content</p></div>'
            '</html>'
        )
        with mock.patch.object(clip, "run_extractor", side_effect=clip.ClipError("BROWSER_FAILED")):
            with mock.patch.object(clip, "_fetch_wechat_html", return_value=mock_html):
                result = clip.run_extractor_with_fallback(
                    Path("/plugin"), "https://mp.weixin.qq.com/s/test"
                )
        self.assertEqual(result["title"], "Fallback Article")
        self.assertEqual(result["method"], "wechat-curl")
        self.assertIn("Fallback content", result["markdown"])

    def test_run_extractor_with_fallback_raises_for_non_wechat(self):
        """When Node.js fails and URL is not WeChat, the error propagates."""
        with mock.patch.object(clip, "run_extractor", side_effect=clip.ClipError("BROWSER_FAILED")):
            with self.assertRaises(clip.ClipError):
                clip.run_extractor_with_fallback(
                    Path("/plugin"), "https://example.com/article"
                )

    def test_run_extractor_with_fallback_no_fallback_on_success(self):
        """When Node.js succeeds, no fallback is triggered."""
        fake_result = {
            "ok": True, "title": "T", "author": "", "published": "",
            "description": "", "site": "x", "canonicalUrl": "https://x",
            "url": "https://x", "keywords": [], "markdown": "body " * 50,
            "wordCount": 50, "method": "static",
        }
        with mock.patch.object(clip, "run_extractor", return_value=fake_result):
            with mock.patch.object(clip, "_fetch_wechat_html") as mock_fetch:
                result = clip.run_extractor_with_fallback(
                    Path("/plugin"), "https://mp.weixin.qq.com/s/test"
                )
        mock_fetch.assert_not_called()
        self.assertEqual(result["method"], "static")


if __name__ == "__main__":
    unittest.main()
