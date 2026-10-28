#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .local
package=$(mktemp -d "$PWD/.local/lambda-package.XXXXXX")
.venv/bin/python -m pip install --require-hashes --no-compile --only-binary=:all: \
  --platform manylinux2014_x86_64 --implementation cp --python-version 3.12 \
  --target "$package" -r requirements.lock
cp -R backend/nevermind "$package/"
find "$package/nevermind" -name '*.pyc' -delete
archive="$PWD/.local/app-$(date +%Y%m%d%H%M%S).zip"
(cd "$package" && zip -q -r "$archive" . -x '*/__pycache__/*')
printf '%s\n' "$archive" >.local/package-path
echo "Lambda package: $archive"
