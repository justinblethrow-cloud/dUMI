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
"$ROOT_DIR/run.sh" test.TestNStringOracleRegression
"$ROOT_DIR/run.sh" test.TestThresholdParallelRegressions
"$ROOT_DIR/run.sh" test.TestParallelTraversalScheduling
"$ROOT_DIR/run.sh" test.TestBKTreeDepthRegressions
"$ROOT_DIR/run.sh" test.TestResourceAndBoundsRegressions
"$ROOT_DIR/run.sh" test.TestNgramBKTreeRegression
"$ROOT_DIR/test/test-streaming.sh"
"$ROOT_DIR/test/test-cli.sh"
python3 "$ROOT_DIR/test/test_benchmark_external.py"
python3 "$ROOT_DIR/test/test_benchmark_external_routing.py"
python3 "$ROOT_DIR/test/test_benchmark_external_summary.py"
python3 "$ROOT_DIR/test/test_benchmark_public_export.py"
python3 "$ROOT_DIR/test/test_semantic_alignment_group_check.py"
python3 "$ROOT_DIR/test/test_cluster_partition_check.py"
python3 "$ROOT_DIR/test/test_directional_oracle_check.py"
