#!/usr/bin/env bash
# Builds the release package (doc/architecture.md §11.1):
#   out/emon2influx-parser-<sha>.tar.gz  wheelhouse (this package + all dependencies), deploy/,
#                                        LICENSE, NOTICE
#   out/manifest.json                    version, sha, sha256, python, package, created
# Usage: deploy/package.sh [<git sha>]   (default: HEAD)
set -euo pipefail
cd "$(dirname "$0")/.."

sha="${1:-$(git rev-parse HEAD)}"
python="${PYTHON:-python3}"
python_version="$("$python" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
out=out
pkg="$out/pkg"
rm -rf "$out"
mkdir -p "$pkg/wheelhouse"

# The commit goes into the wheel, so the parser reports <version>+<short sha>.
printf '{"sha": "%s"}\n' "$sha" >src/emon2influx/build.json
trap 'rm -f src/emon2influx/build.json' EXIT

"$python" -m pip wheel --quiet --wheel-dir "$pkg/wheelhouse" .
# Everything must be pure Python, so the package runs on any CPU architecture.
if find "$pkg/wheelhouse" -name '*.whl' ! -name '*-none-any.whl' | grep -q .; then
  echo "platform-specific wheels found:" >&2
  find "$pkg/wheelhouse" -name '*.whl' ! -name '*-none-any.whl' >&2
  exit 1
fi
cp -r deploy "$pkg/deploy"
cp LICENSE NOTICE "$pkg/"

version="$("$python" -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
name="emon2influx-parser-$sha.tar.gz"
tar -czf "$out/$name" -C "$pkg" .
checksum="$(sha256sum "$out/$name" | cut -d' ' -f1)"
cat >"$out/manifest.json" <<JSON
{
  "version": "$version+${sha:0:7}",
  "sha": "$sha",
  "sha256": "$checksum",
  "python": "$python_version",
  "package": "$name",
  "created": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON
echo "built $out/$name ($(du -h "$out/$name" | cut -f1)) for Python $python_version"
