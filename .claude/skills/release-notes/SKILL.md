---
name: release-notes
description: Draft the next [X.Y.Z] CHANGELOG section from git log since the previous tag
---

# release-notes

Drafts the next CHANGELOG section for `bedrock-strands-agent` from commits
since the previous tag. The release workflow (`.github/workflows/release.yml`)
is tag-triggered and auto-generates GitHub Release notes; this skill keeps
`CHANGELOG.md` clean and human-curated.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) — buckets
**Added**, **Changed**, **Fixed**, **Removed**, **Deprecated**, **Security**.

## Workflow

### 1. Determine the previous tag

```bash
git describe --tags --abbrev=0 2>/dev/null || echo "NO_TAGS"
```

If `NO_TAGS`, the next version is **v0.1.0** (first release). Read commits
from the initial commit instead:

```bash
git log --pretty=format:'%h %s' --no-merges
```

### 2. Gather commits since previous tag

```bash
git log "v${PREV}..HEAD" --pretty=format:'%h %s' --no-merges
```

### 3. Group by Conventional Commits prefix

Map each commit to a Keep-a-Changelog bucket:

| Prefix                              | Bucket                                  |
| ----------------------------------- | --------------------------------------- |
| `feat`, `feat(*)`                   | **Added**                               |
| `fix`, `fix(*)`                     | **Fixed**                               |
| `fix(security)`, `security`         | **Security**                            |
| `chore(deps): bump *`               | **Changed** — fold into one bullet      |
| `chore`, `chore(*)`, `refactor`, `refactor(*)`, `perf` | **Changed**          |
| `docs`, `style`, `test`, `ci`, `build` | omitted by default                   |
| anything else                       | ask the user to classify                |

**Dependency folding**: collapse all `chore(deps): bump *` commits (Dependabot
groups: `strands`, `otel`, `aws`, `github-actions`, `docker`) into a single
bullet under **Changed**:

> Updated dependencies (strands, otel, aws, github-actions, docker as applicable).

The user can opt to include `docs`/`ci`/`build`/`test`/`style` if they're
user-visible.

### 4. Propose the version bump

Inspect the gathered commits:

- any `feat!:` / `fix!:` / `BREAKING CHANGE` footer → **major** bump
- any `feat:` / `feat(*)` → **minor** bump
- only `fix:` / `chore:` / `refactor:` / `perf:` → **patch** bump

Propose `vX.Y.Z` and **ask the user to confirm** before writing the file.

### 5. Apply to `CHANGELOG.md`

- Rename the existing `## [Unreleased]` heading to `## [X.Y.Z] - YYYY-MM-DD`
  (today's date, ISO 8601).
- Insert a fresh `## [Unreleased]` block above it with empty `### Added` and
  `### Changed` placeholders.
- Update the link references at the bottom:
  - Replace `[Unreleased]: ...compare/HEAD` (or `...compare/vPREV...HEAD`)
    with `[X.Y.Z]: ...compare/vPREV...vX.Y.Z`.
  - Add a new `[Unreleased]: ...compare/vX.Y.Z...HEAD` line above it.

For the very first release (no prior tag), use the initial commit SHA instead
of `vPREV` in the compare URL, or simply `[0.1.0]: .../releases/tag/v0.1.0`.

### 6. Show the diff and stop

Print the proposed `CHANGELOG.md` diff. **Do not commit, push, or stage.** The
user (or parent agent) integrates the change.

### 7. Tag step (only after the user has committed the CHANGELOG)

Once the CHANGELOG commit is on the release branch:

```bash
git tag -a "vX.Y.Z" -m "Release X.Y.Z"
git push origin "vX.Y.Z"
```

The tag-triggered workflow at `.github/workflows/release.yml` takes over:
it builds the GHCR image with SBOM + provenance and publishes a GitHub
Release with auto-generated notes via `softprops/action-gh-release@v2`.

## Notes

- Subject lines should be lightly rewritten for the changelog: drop the
  `feat:` / `fix:` prefix, capitalize the first word, end with a period, and
  prefer user-visible phrasing over implementation detail.
- If a commit's intent is ambiguous from its subject, run
  `git show --stat <sha>` and ask the user before classifying.
- Keep bullets terse — one line each. Multi-paragraph notes belong in the
  PR description, not the CHANGELOG.
