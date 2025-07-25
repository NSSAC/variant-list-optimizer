#!/bin/bash
# Test vlopt tool on Rivanna

set -Eeuo pipefail

trap 'echo "############ $BASH_COMMAND"' DEBUG

DATA_DIR="/home/parantapa/data/variant-list-optimizer"
WORK_DIR="/home/parantapa/scratch/variant-list-optimizer"


DATA_FILE="$DATA_DIR/outbreak_info_variants_states_long.csv"
START_DATE="2021-01-01"
END_DATE="2024-10-25"
FIPS_LIST="42,54,51,10,24" # HHS region 3
TEMPORAL_IMPORTANCE_DECAY="0.1"
DISTANCE_PENALTY="10.0"
LIST_SIZE=20

# Output files
VALIDATED_DATA_FILE="$WORK_DIR/obs-count.parquet"
UNWEIGHTED_VARIANT_GRAPH_FILE="$WORK_DIR/unweighted_variant-graph.json"
VARIANT_GRAPH_FILE="$WORK_DIR/variant-graph.json"

OPT_VARIANT_LIST_GREEDY="$WORK_DIR/opt-variant-list-greedy.json"
OPT_VARIANT_LIST_BEAM_SEARCH="$WORK_DIR/opt-variant-list-beam-search.json"
OPT_VARIANT_LIST_GUROBI="$WORK_DIR/opt-variant-list-gurobi.json"

# Create the graph
##################

rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"


# Step 1: Validate input
vlopt make-graph validate-input \
    --input "$DATA_FILE" \
    --output "$VALIDATED_DATA_FILE"

# Step 2: Create the unweighted graph
vlopt make-graph make-variant-graph \
    --input "$VALIDATED_DATA_FILE" \
    --output "$UNWEIGHTED_VARIANT_GRAPH_FILE" \
    --fips-list "$FIPS_LIST" \
    --start-date "$START_DATE" \
    --end-date "$END_DATE"

# Step 3: Add node importrance and edge distance to the graph
vlopt make-graph make-weighted-variant-graph \
    --input "$VALIDATED_DATA_FILE" \
    --graph "$UNWEIGHTED_VARIANT_GRAPH_FILE" \
    --output "$VARIANT_GRAPH_FILE" \
    --reference-date "$END_DATE" \
    --temporal-importance-decay "$TEMPORAL_IMPORTANCE_DECAY"

# Optimize the varaint list
###########################

vlopt optimize optimize-greedy \
    --input "$VARIANT_GRAPH_FILE" \
    --output "$OPT_VARIANT_LIST_GREEDY" \
    --size "$LIST_SIZE" \
    --distance-penalty "$DISTANCE_PENALTY"

vlopt optimize optimize-beam-search \
    --input "$VARIANT_GRAPH_FILE" \
    --output "$OPT_VARIANT_LIST_BEAM_SEARCH" \
    --size "$LIST_SIZE" \
    --distance-penalty "$DISTANCE_PENALTY" \
    --beam-width "$LIST_SIZE"

vlopt optimize optimize-gurobi \
    --input "$VARIANT_GRAPH_FILE" \
    --output "$OPT_VARIANT_LIST_GUROBI" \
    --size "$LIST_SIZE" \
    --distance-penalty "$DISTANCE_PENALTY"

