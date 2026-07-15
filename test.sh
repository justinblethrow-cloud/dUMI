#!/usr/bin/env bash
set -euo pipefail

./run.sh test.TestBitSet
./run.sh test.TestDataStructures
./run.sh test.TestParallelDataStructures
