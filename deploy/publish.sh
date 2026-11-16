#!/usr/bin/env bash
# Publishes out/ to the rolling pre-release `main-latest` (doc/architecture.md §11.1).
# The package goes up first and the manifest last, so a manifest never points to a missing
# package. Older packages are removed afterwards. Needs `gh` with a token that may write releases.
set -euo pipefail
cd "$(dirname "$0")/.."

tag="${RELEASE_TAG:-main-latest}"
package="$(python3 -c 'import json; print(json.load(open("out/manifest.json"))["package"])')"

if ! gh release view "$tag" >/dev/null 2>&1; then
  gh release create "$tag" --prerelease --target main --title "main (latest build)" \
    --notes "Rolling build of main, installed automatically by the updater on the LXC. Not a versioned release."
fi
gh release upload "$tag" "out/$package" --clobber
gh release upload "$tag" out/manifest.json --clobber
gh release view "$tag" --json assets --jq '.assets[].name' |
  grep -E '^emon2influx-parser-[0-9a-f]{40}\.tar\.gz$' | grep -vx -- "$package" |
  while read -r old; do gh release delete-asset "$tag" "$old" --yes; done
echo "published $package to $tag"
