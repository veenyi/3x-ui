#!/usr/bin/env bash
# Re-apply the sponsor opt-out patch onto a fresh upstream tag and trigger a build.
# Run from inside your fork checkout (needs the `git` remote layout below).
#
#   ./sync-upstream.sh v3.12.0
#
set -euo pipefail

TAG="${1:?usage: sync-upstream.sh <upstream-tag, e.g. v3.11.0>}"
PATCH="$(dirname "$0")/../patches/0001-sponsors-env-optout.patch"

git remote get-url upstream >/dev/null 2>&1 || \
  git remote add upstream https://github.com/MHSanaei/3x-ui.git

echo "==> fetching $TAG from upstream"
git fetch --force --tags upstream "refs/tags/${TAG}:refs/tags/${TAG}"

BRANCH="fork/${TAG}"
echo "==> creating $BRANCH from $TAG"
git checkout -B "$BRANCH" "$TAG"

if grep -q 'XUI_DISABLE_SPONSORS' internal/web/service/panel/sponsor.go; then
  echo "==> patch already present, nothing to do"
else
  echo "==> applying patch"
  # --3way gives a resolvable conflict instead of a bare failure when upstream
  # shifts the lines around; resolve by hand and re-run.
  if ! git apply --3way "$PATCH"; then
    cat >&2 <<'MSG'
PATCH FAILED. Upstream moved sponsor.go.
Fix: open internal/web/service/panel/sponsor.go, find `func cachedSponsors()`,
and re-add the sponsorsDisabled() guard as its first statement, then
`git add -A && git commit`. See patches/0001-*.patch for the intent.
MSG
    exit 1
  fi
  git add internal/web/service/panel/sponsor.go
  git commit -m "panel: add XUI_DISABLE_SPONSORS opt-out for sponsor slots

Upstream declined an opt-out (MHSanaei/3x-ui#6845, #6856, #6818 closed as
not_planned). The guard returns an empty list before the mutex, so every
sponsor slot (login/dashboard/page) renders nothing. Default is off, so
sponsor_test.go, which stubs sponsorsURL itself, still passes."
fi

NEW_TAG="${TAG}-nosponsor"
echo "==> pushing $BRANCH and tagging $NEW_TAG"
git push -f origin "$BRANCH"
git tag -f "$NEW_TAG"
git push -f origin "$NEW_TAG"

echo "==> build triggered by the tag push (.github/workflows/build-xui-nosponsors.yml)"
echo "    artifact: x-ui-linux-amd64-nosponsor"
