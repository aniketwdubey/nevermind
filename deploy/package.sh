#!/usr/bin/env bash
# Build the Lambda dependency bundle that CDK uploads as a normal file asset.
set -euo pipefail
cd "$(dirname "$0")/.."
package=".local/lambda-package"
rm -rf "$package"
mkdir -p "$package"
.venv/bin/python -m pip install --quiet --require-hashes --no-compile --only-binary=:all: \
  --platform manylinux2014_x86_64 --implementation cp --python-version 3.12 \
  --target "$package" -r requirements.lock
cp -R backend/nevermind "$package/"
find "$package" -name '__pycache__' -type d -prune -exec rm -rf {} +
echo "Lambda package: $package"
