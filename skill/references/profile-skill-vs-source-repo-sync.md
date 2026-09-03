# Profile skill vs source repo sync

Use this note when a web-clip-related skill/plugin change needs to be committed back to the `url-to-obsidian` source repository.

## Why this matters

The `web-clip-to-obsidian` skill documentation may describe the intended deployment as symlinks, but the live profile path is not guaranteed to still be a symlink or even the same file as the source repo copy.

A profile-local edit such as:

- `~/.hermes/profiles/<profile>/skills/productivity/web-clip-to-obsidian/SKILL.md`

may **not** modify:

- `~/.hermes/workspace/repository/url-to-obsidian/skill/SKILL.md`

If you skip this check, you can incorrectly assume the repo already contains the change and open a PR with no real source-repo diff.

## Verification steps

Before committing a skill/plugin fix back to GitHub:

1. Compare the live profile path and the source repo path.
2. Check whether the profile path is a symlink.
3. Resolve both real paths and compare them.
4. Diff the files before copying any change into the repo.

Typical checks:

```bash
python3 - <<'PY'
from pathlib import Path
profile = Path('~/.hermes/profiles/<profile>/skills/productivity/web-clip-to-obsidian/SKILL.md').expanduser()
source = Path('~/.hermes/workspace/repository/url-to-obsidian/skill/SKILL.md').expanduser()
print('profile_exists=', profile.exists())
print('profile_is_symlink=', profile.is_symlink())
print('profile_real=', profile.resolve())
print('source_real=', source.resolve())
print('same_file=', profile.resolve() == source.resolve())
PY

diff -u ~/.hermes/workspace/repository/url-to-obsidian/skill/SKILL.md \
        ~/.hermes/profiles/<profile>/skills/productivity/web-clip-to-obsidian/SKILL.md || true
```

## Safe workflow

1. Verify whether the profile skill and source repo skill are actually the same file.
2. If they differ, treat the profile edit as a **local hotfix**, not as a repo change.
3. Copy only the intended change into the source repo file.
4. Create a fresh feature branch from the latest `origin/master` in the source repo.
5. Commit only the source repo diff.
6. Open the PR from that source repo branch.

## Scope control

When the profile copy has extra drift unrelated to the current task, do **not** blindly overwrite the source repo with the entire profile file. Port only the specific fix you validated in the session.

## Session lesson captured here

In this session, a docs fix was first applied to the profile-local skill file, but the actual GitHub PR needed to come from `url-to-obsidian/skill/SKILL.md` in the source repo. Verifying real paths before branch/commit/PR creation avoided opening a misleading no-op PR.
