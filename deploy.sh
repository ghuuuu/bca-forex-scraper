#!/bin/sh
# Pull the latest code and rebuild the containers, in one step.
#
#     sudo ./deploy.sh
#
# Safe to re-run, and safe to interrupt. The database lives in ./data on the
# host and is never touched by a rebuild — only the image is replaced.
#
# Works whether or not this is a git checkout: without one (the copy-over-SMB
# workflow) it skips the pull and rebuilds whatever is on disk, which is still
# the step people forget.

set -eu

cd "$(dirname "$0")"

say()  { printf '\n=== %s\n' "$1"; }
warn() { printf '    ! %s\n' "$1"; }
die()  { printf '\nERROR: %s\n' "$1" >&2; exit 1; }

# ---------------------------------------------------------------- preflight --

[ "$(id -u)" -eq 0 ] || die "run this with sudo — Docker on DSM needs root"

# DSM 7.2 ships Compose v2 as a docker subcommand; older packages have the
# standalone v1 binary. Both are still out there on a DS916+.
if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    die "neither 'docker compose' nor 'docker-compose' found — is the Docker/Container Manager package installed and running?"
fi

# Docker on Synology will not create a missing bind-mount source; it refuses to
# start the container instead. Cheap to guarantee, annoying to diagnose.
mkdir -p data web

# ------------------------------------------------------------------- update --

if [ -d .git ] && command -v git >/dev/null 2>&1; then
    say "Pulling from GitHub"

    # Synology shared folders carry ACLs that report every file as 0777. Git
    # reads that as the executable bit being set on every tracked file, marks
    # the entire tree modified, and then refuses to pull:
    #
    #     error: Your local changes to the following files would be overwritten
    #
    # The diff is mode-only — every file shows 0 insertions and 0 deletions —
    # so there is nothing to preserve and nothing to stash. Telling git not to
    # read permission bits here is the fix, not `reset --hard`, which would
    # keep looking like it worked while the tree dirtied itself again.
    git config core.fileMode false

    before=$(git rev-parse HEAD)
    # --ff-only so a surprise local edit stops the deploy loudly rather than
    # being silently merged on a box nobody is watching
    if git pull --ff-only; then
        after=$(git rev-parse HEAD)
        if [ "$before" = "$after" ]; then
            printf '    already up to date (%s)\n' "$(git rev-parse --short HEAD)"
        else
            printf '    %s -> %s\n' "$(git rev-parse --short "$before")" "$(git rev-parse --short "$after")"
            git --no-pager log --oneline "$before..$after" | sed 's/^/      /'
        fi
    else
        die "git pull failed — resolve it by hand, nothing has been rebuilt"
    fi
elif [ -d .git ]; then
    warn "git not installed on this NAS — skipping pull, rebuilding what is on disk"
else
    warn "not a git checkout — skipping pull, rebuilding what is on disk"
fi

# ------------------------------------------------------------------ rebuild --

say "Rebuilding and restarting"
$COMPOSE up -d --build

# Old layers pile up on a NAS that has better uses for the space. Dangling only,
# so nothing still referenced by a tag is at risk.
say "Clearing dangling images"
docker image prune -f | tail -n 1

# ------------------------------------------------------------------- report --

say "Containers"
$COMPOSE ps

say "Scraper log (last 15 lines)"
docker logs --tail 15 bca-forex-scraper 2>&1 || warn "no log yet — it may still be starting"

# Read the port back out of the compose file so this line cannot go stale.
port=$(sed -n 's/^[[:space:]]*-[[:space:]]*"\([0-9]\{2,5\}\):8080".*/\1/p' docker-compose.yml | head -n 1)
printf '\n=== Done. Open http://%s:%s/  (hard-refresh: Ctrl+Shift+R)\n\n' \
    "$(hostname -i 2>/dev/null | awk '{print $1}' || echo '<nas-ip>')" "${port:-7000}"
