#!/bin/bash

# Script to submit SLURM jobs for weekly variant list optimization
# Runs from January 2020 through 2025-10-31 with end-date as each Saturday

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAIN_SCRIPT="$SCRIPT_DIR/rivanna_runner_cli.sh"

# Start and end dates
START_DATE="2020-01-04"  # First Saturday in January 2020
END_DATE="2025-10-25"    # Last Saturday on or before 2025-10-31

# Convert dates to seconds since epoch for easier arithmetic
start_epoch=$(date -d "$START_DATE" +%s)
end_epoch=$(date -d "$END_DATE" +%s)
current_epoch=$start_epoch

# Array to store job IDs
declare -a JOB_IDS

echo "Submitting SLURM jobs for weekly variant list optimization"
echo "Period: $START_DATE to $END_DATE"
echo ""

while [ $current_epoch -le $end_epoch ]; do
    # Convert epoch back to date
    current_date=$(date -d @$current_epoch +%Y-%m-%d)
    
    # Create a temporary SLURM script for this week
    SLURM_SCRIPT="/tmp/slurm_job_${current_date}.sh"
    
    cat > "$SLURM_SCRIPT" << 'SLURM_EOF'
#!/bin/bash
#SBATCH --job-name=vlopt-weekly
#SBATCH --partition=bii
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=04:00:00
#SBATCH --output=%x_%j.log
#SBATCH --error=%x_%j.err

SLURM_EOF

    # Add the main script call with the end-date parameter
    echo "bash $MAIN_SCRIPT --end-date $current_date" >> "$SLURM_SCRIPT"
    
    chmod +x "$SLURM_SCRIPT"
    
    # Submit the job
    JOB_OUTPUT=$(sbatch "$SLURM_SCRIPT")
    JOB_ID=$(echo $JOB_OUTPUT | awk '{print $NF}')
    JOB_IDS+=($JOB_ID)
    
    echo "Submitted job $JOB_ID for week ending $current_date"
    
    # Move to next Saturday (add 7 days = 604800 seconds)
    current_epoch=$((current_epoch + 604800))
done

echo ""
echo "Submitted ${#JOB_IDS[@]} jobs"
echo "Job IDs: ${JOB_IDS[*]}"
