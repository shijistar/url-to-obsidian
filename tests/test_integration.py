from pathlib import Path
import subprocess
import tempfile
from typing import cast
import unittest
from unittest import mock

import yaml

import web_to_obsidian as clip


ARTICLE = {
    "ok": True,
    "title": "An Article",
    "author": "Ada",
    "published": "2026-07-23",
    "description": "A useful page",
    "site": "Example",
    "canonicalUrl": "https://example.com/article",
    "url": "https://example.com/article",
    "keywords": ["security", "clipping"],
    "markdown": (
        "# An Article\n\n"
        + "Useful content for an integration test. " * 20
        + "\n\n![remote](https://cdn.example/image.png)\n"
    ),
    "wordCount": 140,
    "method": "static",
}

ARTICLE_HTML_IMAGE = {
    **ARTICLE,
    "markdown": (
        "# An Article\n\n"
        + "Useful content for an integration test. " * 20
        + '\n\n<img src="https://cdn.example/hero.png" alt="Hero image">\n'
    ),
}


class ClipServiceIntegrationTests(unittest.TestCase):
    def _git(self, root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def _env(
        self,
        vault: Path,
        lock_file: Path,
        pending_root: Path,
        sync_branch: str = "master",
    ) -> dict[str, str]:
        return {
            "WEB_TO_OBSIDIAN_VAULT": str(vault),
            "WEB_TO_OBSIDIAN_DEST": "Inbox",
            "WEB_TO_OBSIDIAN_IMAGES": "images",
            "WEB_TO_OBSIDIAN_SYNC_BRANCH": sync_branch,
            "WEB_TO_OBSIDIAN_LOCK_FILE": str(lock_file),
            "WEB_TO_OBSIDIAN_PENDING_ROOT": str(pending_root),
        }

    def test_service_writes_pushes_and_repeats_as_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote.git"
            vault = root / "vault"
            lock_file = root / "vault.lock"

            self._git(root, "init", "--bare", str(remote))
            self._git(root, "init", "-b", "master", str(vault))
            self._git(vault, "config", "user.name", "Clip Test")
            self._git(vault, "config", "user.email", "clip@example.invalid")
            self._git(vault, "remote", "add", "origin", str(remote))
            (vault / "Inbox").mkdir()
            (vault / "Inbox" / ".gitkeep").write_text("", encoding="utf-8")
            self._git(vault, "add", "Inbox/.gitkeep")
            self._git(vault, "commit", "-m", "initial")
            self._git(vault, "push", "-u", "origin", "master")

            env = {
                "WEB_TO_OBSIDIAN_VAULT": str(vault),
                "WEB_TO_OBSIDIAN_DEST": "Inbox",
                "WEB_TO_OBSIDIAN_IMAGES": "images",
                "WEB_TO_OBSIDIAN_SYNC_BRANCH": "master",
                "WEB_TO_OBSIDIAN_LOCK_FILE": str(lock_file),
            }
            service = clip.ClipService(Path(__file__).parents[1], env=env)

            with mock.patch.object(clip, "run_extractor", return_value=dict(ARTICLE)):
                first = service.run("https://example.com/article --save-images no")

            self.assertEqual(first.commit_state, "committed")
            self.assertEqual(first.push_state, "pushed")
            self.assertRegex(first.path, r"^Inbox/\d{4}-\d{2}-\d{2}-An Article\.md$")
            note = vault / first.path
            before_mtime = note.stat().st_mtime_ns
            before_head = self._git(vault, "rev-parse", "HEAD").stdout
            content = note.read_text(encoding="utf-8")
            frontmatter, _ = content[4:].split("\n---\n", 1)
            metadata = yaml.safe_load(frontmatter)
            self.assertEqual(metadata["url"], ARTICLE["canonicalUrl"])
            self.assertEqual(metadata["original_url"], ARTICLE["canonicalUrl"])
            self.assertEqual(metadata["original_host"], "example.com")
            self.assertEqual(metadata["keywords"], ARTICLE["keywords"])
            self.assertEqual(metadata["category"], "Inbox")
            self.assertEqual(metadata["extraction_method"], "static")
            self.assertIn("<!-- webclip:managed:start -->\n# An Article\n\n", content)
            self.assertIn("![remote](https://cdn.example/image.png)", content)

            remote_content = subprocess.run(
                [
                    "git",
                    f"--git-dir={remote}",
                    "show",
                    f"refs/heads/master:{first.path}",
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ).stdout.decode("utf-8")
            self.assertEqual(remote_content, content)

            with mock.patch.object(clip, "run_extractor", return_value=dict(ARTICLE)):
                second = service.run("https://example.com/article --save-images no")

            self.assertEqual(second.path, first.path)
            self.assertEqual(second.commit_state, "unchanged")
            self.assertEqual(second.push_state, "not_needed")
            self.assertEqual(note.stat().st_mtime_ns, before_mtime)
            self.assertEqual(self._git(vault, "rev-parse", "HEAD").stdout, before_head)
            self.assertEqual(
                self._git(vault, "status", "--porcelain=v1").stdout,
                b"",
            )

    def test_service_asks_before_writing_when_remote_images_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            lock_file = root / "vault.lock"
            pending_root = root / "pending"
            (vault / "Inbox").mkdir(parents=True)

            service = clip.ClipService(
                Path(__file__).parents[1],
                env=self._env(vault, lock_file, pending_root),
            )

            with mock.patch.object(clip, "run_extractor", return_value=dict(ARTICLE)):
                pending = cast(
                    clip.PendingClipResult,
                    service.run("https://example.com/article --no-git"),
                )

            self.assertIn("yes or no", pending.user_message().lower())
            self.assertEqual(list((vault / "Inbox").glob("*.md")), [])
            self.assertTrue((pending_root / "active.json").is_file())

    def test_service_does_not_ask_when_markdown_contains_remote_html_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            lock_file = root / "vault.lock"
            pending_root = root / "pending"
            (vault / "Inbox").mkdir(parents=True)

            service = clip.ClipService(
                Path(__file__).parents[1],
                env=self._env(vault, lock_file, pending_root),
            )

            with mock.patch.object(
                clip, "run_extractor", return_value=dict(ARTICLE_HTML_IMAGE)
            ):
                result = cast(
                    clip.ClipResult,
                    service.run("https://example.com/article --no-git"),
                )

            note = vault / result.path
            content = note.read_text(encoding="utf-8")
            self.assertTrue(note.is_file())
            self.assertIn('&lt;img src="https://cdn.example/hero.png" alt="Hero image">', content)
            self.assertFalse((pending_root / "active.json").exists())

    def test_resume_pending_no_writes_note_with_remote_images_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            lock_file = root / "vault.lock"
            pending_root = root / "pending"
            (vault / "Inbox").mkdir(parents=True)

            service = clip.ClipService(
                Path(__file__).parents[1],
                env=self._env(vault, lock_file, pending_root),
            )

            with mock.patch.object(clip, "run_extractor", return_value=dict(ARTICLE)):
                service.run("https://example.com/article --no-git")

            result = service.resume_pending("no")
            note = vault / result.path
            content = note.read_text(encoding="utf-8")

            self.assertEqual(result.commit_state, "disabled")
            self.assertTrue(note.is_file())
            self.assertIn("![remote](https://cdn.example/image.png)", content)
            self.assertFalse((pending_root / "active.json").exists())

    def test_resume_pending_yes_localizes_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            lock_file = root / "vault.lock"
            pending_root = root / "pending"
            (vault / "Inbox").mkdir(parents=True)

            service = clip.ClipService(
                Path(__file__).parents[1],
                env=self._env(vault, lock_file, pending_root),
            )

            with mock.patch.object(clip, "run_extractor", return_value=dict(ARTICLE)):
                service.run("https://example.com/article --no-git")

            def fake_download(url: str, destination_dir: Path, index: int) -> Path:
                self.assertEqual(url, "https://cdn.example/image.png")
                destination_dir.mkdir(parents=True, exist_ok=True)
                target = destination_dir / f"{index:02d}-image.png"
                target.write_bytes(b"png")
                return target

            with mock.patch.object(clip, "download_remote_image", side_effect=fake_download):
                result = service.resume_pending("yes")

            note = vault / result.path
            content = note.read_text(encoding="utf-8")
            image_dir = clip._slugify_image_dir(note, ARTICLE["canonicalUrl"])
            self.assertIn(f"![remote](../images/{image_dir}/01-image.png)", content)
            self.assertTrue((vault / "images" / image_dir / "01-image.png").is_file())
            self.assertFalse((pending_root / "active.json").exists())

    def test_resume_pending_rejects_different_vault_or_clip_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault_a = root / "vault-a"
            vault_b = root / "vault-b"
            lock_a = root / "vault-a.lock"
            lock_b = root / "vault-b.lock"
            pending_root = root / "pending"
            (vault_a / "Inbox").mkdir(parents=True)
            (vault_b / "Inbox").mkdir(parents=True)

            service_a = clip.ClipService(
                Path(__file__).parents[1],
                env=self._env(vault_a, lock_a, pending_root),
            )
            service_b = clip.ClipService(
                Path(__file__).parents[1],
                env=self._env(vault_b, lock_b, pending_root),
            )

            with mock.patch.object(clip, "run_extractor", return_value=dict(ARTICLE)):
                service_a.run("https://example.com/article --no-git")

            with self.assertRaisesRegex(clip.ClipError, "different vault or clip configuration"):
                service_b.resume_pending("no")

            self.assertEqual(list((vault_b / "Inbox").glob("*.md")), [])
            self.assertTrue((pending_root / "active.json").is_file())

    def test_service_save_images_yes_with_git_commits_note_and_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote.git"
            vault = root / "vault"
            lock_file = root / "vault.lock"
            pending_root = root / "pending"

            self._git(root, "init", "--bare", str(remote))
            self._git(root, "init", "-b", "master", str(vault))
            self._git(vault, "config", "user.name", "Clip Test")
            self._git(vault, "config", "user.email", "clip@example.invalid")
            self._git(vault, "remote", "add", "origin", str(remote))
            (vault / "Inbox").mkdir()
            (vault / "Inbox" / ".gitkeep").write_text("", encoding="utf-8")
            self._git(vault, "add", "Inbox/.gitkeep")
            self._git(vault, "commit", "-m", "initial")
            self._git(vault, "push", "-u", "origin", "master")

            service = clip.ClipService(
                Path(__file__).parents[1],
                env=self._env(vault, lock_file, pending_root),
            )

            def fake_download(url: str, destination_dir: Path, index: int) -> Path:
                self.assertEqual(url, "https://cdn.example/image.png")
                destination_dir.mkdir(parents=True, exist_ok=True)
                target = destination_dir / f"{index:02d}-image.png"
                target.write_bytes(b"png")
                return target

            with mock.patch.object(clip, "run_extractor", return_value=dict(ARTICLE)):
                with mock.patch.object(clip, "download_remote_image", side_effect=fake_download):
                    result = cast(
                        clip.ClipResult,
                        service.run("https://example.com/article --save-images yes"),
                    )

            self.assertEqual(result.commit_state, "committed")
            self.assertEqual(result.push_state, "pushed")
            note = vault / result.path
            image_dir = clip._slugify_image_dir(note, ARTICLE["canonicalUrl"])
            image = vault / "images" / image_dir / "01-image.png"
            self.assertTrue(note.is_file())
            self.assertTrue(image.is_file())
            committed = {
                line
                for line in self._git(vault, "show", "--pretty=", "--name-only", "HEAD")
                .stdout.decode("utf-8")
                .splitlines()
                if line
            }
            self.assertIn(result.path, committed)
            self.assertIn(f"images/{image_dir}/01-image.png", committed)

    def test_service_cleans_up_localized_images_when_note_write_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            lock_file = root / "vault.lock"
            pending_root = root / "pending"
            (vault / "Inbox").mkdir(parents=True)

            service = clip.ClipService(
                Path(__file__).parents[1],
                env=self._env(vault, lock_file, pending_root),
            )

            with mock.patch.object(clip, "run_extractor", return_value=dict(ARTICLE)):
                first = cast(
                    clip.ClipResult,
                    service.run("https://example.com/article --save-images no --no-git"),
                )

            def fake_download(url: str, destination_dir: Path, index: int) -> Path:
                self.assertEqual(url, "https://cdn.example/image.png")
                destination_dir.mkdir(parents=True, exist_ok=True)
                target = destination_dir / f"{index:02d}-image.png"
                target.write_bytes(b"png")
                return target

            with mock.patch.object(clip, "run_extractor", return_value=dict(ARTICLE)):
                with mock.patch.object(clip, "download_remote_image", side_effect=fake_download):
                    with self.assertRaisesRegex(clip.ClipError, "rerun with --refresh"):
                        service.run("https://example.com/article --save-images yes --no-git")

            self.assertTrue((vault / first.path).is_file())
            first_note = vault / first.path
            image_dir = clip._slugify_image_dir(first_note, ARTICLE["canonicalUrl"])
            self.assertFalse((vault / "images" / image_dir / "01-image.png").exists())


if __name__ == "__main__":
    unittest.main()
