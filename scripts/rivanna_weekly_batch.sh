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

# Start and end dates
START_DATE="2025-01-04"  # First Saturday mid 2021
END_DATE="2026-02-21"    # Last Saturday on or before 2025-10-31

# Convert dates to seconds since epoch for easier arithmetic
start_epoch=$(date -d "$START_DATE" +%s)
end_epoch=$(date -d "$END_DATE" +%s)
current_epoch=$start_epoch

week_num=1

echo "Starting weekly variant list optimization"
echo "Period: $START_DATE to $END_DATE"
echo "Start time: $(date)"
echo ""

### hide warnings generated in the script
export DISABLE_PANDERA_IMPORT_WARNING=True


while [ $current_epoch -le $end_epoch ]; do
    # Convert epoch back to date
    current_date=$(date -d @$current_epoch +%Y-%m-%d)
    
    # Calculate start date 180 days earlier
    start_date_epoch=$((current_epoch - 180 * 86400))
    start_date=$(date -d @$start_date_epoch +%Y-%m-%d)
    
    echo "=========================================="
    echo "Week $week_num: Processing start-date=$start_date to end-date=$current_date"
    echo "Start time: $(date)"
    
    # Run the main script with both start-date and end-date flags
    
    bash "$MAIN_SCRIPT" --start-date "$start_date" --end-date "$current_date"
    
    if [ $? -eq 0 ]; then
        echo "✓ Week $week_num completed successfully"
    else
        echo "✗ Week $week_num FAILED"
    fi
    
    echo "End time: $(date)"
    echo ""
    
    # Move to next Saturday (add 7 days = 604800 seconds)
    current_epoch=$((current_epoch + 604800))
    week_num=$((week_num + 1))
done

echo "=========================================="
echo "All weeks processed"
echo "End time: $(date)"
