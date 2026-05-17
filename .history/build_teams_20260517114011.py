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
        
        # Look for the anchor tag whose title is "{TeamName} stats"
        # Using regex to ensure case-insensitivity or slight spacing differences
        target_title = re.compile(f"^{re.escape(team_name)} stats$", re.IGNORECASE)
        team_link = soup.find('a', title=target_title)
        
        if team_link and 'href' in team_link.attrs:
            raw_href = team_link['href']
            # Extract just the numeric ID (e.g., 2542) from the URL
            id_match = re.search(r'/team-stats/(\d+)/', raw_href)
            if id_match:
                team_id = id_match.group(1)
                # Build a clean, global URL without the specific tournament filters
                clean_url = f"https://gol.gg/teams/team-stats/{team_id}/split-ALL/tournament-ALL/"
                return clean_url
                
    except Exception as e:
        print(f"  [Error] Failed to fetch URL for {team_name}: {e}")
        
    return None

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
        
        # Check if we already have this team fully populated to avoid redundant scraping
        cursor.execute("SELECT gol_team_url FROM teams WHERE team_name = %s;", (team_name,))
        existing_row = cursor.fetchone()
        
        if existing_row and existing_row[0]:
            print(f"  -> Already exists in DB. Skipping.")
            continue
            
        gol_team_url = scrape_team_url(team_name, sample_match_url)
        
        if gol_team_url:
            # Insert or update the team
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
            
        time.sleep(1) # Polite scraping delay

    conn.close()
    print("\nTeam database build complete!")

if __name__ == "__main__":
    build_teams_database()