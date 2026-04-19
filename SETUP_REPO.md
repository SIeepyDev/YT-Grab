# One-click repo setup

This folder is staged and ready. Two options, ranked by effort:

## Option A — Double-click `init_repo.ps1` (recommended)

Right-click → **Run with PowerShell**. That's it.

The script:

1. Cleans up the stale partial `.git` from staging
2. Runs `git init`, adds everything, commits with a v1.0 release message
3. If you have [gh CLI](https://cli.github.com) installed and authed:
   creates the private GitHub repo and pushes automatically — opens
   it in your browser when done.
4. If gh isn't installed: prints the two commands to finish manually.

## Option B — Manual, if you prefer

```powershell
cd <path-to>\yt-grab
# Clean the staged partial .git -- sandbox left it in a weird state
Remove-Item -Recurse -Force .git
git init --initial-branch=main
git add .
git commit -m "Initial commit: YT Grab v1.0 (standalone beta)"

# Then either:
gh repo create yt-grab --private --source=. --remote=origin --push
# OR manually via https://github.com/new, then:
git remote add origin https://github.com/SIeepyDev/yt-grab.git
git push -u origin main
```

## After pushing

Delete this `SETUP_REPO.md` — it's just a first-time setup note. The
`README.md` is what people land on when they visit the repo.

## Going public later

When the beta's ready for your portfolio:

```powershell
gh repo edit SIeepyDev/yt-grab --visibility public `
  --accept-visibility-change-consequences
```

Or: GitHub UI → Settings → General → Danger Zone → Change visibility.
