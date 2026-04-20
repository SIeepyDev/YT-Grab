# Contributing

YT Grab is a solo side project. That said — if you find a bug, break something, or want to improve something, you're welcome to jump in.

## Bugs

Open an issue. Include: what you did, what you expected, what actually happened, and your Windows version. A stack trace from `debug.log` helps a lot.

## Pull requests

PRs are welcome but not promised. Keep them focused — one fix or one feature per PR. Match the existing style (single-file `index.html`, vanilla JS, Flask on the backend, no build step for the frontend).

Commits follow [Conventional Commits](https://www.conventionalcommits.org) so [release-please](https://github.com/googleapis/release-please) can pick them up:

- `feat: …` for new functionality
- `fix: …` for bug fixes
- `docs:` / `chore:` / `refactor:` for everything else

## Scope

What YT Grab is: a local-first, Windows-only YouTube downloader with a clean UI. What it isn't: cross-platform, a media player, a library manager, or a cloud service. PRs that broaden the scope will probably get a polite no.
