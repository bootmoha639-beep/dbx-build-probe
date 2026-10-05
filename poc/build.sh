#!/usr/bin/env bash
# Cut the probe sdist and print the commands to publish it as a GitHub release asset.
#
#   ./build.sh              -> builds dist/zzbuildprobe-<version>.tar.gz
#   ./build.sh v13          -> builds, then prints the gh commands to publish tag v13
#
# The only thing that matters for the report is that the resulting tarball is
# reachable over HTTPS. Any host works; the reported chain used a GitHub release
# asset of this repository.
set -euo pipefail

cd "$(dirname "$0")"
TAG="${1:-}"

VERSION=$(sed -n "s/.*version='\([0-9.]*\)'.*/\1/p" setup.py | head -1)
[ -n "$VERSION" ] || { echo "could not read version from setup.py" >&2; exit 1; }
ARTIFACT="zzbuildprobe-${VERSION}.tar.gz"

rm -rf dist build ./*.egg-info zzbuildprobe.egg-info
python setup.py sdist --dist-dir dist >/dev/null
cp "dist/${ARTIFACT}" "dist/${ARTIFACT}"
echo "built: dist/${ARTIFACT}"
sha256sum "dist/${ARTIFACT}"

if [ -n "$TAG" ]; then
  cat <<EOF

publish:
  gh release create ${TAG} "dist/${ARTIFACT}" -R <owner>/<repo> -t ${TAG}
  # asset URL becomes:
  #   https://github.com/<owner>/<repo>/releases/download/${TAG}/${ARTIFACT}
EOF
fi
