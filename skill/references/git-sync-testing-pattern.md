# Git Sync Testing Pattern

Pattern for unit-testing `GitSync.preflight()` and `GitSync.finalize()` in `plugin/tests/test_web_to_obsidian.py`.

## Standard Setup

Every test that exercises Git sync must create a temporary repo with a bare remote:

```python
def _git(self, root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

def test_example(self):
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
        self._git(repo, "push", "-u", "origin", "master")  # CRITICAL: sets upstream

        sync = clip.GitSync.preflight(repo, expected_branch="master")
        # ... test finalize(), etc.
```

## Key Pitfall: Upstream Required

`GitSync.preflight()` calls `git rev-parse --abbrev-ref @{upstream}` internally. Without `git push -u`, this raises `ClipError("The clip sync branch has no upstream.")`. Always push with `-u` in test setup.

## Verifying Commit Messages

```python
log_msg = self._git(repo, "log", "-1", "--pretty=%s").stdout.decode().strip()
self.assertEqual(log_msg, "clip: My Article")
```

## Verifying Staged Files

```python
changed = self._git(repo, "show", "--pretty=", "--name-only", "HEAD").stdout
self.assertEqual(changed.decode().strip(), "Inbox/Article.md")
```

## Verifying Push State

```python
upstream = self._git(repo, "rev-parse", "--abbrev-ref", "@{upstream}").stdout
self.assertEqual(upstream.decode().strip(), "origin/master")
```
