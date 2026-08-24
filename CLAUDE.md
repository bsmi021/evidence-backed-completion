# Repository instructions for Claude Code

This repository is the source for the `evidence-backed-completion` Claude Code
plugin, published as a single-plugin marketplace
(`.claude-plugin/marketplace.json`).

## Release process

Every push that changes plugin behavior, skill content, reference docs, or
bundled scripts ships as a new version. Follow these steps in order:

1. Bump `version` in `.claude-plugin/plugin.json` **and** the matching
   `plugins[0].version` in `.claude-plugin/marketplace.json` together — they
   must always be equal. `claude plugin tag` fails validation if they
   disagree.
2. Add a dated entry to `CHANGELOG.md` (Keep a Changelog format) describing
   what changed.
3. Run `claude plugin validate . --strict` and confirm it passes before
   committing.
4. Commit the version bump, changelog entry, and the actual change together.
5. Run `claude plugin tag . --push -m "Release v%s"` to create and push the
   `evidence-backed-completion--v<version>` git tag from that commit.
6. Push commits: `git push`.
7. Cut a GitHub release from that tag (`gh release create <tag> --notes-file
   <notes>`) summarizing the new `CHANGELOG.md` entry.

Do not push a version bump without a matching `CHANGELOG.md` entry, and do not
tag or release a version where `plugin.json` and `marketplace.json` disagree.
The marketplace file's top-level `metadata.version` tracks the marketplace
manifest format itself, not the plugin release — leave it alone unless the
marketplace schema usage actually changes.
