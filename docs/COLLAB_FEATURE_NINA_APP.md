# Working together on `feature/nina-app`

Two-developer workflow for the canonical Nina branch. Read this once,
then keep the **Daily flow** section pinned somewhere.

## Where the source of truth lives

| Thing | Where |
| --- | --- |
| Canonical branch | `origin/feature/nina-app` on `github.com:Sirena-Technologies/Nvidia-jetson-platform` |
| Prajwal's clone | `F:\nina-ui\Nvidia-jetson-platform-nina-arm` (SSH remote) |
| Hari's clone | `F:\sirena\Nvidia\nina-app\Nvidia-jetson-platform` (HTTPS remote) |
| Pre-migration safety tag | `pre-migration-nina-arm-2026-05-14` (pushed to origin) |
| Pre-migration backup branch | `nina-arm-backup` (local + pushed to `origin/nina-arm-backup`) |

The historic `nina-arm` branch is **obsolete** — its work has been
forward-ported into `feature/nina-app` as commit `9263f52`.

## One-time per-clone setup (already done in both clones)

These are local-repo settings, not global. They ensure pulls stay
linear instead of producing merge bubbles and that repeat conflicts
get auto-resolved by `rerere`:

```bash
git config pull.rebase true        # `git pull` rebases instead of merging
git config rebase.autoStash true   # uncommitted changes get stashed/unstashed automatically
git config rerere.enabled true     # remember conflict resolutions
git config rerere.autoUpdate true  # apply remembered resolutions automatically
git config push.default current    # `git push` only pushes the current branch
```

If you ever clone the repo onto a third machine, run those five
commands inside the new clone before doing any work on it.

## Daily flow (the short version)

```bash
# 1. Always start the day with a fresh tree
git checkout feature/nina-app
git pull                  # rebases your local commits on top of origin

# 2. Hack, test, commit small. Push often.
#    Edit files...
git add <files>
git commit -m "<scope>: <short summary>"

# 3. Before pushing, sync once more (the other person may have pushed)
git pull                  # rebase-pulls; conflicts will pause for you to fix
# ...resolve conflicts if any, `git add`, `git rebase --continue`...
git push
```

That's it. No feature branches needed for small changes; cut a branch
only when the change is large enough to want review.

## What changed in the integration commit

The forward-port commit `9263f52` brought four big things from the old
`nina-arm` branch onto `feature/nina-app`:

- **Android companion app** — `android/` (Drive, Map, Vision, Find
  Robot, Settings screens with Sirena UI parity).
- **Embedded HTTP gateway** — `sirena_ui/android_gateway/` (replaces
  the old `nina/link_daemon/` process; the kiosk now hosts it).
- **`nina/jetson_net/`** — config + `host_control` for the in-app
  Shutdown / Reboot buttons (calls `sudo -n systemctl ...`).
- **Lidar S2E driver hardening** — actionable diagnostics, an explicit
  `NINA_LIDAR_MODEL=disabled` mode, A1 fallback restricted to `auto`.

Plus a single bring-up script that chains all four installers:
`scripts/bring-up-nina-jetson.sh`.

## Where each person typically works (avoid stepping on each other)

This isn't a hard rule — just a hint that minimises conflicts:

- **Prajwal** → `android/`, `sirena_ui/android_gateway/`,
  `nina/jetson_net/`, `sirena_ui/screens/`, install scripts,
  `docs/COMPANION_*.md`.
- **Hari** → `nina/controllers/hoverboard_axis_drive.py`,
  `nina/config/settings.py` (motor tuning defaults),
  `nina/sensors/`, hoverboard / lean-tuning commits.

When you both touch the same file, the rebase-pull will pause; resolve
the conflict, `git add` it, `git rebase --continue`, push. `rerere`
will remember the resolution if it ever recurs.

## When `git pull` complains

Common shape, what to do:

| Symptom | Why | Fix |
| --- | --- | --- |
| `Your local changes ... would be overwritten` | uncommitted work in the tree | already auto-stashed (`rebase.autoStash=true`); if not, `git stash`, pull, `git stash pop` |
| `CONFLICT (content): Merge conflict in <file>` | both sides changed the same lines | edit `<file>`, remove `<<<<<<< / =======/ >>>>>>>` markers, `git add <file>`, `git rebase --continue` |
| `! [rejected]` on push, `(non-fast-forward)` | the other person pushed first | `git pull` (rebases your work), then `git push` |
| `error: failed to push some refs ... (fetch first)` | same as above | `git pull && git push` |

Never use `git push --force` or `git push --force-with-lease` against
`feature/nina-app`. If you absolutely need to rewrite history (rare),
talk to the other person first — they'll need to re-clone or do a
`git reset --hard origin/feature/nina-app`.

## Branching for larger work (optional)

For anything bigger than a one-sitting change, cut a branch off
`feature/nina-app`, push it, open a PR on GitHub:

```bash
git checkout feature/nina-app
git pull
git checkout -b prajwal/companion-bonjour-discovery
# ...work...
git push -u origin prajwal/companion-bonjour-discovery
# open PR on github.com from your branch into feature/nina-app
```

Merging the PR with "Squash and merge" (GitHub's UI option) keeps
`feature/nina-app`'s history clean.

## When you change Python imports / module paths

Both clones run their own Jetson tablets. After a pull that touches
`sirena_ui/`, `nina/jetson_net/`, or `nina/controllers/`, re-run on
the Jetson:

```bash
journalctl --user -u nina-ui-kiosk -f &  # watch logs
systemctl --user restart nina-ui-kiosk    # apply the new code
```

If anything imports-fails on startup, the journal will say which line.

## Reverting if a teammate's commit breaks you

```bash
git pull                                 # get the bad commit
git log --oneline -5                     # find the SHA
git revert <bad-sha>                     # creates a "Revert ..." commit
git push                                 # publish the revert
```

Avoid `git reset --hard` on `feature/nina-app` — that just throws the
bad commit away locally; the next pull will bring it right back. A
revert commit is the right answer.

## Hard recovery: roll the whole branch back

Only if something truly catastrophic landed and a `git revert` chain
would be huge. Talk to each other first, then **one** of you runs:

```bash
git fetch origin
git checkout feature/nina-app
git reset --hard pre-migration-nina-arm-2026-05-14   # or any other good tag
git push --force-with-lease origin feature/nina-app  # only after agreement
```

The other person must then re-sync their clone:

```bash
git fetch origin
git checkout feature/nina-app
git reset --hard origin/feature/nina-app
```

## Cleaning up local safety nets (when you're confident)

After ~a week of green builds and successful Jetson deploys:

```bash
# local
git branch -D nina-arm-backup

# remote — only after both clones agree the forward-port is stable
git push origin --delete nina-arm-backup
git push origin --delete nina-arm           # the old branch tip
```

Keep the `pre-migration-*` tags forever; they're tiny and they let
anyone spelunk the original 24-commit history of the Android work.

## Quick reference

```bash
# Where am I?
git status -sb

# What did the other person just push?
git fetch && git log --oneline ..origin/feature/nina-app

# I'm partway through a rebase and want out
git rebase --abort

# I made a typo in my last commit message (only if NOT pushed yet)
git commit --amend

# Show my last commit's full message + diff
git show HEAD

# Who last touched this file and why?
git log -p --follow <file>
```
