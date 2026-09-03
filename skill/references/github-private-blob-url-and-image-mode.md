# GitHub private blob URLs, skill/source-repo drift, and `image_mode` semantics

Use this note when a clipped article was saved successfully but the returned GitHub preview URL behaves inconsistently across clients, when a documentation patch must land in the `url-to-obsidian` repo itself, or when frontmatter/image behavior looks inconsistent with the article body.

## 1) Private GitHub blob URL can be client-sensitive

Observed in this workflow:
- The target repo existed and was private.
- The target file existed on `master` and GitHub Contents API confirmed the path.
- The plugin's `github_blob_url()` implementation matched GitHub Contents API `html_url` and produced the form:
  - `.../blob/master/Inbox/2026-...md`
- But the user reported that this form 404ed in their real usage path, while the alternative with the slash encoded as `%2F` worked:
  - `.../blob/master/Inbox%2F2026-...md`

### Diagnostic sequence
1. Verify the repo exists and whether it is private.
2. Verify the file exists on the target branch via git or GitHub Contents API.
3. Compare three values separately:
   - plugin-generated URL
   - GitHub Contents API `html_url`
   - user-reported actually-openable URL
4. If plugin/API agree but the user-openable URL differs, treat this as a real delivery compatibility problem rather than saying "the URL is already correct".

### Practical lesson
For user-facing preview links, prioritize the format that actually opens in the user's environment. Distinguish canonical API output from the empirically reliable delivery format.

## 2) Profile skill file vs source-repo skill file

Do not assume the loaded profile skill file is always the same file as the source-repo skill under:

- Profile skill: `~/.hermes/profiles/<profile>/skills/productivity/web-clip-to-obsidian/SKILL.md`
- Source repo skill: `~/.hermes/workspace/repository/url-to-obsidian/skill/SKILL.md`

Before creating a repository commit or PR for documentation updates, verify whether they are the same file or symlink target. If they are different files, patching the loaded profile skill alone will not create a diff in the `url-to-obsidian` repository. Sync the source-repo file explicitly first.

## 3) `image_mode` semantics

`image_mode` should describe only the remote-Markdown-image workflow:

- Omit the field when there are no remote Markdown images.
- Use `image_mode: remote` when remote Markdown images were detected and intentionally kept remote.
- Use `image_mode: local` when those images were localized into the vault.

Do not treat the field as a general "article has any image-like content" flag.

## 4) HTML `<img>` does not imply `image_mode`

The current workflow detects remote Markdown images for the yes/no confirmation path. Plain HTML image tags in article content are outside that flow and should not be used as evidence that `image_mode` must be present.

## 5) Verification pattern

When validating image-related behavior after a clip:

1. Check the extractor/result path that detected remote Markdown images.
2. Inspect the saved note body for real Markdown image references, not just code samples or HTML tags.
3. Treat frontmatter `image_mode` as a consequence of the workflow above, not as the sole source of truth for whether an article contains display images.
