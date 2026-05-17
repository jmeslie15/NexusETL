import subprocess
import time
import sys
import os
from prefect import flow, task

@task(name="Execute Python Script", log_prints=True)
def run_script(step_name: str, script_name: str, args: list = None): # type: ignore
    """Executes a python script with optional arguments and monitors its exit status."""
    print(f"\n{'='*50}\n🚀 STARTING: {step_name}\n{'='*50}")
    start_time = time.time()
    
    # Target the script inside the src/ folder explicitly
    script_path = os.path.join("src", script_name)
    
    # Build the terminal command
    cmd = ['python', script_path]
    if args:
        cmd.extend(args)
        
    try:
        subprocess.run(cmd, check=True)
        elapsed_time = round(time.time() - start_time, 2)
        print(f"\n✅ COMPLETED: {step_name} (Took {elapsed_time}s)\n")
        
    except subprocess.CalledProcessError as e:
        print(f"\n❌ FAILED: {step_name} crashed with exit code {e.returncode}.")
        raise Exception(f"Pipeline halted: {script_name} failed.") from e
    except FileNotFoundError as e:
        print(f"\n❌ FAILED: Could not find script '{script_path}'.")
        raise Exception(f"Pipeline halted: File not found ({script_path}). Check your directory.") from e

@flow(name="RiftQuant ETL Pipeline", description="Dynamic ETL triggered by Tournament URL")
def execute_pipeline(tournament_url: str):
    print(f"--- NEW PIPELINE RUN INITIATED FOR: {tournament_url} ---")
    
    # Step 1: Execute the initial scraper and pass the URL parameter
    run_script("1. Match Scraper", "matches_webscraper.py", args=[tournament_url])
    time.sleep(2)
    
    # Steps 2-7: The state-driven cleanup crew housed inside /src
    STATE_DRIVEN_STEPS = [
        {"name": "2. Team URL Builder", "script": "build_teams.py"},
        {"name": "3. Player Database Builder", "script": "build_players.py"},
        {"name": "4. Draft Scraper", "script": "drafts_webscraper.py"},
        {"name": "5. Init Match Participants", "script": "1_init_match_participants.py"},
        {"name": "6. Update Advanced Metrics", "script": "2_update_advanced_stats.py"},
        {"name": "7. Generate Derived Features", "script": "4_compute_derived_features.py"}
    ]
    
    for step in STATE_DRIVEN_STEPS:
        run_script(step["name"], step["script"])
        time.sleep(2)
        
    print("--- PIPELINE RUN COMPLETED SUCCESSFULLY ---")

if __name__ == "__main__":
    # Strict enforcement: Fail immediately if no URL is provided in the command line
    if len(sys.argv) < 2:
        print("❌ Error: You must provide a tournament URL.")
        print('Usage: python run_pipeline.py "https://gol.gg/tournament/tournament-matchlist/..."')
        sys.exit(1)
        
    target_url = sys.argv[1]
    execute_pipeline(tournament_url=target_url)