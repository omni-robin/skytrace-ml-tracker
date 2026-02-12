#!/usr/bin/env bash
set -euo pipefail

# Build a Pop!_OS/Ubuntu-friendly .deb using cargo-deb.
# Run this on a Linux machine.

cd "$(dirname "$0")/.."

if ! command -v cargo-deb >/dev/null 2>&1; then
  echo "cargo-deb not found. Install it with: cargo install cargo-deb" >&2
  exit 2
fi

cargo build --release
cargo deb --no-build

echo "Built .deb(s) in target/debian/"