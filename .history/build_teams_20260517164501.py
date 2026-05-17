import psycopg2
import requests
from bs4 import BeautifulSoup
import re
import time

# ==========================================
# 1. DATABASE SETUP
# ==========================================
DB_CONFIG = {
    "dbname": "RiftQuant",
    "user": "postgres",
    "password": "LeBronJames2016!",
    "host": "localhost",
    "port": "5432"
}

def setup_teams_table(conn):
    """Creates the teams table if it doesn't exist."""
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS teams (
        team_id SERIAL PRIMARY KEY,
        team_name TEXT UNIQUE NOT NULL,
        region TEXT,
        gol_team_url TEXT
    );
    ''')
    conn.commit()

# ==========================================
# 2. DATA EXTRACTION LOGIC
# ==========================================
def get_unique_teams_from_matches(conn):
    """
    Finds all unique teams across blue and red sides, their region, 
    and exactly ONE match URL to use as a scraping reference.
    """
    cursor = conn.cursor()
    query = """
    WITH CombinedTeams AS (
        SELECT blue_team AS team_name, region, MAX(gol_match_url) AS sample_match
        FROM matches
        GROUP BY blue_team, region
        UNION
        SELECT red_team AS team_name, region, MAX(gol_match_url) AS sample_match
        FROM matches
        GROUP BY red_team, region
    )
    SELECT team_name, region, MAX(sample_match) 
    FROM CombinedTeams 
    GROUP BY team_name, region;
    """
    cursor.execute(query)
    return cursor.fetchall()

def scrape_team_url(team_name, sample_match_url):
    """Visits a match page to extract the clean, generic team URL."""
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(sample_match_url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        target_title = re.compile(f"^{re.escape(team_name)} stats$", re.IGNORECASE)
        team_link = soup.find('a', title=target_title)
        
        if team_link and 'href' in team_link.attrs:
            raw_href = team_link['href']
            id_match = re.search(r'/team-stats/(\d+)/', raw_href)
            if id_match:
                team_id = id_match.group(1)
                clean_url = f"https://gol.gg/teams/team-stats/{team_id}/split-ALL/tournament-ALL/"
                return clean_url
                
    except Exception as e:
        print(f"  [Error] Failed to fetch URL for {team_name}: {e}")
        
    return None

# ==========================================
# NEW FEATURE: RELATIONAL TEAM ID BACKFILL
# ==========================================
def backfill_relational_team_ids(conn):
    """Maps the text team names across matches and participants tables to their true team_ids."""
    print("\nRunning relational mapping backfill for newly added teams...")
    cursor = conn.cursor()

    # 1. Fix the parent matches table mapping string names to real IDs
    print("  -> Syncing team IDs inside the 'matches' table...")
    update_matches_sql = """
        UPDATE matches m
        SET 
            blue_team_id = t_blue.team_id,
            red_team_id = t_red.team_id
        FROM teams t_blue, teams t_red
        WHERE m.blue_team = t_blue.team_name 
          AND m.red_team = t_red.team_name
          AND (m.blue_team_id IS NULL OR m.red_team_id IS NULL);
    """
    cursor.execute(update_matches_sql)
    matches_affected = cursor.rowcount
    print(f"     Successfully backfilled IDs for {matches_affected} matches.")

    # 2. Fix the child participant table map relative to their side
    print("  -> Syncing team IDs inside the 'match_participants' table...")
    update_participants_sql = """
        UPDATE match_participants mp
        SET team_id = CASE 
            WHEN mp.side = 'blue' THEN m.blue_team_id 
            WHEN mp.side = 'red' THEN m.red_team_id 
        END
        FROM matches m
        WHERE mp.match_id = m.match_id 
          AND mp.team_id IS NULL;
    """
    cursor.execute(update_participants_sql)
    participants_affected = cursor.rowcount
    print(f"     Successfully backfilled IDs for {participants_affected} participant slots.")
    
    conn.commit()

# ==========================================
# 3. MAIN EXECUTION
# ==========================================
def build_teams_database():
    conn = psycopg2.connect(**DB_CONFIG) # type: ignore
    setup_teams_table(conn)
    
    print("Fetching unique teams from the matches table...")
    teams_data = get_unique_teams_from_matches(conn)
    print(f"Found {len(teams_data)} unique teams. Commencing URL extraction...\n")
    
    cursor = conn.cursor()
    
    for team_name, region, sample_match_url in teams_data:
        print(f"Processing: {team_name} ({region})")
        
        cursor.execute("SELECT gol_team_url FROM teams WHERE team_name = %s;", (team_name,))
        existing_row = cursor.fetchone()
        
        if existing_row and existing_row[0]:
            print(f"  -> Already exists in DB. Skipping.")
            continue
            
        gol_team_url = scrape_team_url(team_name, sample_match_url)
        
        if gol_team_url:
            sql = """
                INSERT INTO teams (team_name, region, gol_team_url)
                VALUES (%s, %s, %s)
                ON CONFLICT (team_name) 
                DO UPDATE SET region = EXCLUDED.region, gol_team_url = EXCLUDED.gol_team_url;
            """
            cursor.execute(sql, (team_name, region, gol_team_url))
            conn.commit()
            print(f"  -> Saved URL: {gol_team_url}")
        else:
            print(f"  -> Could not find URL on reference match page.")
            
        time.sleep(1)

    # Trigger the downstream mapping logic immediately following the crawl
    backfill_relational_team_ids(conn)

    conn.close()
    print("\nTeam database build complete!")

if __name__ == "__main__":
    build_teams_database()