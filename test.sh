#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

"$ROOT_DIR/run.sh" test.TestBitSet
"$ROOT_DIR/run.sh" test.TestDataStructures
"$ROOT_DIR/run.sh" test.TestParallelDataStructures
"$ROOT_DIR/run.sh" test.TestOptimizedRegressions
"$ROOT_DIR/run.sh" test.TestDeduplicateSAMHardening
"$ROOT_DIR/run.sh" test.TestReleaseRegressions
"$ROOT_DIR/run.sh" test.TestNKeyRegressions
"$ROOT_DIR/run.sh" test.TestThresholdParallelRegressions
"$ROOT_DIR/run.sh" test.TestParallelTraversalScheduling
"$ROOT_DIR/run.sh" test.TestBKTreeDepthRegressions
"$ROOT_DIR/run.sh" test.TestResourceAndBoundsRegressions
"$ROOT_DIR/run.sh" test.TestNgramBKTreeRegression
"$ROOT_DIR/test/test-streaming.sh"
"$ROOT_DIR/test/test-cli.sh"
