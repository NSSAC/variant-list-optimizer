#!/bin/bash

module load gcc/14.2.0
export GRB_LICENSE_FILE="/apps/software/vendor/gurobi/13.0.0/gurobi.lic"

PROJECT="variant-list-optimizer"
WORK_DIR="/scratch/$USER/$PROJECT"
BUILD_ROOT="$WORK_DIR"

DATA_DIR="/home/parantapa/data/variant-list-optimizer"
BUILD_DIR="$BUILD_ROOT/build/Release"

# Parse command line arguments
DATA_FILE="/project/nssac_covid19/COVID-19_commons/products/external_data_collection/variants/outbreak_info_variants_states_long.csv"
START_DATE="2021-01-01"
END_DATE="2024-10-25"
FIPS_LIST="42,54,51,10,24"
TEMPORAL_IMPORTANCE_DECAY="0.1"
DISTANCE_PENALTY="10.0"
LIST_SIZE=20

while [[ $# -gt 0 ]]; do
    case $1 in
        --data-file) DATA_FILE="$2"; shift 2 ;;
        --start-date) START_DATE="$2"; shift 2 ;;
        --end-date) END_DATE="$2"; shift 2 ;;
        --fips-list) FIPS_LIST="$2"; shift 2 ;;
        --temporal-decay) TEMPORAL_IMPORTANCE_DECAY="$2"; shift 2 ;;
        --distance-penalty) DISTANCE_PENALTY="$2"; shift 2 ;;
        --list-size) LIST_SIZE="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo "Data file: $DATA_FILE"
echo "FIPS list: $FIPS_LIST"
echo "Start date: $START_DATE"
echo "End date: $END_DATE"
echo "Temporal importance decay: $TEMPORAL_IMPORTANCE_DECAY"
echo "Distance penalty: $DISTANCE_PENALTY"
echo "List size: $LIST_SIZE"

eval "$( conda shell.bash hook )"
    . "$BUILD_DIR/generators/conanrun.sh"

    set -Eeuo pipefail
    trap 'echo "############ $BASH_COMMAND"' DEBUG

    cmake --build "$BUILD_DIR" --parallel

    conda activate "$PROJECT"
    PATH="$BUILD_DIR:$PATH"

# Intermediate files
VALIDATED_DATA_FILE="$WORK_DIR/obs-count.parquet"
UNWEIGHTED_VARIANT_GRAPH_FILE="$WORK_DIR/unweighted_variant-graph.json"
VARIANT_GRAPH_FILE="$WORK_DIR/variant-graph.json"
VARIANT_GRAPH_H5_FILE="$WORK_DIR/variant-graph.h5"

# Step 1: Validate input
rm -f "$VALIDATED_DATA_FILE"
vlopt make-graph validate-input \
    --input "$DATA_FILE" \
    --output "$VALIDATED_DATA_FILE"

# Step 2: Create the unweighted graph
rm -f "$UNWEIGHTED_VARIANT_GRAPH_FILE"
vlopt make-graph make-variant-graph \
    --input "$VALIDATED_DATA_FILE" \
    --output "$UNWEIGHTED_VARIANT_GRAPH_FILE" \
    --fips-list "$FIPS_LIST" \
    --start-date "$START_DATE" \
    --end-date "$END_DATE"

# Step 3: Add node importrance and edge distance to the graph
rm -f "$VARIANT_GRAPH_FILE"
vlopt make-graph make-weighted-variant-graph \
    --input "$VALIDATED_DATA_FILE" \
    --graph "$UNWEIGHTED_VARIANT_GRAPH_FILE" \
    --output "$VARIANT_GRAPH_FILE" \
    --reference-date "$END_DATE" \
    --temporal-importance-decay "$TEMPORAL_IMPORTANCE_DECAY"

# Step 4: Create hdf5 version of the graph
rm -f "$VARIANT_GRAPH_H5_FILE"
vlopt make-graph make-weighted-variant-graph-h5 \
    --input "$VARIANT_GRAPH_FILE" \
    --output "$VARIANT_GRAPH_H5_FILE"


rm -f "$WORK_DIR/optimal-list-index-highs.json"
rm -f "$WORK_DIR/optimal-list-highs.json"
compute-optimal-tlist \
    --sample 0 \
    --size "$LIST_SIZE" \
    --method highs \
    --input-file "$VARIANT_GRAPH_H5_FILE" \
    --output-file "$WORK_DIR/optimal-list-index-highs.json" \
    --timelimit 300

vlopt optimize optimize-external \
    --input "$VARIANT_GRAPH_FILE" \
    --index-list "$WORK_DIR/optimal-list-index-highs.json" \
    --output "$WORK_DIR/optimal-list-highs_$END_DATE.json"


run_test() {
    eval "$( conda shell.bash hook )"
    . "$BUILD_DIR/generators/conanrun.sh"

    set -Eeuo pipefail
    trap 'echo "############ $BASH_COMMAND"' DEBUG

    cmake --build "$BUILD_DIR" --parallel

    conda activate "$PROJECT"
    PATH="$BUILD_DIR:$PATH"

    # Input files
    DATA_FILE="/project/nssac_covid19/COVID-19_commons/products/external_data_collection/variants/outbreak_info_variants_states_long.csv"
    START_DATE="2021-01-01"
    END_DATE="2024-10-25"
    FIPS_LIST="42,54,51,10,24" # HHS region 3
    TEMPORAL_IMPORTANCE_DECAY="0.1"
    DISTANCE_PENALTY="10.0"
    LIST_SIZE=20

    # Intermediate files
    VALIDATED_DATA_FILE="$WORK_DIR/obs-count.parquet"
    UNWEIGHTED_VARIANT_GRAPH_FILE="$WORK_DIR/unweighted_variant-graph.json"
    VARIANT_GRAPH_FILE="$WORK_DIR/variant-graph.json"
    VARIANT_GRAPH_H5_FILE="$WORK_DIR/variant-graph.h5"

    # Step 1: Validate input
    rm -f "$VALIDATED_DATA_FILE"
    vlopt make-graph validate-input \
        --input "$DATA_FILE" \
        --output "$VALIDATED_DATA_FILE"

    # Step 2: Create the unweighted graph
    rm -f "$UNWEIGHTED_VARIANT_GRAPH_FILE"
    vlopt make-graph make-variant-graph \
        --input "$VALIDATED_DATA_FILE" \
        --output "$UNWEIGHTED_VARIANT_GRAPH_FILE" \
        --fips-list "$FIPS_LIST" \
        --start-date "$START_DATE" \
        --end-date "$END_DATE"

    # Step 3: Add node importrance and edge distance to the graph
    rm -f "$VARIANT_GRAPH_FILE"
    vlopt make-graph make-weighted-variant-graph \
        --input "$VALIDATED_DATA_FILE" \
        --graph "$UNWEIGHTED_VARIANT_GRAPH_FILE" \
        --output "$VARIANT_GRAPH_FILE" \
        --reference-date "$END_DATE" \
        --temporal-importance-decay "$TEMPORAL_IMPORTANCE_DECAY"

    # Step 4: Create hdf5 version of the graph
    rm -f "$VARIANT_GRAPH_H5_FILE"
    vlopt make-graph make-weighted-variant-graph-h5 \
        --input "$VARIANT_GRAPH_FILE" \
        --output "$VARIANT_GRAPH_H5_FILE"

    # Old optimizers
    ###########################

    # rm -f "$WORK_DIR/optimal-list-greedy.json"
    # vlopt optimize optimize-greedy \
    #     --input "$VARIANT_GRAPH_FILE" \
    #     --output "$WORK_DIR/optimal-list-greedy.json" \
    #     --size "$LIST_SIZE" \
    #     --distance-penalty "$DISTANCE_PENALTY"

    # vlopt optimize optimize-beam-search \
    #     --input "$VARIANT_GRAPH_FILE" \
    #     --output "$OPT_VARIANT_LIST_BEAM_SEARCH" \
    #     --size "$LIST_SIZE" \
    #     --distance-penalty "$DISTANCE_PENALTY" \
    #     --beam-width "$LIST_SIZE"
    #
    # vlopt optimize optimize-gurobi \
    #     --input "$VARIANT_GRAPH_FILE" \
    #     --output "$OPT_VARIANT_LIST_GUROBI" \
    #     --size "$LIST_SIZE" \
    #     --distance-penalty "$DISTANCE_PENALTY"

    # New optimizers
    ###########################

    rm -f "$WORK_DIR/optimal-list-index-greedy.json"
    rm -f "$WORK_DIR/optimal-list-greedy.json"
    compute-optimal-tlist \
        --sample 0 \
        --size "$LIST_SIZE" \
        --method greedy \
        --input-file "$VARIANT_GRAPH_H5_FILE" \
        --output-file "$WORK_DIR/optimal-list-index-greedy.json"

    vlopt optimize optimize-external \
        --input "$VARIANT_GRAPH_FILE" \
        --index-list "$WORK_DIR/optimal-list-index-greedy.json" \
        --output "$WORK_DIR/optimal-list-greedy.json"

    rm -f "$WORK_DIR/optimal-list-index-gurobi.json"
    rm -f "$WORK_DIR/optimal-list-gurobi.json"
    compute-optimal-tlist \
        --sample 0 \
        --size "$LIST_SIZE" \
        --method gurobi \
        --input-file "$VARIANT_GRAPH_H5_FILE" \
        --output-file "$WORK_DIR/optimal-list-index-gurobi.json" \
        --timelimit 300

    vlopt optimize optimize-external \
        --input "$VARIANT_GRAPH_FILE" \
        --index-list "$WORK_DIR/optimal-list-index-gurobi.json" \
        --output "$WORK_DIR/optimal-list-gurobi.json"

    rm -f "$WORK_DIR/optimal-list-index-highs.json"
    rm -f "$WORK_DIR/optimal-list-highs.json"
    compute-optimal-tlist \
        --sample 0 \
        --size "$LIST_SIZE" \
        --method highs \
        --input-file "$VARIANT_GRAPH_H5_FILE" \
        --output-file "$WORK_DIR/optimal-list-index-highs.json" \
        --timelimit 300

    vlopt optimize optimize-external \
        --input "$VARIANT_GRAPH_FILE" \
        --index-list "$WORK_DIR/optimal-list-index-highs.json" \
        --output "$WORK_DIR/optimal-list-highs.json"
}
