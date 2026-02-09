#!/usr/bin/env bash
# Migrate existing screenshot and image files to Git LFS.
#
# The .gitattributes already has LFS tracking patterns, but files committed
# before LFS was configured are stored as regular git blobs. This script
# re-adds them through the LFS clean filter so they become LFS pointer files.
#
# Prerequisites:
#   - git lfs installed (https://git-lfs.com)
#   - git lfs install (run once per machine)
#
# Usage:
#   ./scripts/migrate-lfs.sh
#
set -euo pipefail

# Ensure git-lfs is available
if ! command -v git-lfs &>/dev/null; then
    echo "Error: git-lfs is not installed. Install it from https://git-lfs.com" >&2
    exit 1
fi

echo "Migrating screenshots and images to Git LFS..."

# Remove from index and re-add so the LFS clean filter processes them
git rm --cached docs/screenshots/*.png app/static/images/*.png app/static/images/*.svg 2>/dev/null || true
git add docs/screenshots/*.png app/static/images/*.png app/static/images/*.svg

echo ""
echo "LFS status:"
git lfs status

echo ""
echo "Migration staged. Review with 'git diff --cached --stat' then commit:"
echo "  git commit -m 'chore: Convert screenshots and images to Git LFS objects'"
echo "  git push"
