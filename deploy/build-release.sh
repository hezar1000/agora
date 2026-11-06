#!/bin/bash
# Unpack one commit of Agora into its own release directory, next to the one
# that is live, without touching the live one.
#
#   sudo bash deploy/build-release.sh <commit> <tarball>
#
# The tarball is `git archive` of that commit (see docs/DEPLOYING.md). The
# release lands in $AGORA_RELEASES/release-<commit> and links the shared
# virtualenv; deploy/activate-release.sh then migrates and switches to it.
set -euo pipefail

SHA="${1:?usage: build-release.sh <commit> <tarball>}"
TARBALL="${2:?usage: build-release.sh <commit> <tarball>}"

RELEASES="${AGORA_RELEASES:-/home/agora/agora-releases}"
LINK="${AGORA_CURRENT:-/srv/agora/current}"
SHARED_VENV="${AGORA_VENV:-/home/agora/venv}"
RUNAS="${AGORA_USER:-agora}"
KEEP=2  # releases kept for rollback besides the live one

NEW="$RELEASES/release-$SHA"
CURRENT=$(readlink -f "$LINK" 2>/dev/null || true)

if [ "$NEW" = "$CURRENT" ]; then
  echo "refusing: release-$SHA is the one currently deployed" >&2
  exit 1
fi
[ -f "$TARBALL" ] || { echo "refusing: no tarball at $TARBALL" >&2; exit 1; }
[ -x "$SHARED_VENV/bin/python" ] || {
  echo "refusing: no virtualenv at $SHARED_VENV (see docs/DEPLOYING.md)" >&2
  exit 1
}

# Keep the live release and the newest $KEEP others; drop the rest first, so a
# full disk does not leave a half-written release behind.
kept=0
while IFS= read -r dir; do
  [ "$dir" = "$CURRENT" ] && continue
  [ "$dir" = "$NEW" ] && continue
  if [ "$kept" -lt "$KEEP" ]; then
    kept=$((kept + 1))
    continue
  fi
  echo "  pruning $(basename "$dir")"
  rm -rf "$dir"
done < <(
  find "$RELEASES" -maxdepth 1 -type d -name 'release-*' -printf '%T@ %p\n' \
    | sort -rn | cut -d' ' -f2-
)

rm -rf "$NEW"
mkdir -p "$NEW"
tar -xf "$TARBALL" -C "$NEW"

# Root unpacked it; the service account has to be able to read it. Static
# files are served from DJANGO_STATIC_ROOT, not from here, so nginx needs
# nothing in this tree.
chown -R "$RUNAS":"$RUNAS" "$NEW"
chmod -R u=rwX,g=rX,o= "$NEW"

# One virtualenv shared by every release. A release is a few megabytes of
# code; copying a gigabyte of identical packages into each one fills the disk.
# The trade-off: rolling back to an older release does not roll packages back.
ln -sfn "$SHARED_VENV" "$NEW/.venv"

rm -f "$TARBALL"

echo "  built $NEW"
echo "  live: ${CURRENT:-none}"
echo "  next: sudo bash $NEW/deploy/activate-release.sh $NEW"
