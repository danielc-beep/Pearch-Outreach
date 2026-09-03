#!/usr/bin/env bash
#
# Move this directory into its own GitHub repository.
#
# Pearch Outreach was developed inside the pearch-audit- checkout because the
# session's GitHub app can't create repositories. This script lifts it out
# into a standalone repo with its own history, in one go.
#
# Usage, from inside pearch-outreach/:
#     gh repo create danielc-beep/pearch-outreach --private   # or create it in the browser
#     ./scripts/publish_new_repo.sh danielc-beep/pearch-outreach
#
set -euo pipefail

REPO="${1:-}"
if [[ -z "$REPO" ]]; then
  echo "usage: $0 <owner/repo>" >&2
  exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGING="$(mktemp -d)/pearch-outreach"

echo "→ copying $HERE to $STAGING"
mkdir -p "$STAGING"
# Copy everything except the parent repo's git metadata and local artefacts.
(cd "$HERE" && tar --exclude='.git' --exclude='__pycache__' --exclude='*.db*' \
                   --exclude='.pytest_cache' -cf - .) | (cd "$STAGING" && tar -xf -)

cd "$STAGING"
git init -q -b main
git add -A
git commit -qm "Pearch Outreach: Australian business prospecting database and outreach engine"
git remote add origin "https://github.com/${REPO}.git"

echo "→ pushing to https://github.com/${REPO}"
git push -u origin main

echo
echo "Done. The standalone repo is at https://github.com/${REPO}"
echo "You can now delete pearch-outreach/ from the pearch-audit- checkout."
