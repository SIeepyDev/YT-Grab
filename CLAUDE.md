# YT Grab — notes for Claude

## Commit format (Conventional Commits — release-please parses these)

- `feat: <msg>` → minor bump (1.4.0 → 1.5.0), shows under **Features** in CHANGELOG.
- `fix: <msg>` → patch bump (1.4.0 → 1.4.1), shows under **Bug Fixes**.
- `feat!:` / `fix!:` or a `BREAKING CHANGE:` footer → major bump.
- `chore:` / `ci:` / `test:` / `style:` are hidden from the changelog; use for non-user-facing work.
- Keep the subject ≤50 chars, imperative mood, no trailing period. Example: `feat: add Ctrl+K command palette`.

## Ship flow
Push conventional commits to `main`. Release-please keeps a living PR open with the bumped version + changelog. Merge that PR → tag + GitHub Release publish automatically. No local ship scripts.

## Version source of truth
`.release-please-manifest.json`. `index.html` is auto-bumped via the `<!-- x-release-please-version -->` marker on the about-line.
