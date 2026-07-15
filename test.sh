#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

"$ROOT_DIR/run.sh" test.TestBitSet
"$ROOT_DIR/run.sh" test.TestDataStructures
"$ROOT_DIR/run.sh" test.TestParallelDataStructures
"$ROOT_DIR/run.sh" test.TestOptimizedRegressions
"$ROOT_DIR/run.sh" test.TestNgramBKTreeRegression
"$ROOT_DIR/test/test-streaming.sh"
