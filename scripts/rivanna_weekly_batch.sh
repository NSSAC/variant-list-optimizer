#!/bin/bash

# Script to run weekly variant list optimization in a SLURM job
# Processes weeks from January 2020 through 2025-10-31

#SBATCH --job-name=vlopt-weekly-batch
#SBATCH -A bii_nssac
#SBATCH -p bii
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=40
#SBATCH --mem=0
#SBATCH -t 1-00:00:00
#SBATCH --output "/scratch/bl4zc/vlopt_weekly_batch.out"


## Set up where the core script is
SCRIPT_DIR="/sfs/gpfs/tardis/project/bii_nssac/people/bl4zc/variant-list-optimizer/scripts"
MAIN_SCRIPT="$SCRIPT_DIR/rivanna_runner_cli.sh"

# HHS region state-FIPS mappings
REGION_IDS=(
    hhs01
    hhs02
    hhs03
    hhs04
    hhs05
    hhs06
    hhs07
    hhs08
    hhs09
    hhs10
    us
)

declare -A FIPS_BY_REGION
FIPS_BY_REGION[hhs01]="9,23,25,33,44,50"
FIPS_BY_REGION[hhs02]="34,36"
FIPS_BY_REGION[hhs03]="10,11,24,42,51,54"
FIPS_BY_REGION[hhs04]="1,12,13,21,28,37,45,47"
FIPS_BY_REGION[hhs05]="17,18,26,27,39,55"
FIPS_BY_REGION[hhs06]="5,22,35,40,48"
FIPS_BY_REGION[hhs07]="19,20,29,31"
FIPS_BY_REGION[hhs08]="8,30,38,46,49,56"
FIPS_BY_REGION[hhs09]="4,6,15,32"
FIPS_BY_REGION[hhs10]="2,16,41,53"
FIPS_BY_REGION[us]="1,2,4,5,6,8,9,10,11,12,13,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,44,45,46,47,48,49,50,51,53,54,55,56"

# Start and end dates
START_DATE="2022-01-08"  # First Saturday mid 2021
END_DATE="2025-01-04"    # Last Saturday on or before 2025-10-31

# Convert dates to seconds since epoch for easier arithmetic
start_epoch=$(date -d "$START_DATE" +%s)
end_epoch=$(date -d "$END_DATE" +%s)

echo "Starting weekly variant list optimization"
echo "Period: $START_DATE to $END_DATE"
echo "Start time: $(date)"
echo ""

### hide warnings generated in the script
export DISABLE_PANDERA_IMPORT_WARNING=True

echo "Running one-time build warm-up"
bash "$MAIN_SCRIPT" --build-only

if [ $? -ne 0 ]; then
    echo "✗ Build warm-up FAILED. Aborting batch run."
    exit 1
fi

echo "✓ Build warm-up completed"
echo "All region/week runs will use --skip-build"
echo ""


for region_id in "${REGION_IDS[@]}"; do
    fips_list="${FIPS_BY_REGION[$region_id]}"
    region_log="/scratch/bl4zc/vlopt_weekly_batch_${region_id}.out"

    : > "$region_log"

    current_epoch=$start_epoch
    week_num=1

    echo "=========================================="
    echo "Processing region: $region_id"
    echo "FIPS list: $fips_list"
    echo "Region log: $region_log"
    echo ""

    {
        echo "=========================================="
        echo "Region: $region_id"
        echo "FIPS list: $fips_list"
        echo "Batch start time: $(date)"
        echo ""
    } >> "$region_log"

    while [ $current_epoch -le $end_epoch ]; do
        # Convert epoch back to date
        current_date=$(date -d @$current_epoch +%Y-%m-%d)

        # Calculate start date 180 days earlier
        start_date_epoch=$((current_epoch - 180 * 86400))
        start_date=$(date -d @$start_date_epoch +%Y-%m-%d)

        echo "=========================================="
        echo "Region $region_id - Week $week_num: Processing start-date=$start_date to end-date=$current_date"
        echo "Start time: $(date)"

        {
            echo "=========================================="
            echo "Region $region_id - Week $week_num"
            echo "Processing start-date=$start_date to end-date=$current_date"
            echo "Start time: $(date)"
        } >> "$region_log"

        bash "$MAIN_SCRIPT" \
            --start-date "$start_date" \
            --end-date "$current_date" \
            --fips-list "$fips_list" \
            --region-name "$region_id" \
            --skip-build \
            >> "$region_log" 2>&1

        if [ $? -eq 0 ]; then
            echo "✓ Region $region_id week $week_num completed successfully"
            echo "✓ Region $region_id week $week_num completed successfully" >> "$region_log"
        else
            echo "✗ Region $region_id week $week_num FAILED"
            echo "✗ Region $region_id week $week_num FAILED" >> "$region_log"
        fi

        echo "End time: $(date)"
        echo ""

        {
            echo "End time: $(date)"
            echo ""
        } >> "$region_log"

        # Move to next Saturday (add 7 days = 604800 seconds)
        current_epoch=$((current_epoch + 604800))
        week_num=$((week_num + 1))
    done

    {
        echo "=========================================="
        echo "Region: $region_id complete"
        echo "Region end time: $(date)"
        echo ""
    } >> "$region_log"
done

echo "=========================================="
echo "All weeks processed"
echo "End time: $(date)"
