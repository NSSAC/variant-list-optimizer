#!/bin/bash

module load gcc/14.2.0
export GRB_LICENSE_FILE="/apps/software/vendor/gurobi/13.0.0/gurobi.lic"

PROJECT="variant-list-optimizer"
WORK_DIR="/scratch/$USER/$PROJECT"
BUILD_ROOT="$WORK_DIR"

DATA_DIR="/home/parantapa/data/variant-list-optimizer"
BUILD_DIR="$BUILD_ROOT/build/Release"

# Parse command line arguments
DATA_FILE="/home/bl4zc/bi_bl4zc/variant-list-optimizer/data/cov-spectrum_variants_states_long_all-dates.csv"
START_DATE="2021-01-01"
END_DATE="2024-10-25"
FIPS_LIST="42,54,51,10,24"
REGION_NAME="hhs03"
##TEMPORAL_IMPORTANCE_DECAY="0.1"
TEMPORAL_IMPORTANCE_DECAY="0.025"
##DISTANCE_PENALTY="10.0"
DISTANCE_PENALTY="5.0"
LIST_SIZE=10
SKIP_BUILD=0
BUILD_ONLY=0

while [[ $# -gt 0 ]]; do
    case $1 in
        --data-file) DATA_FILE="$2"; shift 2 ;;
        --start-date) START_DATE="$2"; shift 2 ;;
        --end-date) END_DATE="$2"; shift 2 ;;
        --fips-list) FIPS_LIST="$2"; shift 2 ;;
        --region-name) REGION_NAME="$2"; shift 2 ;;
        --temporal-decay) TEMPORAL_IMPORTANCE_DECAY="$2"; shift 2 ;;
        --distance-penalty) DISTANCE_PENALTY="$2"; shift 2 ;;
        --list-size) LIST_SIZE="$2"; shift 2 ;;
        --skip-build) SKIP_BUILD=1; shift 1 ;;
        --build-only) BUILD_ONLY=1; shift 1 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

    REGION_SLUG=$(echo "$REGION_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9._-]/-/g')
    FILE_SUFFIX="region-$REGION_SLUG-size-$LIST_SIZE-end-date_$END_DATE.json"
    LIST_FILE="$WORK_DIR/optimal-list-highs_$FILE_SUFFIX"
    METADATA_FILE="$WORK_DIR/metadata_$FILE_SUFFIX"

echo "Data file: $DATA_FILE"
echo "Start date: $START_DATE"
echo "End date: $END_DATE"
echo "FIPS list: $FIPS_LIST"
echo "Region name: $REGION_NAME"
echo "Temporal importance decay: $TEMPORAL_IMPORTANCE_DECAY"
echo "Distance penalty: $DISTANCE_PENALTY"
echo "List size: $LIST_SIZE"
echo "Skip build: $SKIP_BUILD"
echo "Build only: $BUILD_ONLY"
echo "Output list file: $LIST_FILE"
echo ""


# Create metadata JSON file with parameters
cat > "$METADATA_FILE" <<EOF
{
  "data_file": "$DATA_FILE",
    "region_name": "$REGION_NAME",
  "fips_list": "$FIPS_LIST",
  "start_date": "$START_DATE",
  "end_date": "$END_DATE",
  "temporal_importance_decay": $TEMPORAL_IMPORTANCE_DECAY,
  "distance_penalty": $DISTANCE_PENALTY,
  "list_size": $LIST_SIZE,
  "output_list_file": "$LIST_FILE"
}
EOF

echo "Parameters saved to: $METADATA_FILE"



eval "$( conda shell.bash hook )"
    . "$BUILD_DIR/generators/conanrun.sh"

    set -Eeuo pipefail
    trap 'echo "############ $BASH_COMMAND"' DEBUG

    if [[ "$SKIP_BUILD" -eq 1 ]]; then
        echo "Skipping build step (--skip-build)"
    else
        cmake --build "$BUILD_DIR" --parallel
    fi

    conda activate "$PROJECT"
    PATH="$BUILD_DIR:$PATH"

if [[ "$BUILD_ONLY" -eq 1 ]]; then
    echo "Build-only mode complete. Exiting before optimization pipeline."
    exit 0
fi

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
    --output "$LIST_FILE"

echo "Optimal variant list saved to: $LIST_FILE"
