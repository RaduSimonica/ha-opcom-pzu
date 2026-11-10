# Contributing

## Branches and releases

| Branch | What it is | Release it publishes |
|---|---|---|
| `development` | day-to-day work | none |
| `staging` | things that are ready to try | a **snapshot**, a GitHub pre-release (`vX.Y.Z-snapshot.N`) |
| `main` | the stable version | a **stable release** (`vX.Y.Z`), which HACS installs by default |

Flow: work on `development`, open a pull request into `staging` when something
is ready to try, and once in a while open a pull request from `staging` into
`main` to release everything that has gathered there.

### Snapshots (pull request into `staging`)

1. Set `version` in `custom_components/opcom_pzu/manifest.json` to the next snapshot
   of the coming release: `0.5.0-snapshot.1`, then `0.5.0-snapshot.2`, and so on.
   The number after `snapshot.` goes up with every pull request; the `0.5.0` part is the
   stable version the snapshots lead to (`0.x.0` for features, `0.x.y` for fixes).
2. Add a `## [0.5.0-snapshot.2] - date` section to `CHANGELOG.md` with what this
   snapshot adds.

CI fails the pull request if the version is not a snapshot, is not higher than
every tag so far, or has no changelog section. Merging publishes a pre-release
with that section as its notes. HACS offers it only to users who turn on
*Show beta versions* for the repository (repository menu in HACS, or
*Redownload* and pick the version).

### Stable releases (pull request from `staging` into `main`)

1. Set `version` to the stable number, e.g. `0.5.0`.
2. Replace the snapshot sections in `CHANGELOG.md` with one `## [0.5.0] - date`
   section that describes the whole release (keep the same headings: Added,
   Changed, Fixed).

CI fails the pull request if the version is a snapshot, is not higher than the
latest *stable* release, or has no changelog section. Merging publishes the
release, which HACS shows as the version and offers to everybody. Afterwards,
merge `main` back into `staging` and `development` so they carry the new version
and changelog.

### Hotfixes (a fix that cannot wait for the next release)

A bug in the stable release is fixed on its own branch from `main`, so the fix
does not carry what is still being tried in `staging`.

1. `git switch -c hotfix/0.3.1 main`, fix it with a test, set `version` to the
   next stable patch (`0.3.1`) and add its `## [0.3.1] - date` section.
2. Open a pull request into `main`. Merging publishes the stable release.
3. **Keep the fix in the snapshots.** Make the same change on `development` too
   (an identical change merges without a conflict) and note it in the next
   snapshot's changelog section. After the release, merge `main` back into
   `staging` and `development`, as after any stable release. If the changelog or
   manifest conflict, keep the snapshot version and keep both changelog sections.

## Tests

```
uv venv && uv pip install pytest-homeassistant-custom-component ruff
.venv/bin/ruff check custom_components tests .github --select F,E9,I
.venv/bin/python -m pytest
```
