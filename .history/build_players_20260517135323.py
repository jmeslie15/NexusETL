import time
import requests
from bs4 import BeautifulSoup
import psycopg2
import re
from requests.exceptions import RequestException
from http.client import IncompleteRead

# ==========================================
# 1. DATABASE SETUP
# ==========================================
DB_CONFIG = {
    "dbname": "RiftQuant",
    "user": "postgres",     # Update this
    "password": "LeBronJames2016!", # Update this
    "host": "localhost",
    "port": "5432"
}

def setup_players_table(conn):
    """Creates the players table linking back to the teams table."""
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS players (
        player_id SERIAL PRIMARY KEY,
        player_name TEXT UNIQUE NOT NULL,
        current_team_id INT REFERENCES teams(team_id) ON DELETE SET NULL,
        role TEXT,
        gol_player_url TEXT
    );
    ''')
    conn.commit()

# ==========================================
# 2. NETWORK HELPER
# ==========================================
def fetch_with_retries(url, max_retries=3):
    """Safely fetches a URL and automatically retries if the server drops the connection."""
    headers = {'User-Agent': 'Mozilla/5.0'}
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=10)
            return response
        except (RequestException, IncompleteRead, ConnectionError) as e:
            print(f"    [Warning] Network hiccup on attempt {attempt}/{max_retries}: {e}")
            if attempt < max_retries:
                time.sleep(2 * attempt)
            else:
                print(f"    [Failed] Skipping {url} after {max_retries} attempts.")
                raise

# ==========================================
# 3. SCRAPING LOGIC
# ==========================================
def get_teams_from_db(conn):
    """Fetches all teams that have a valid gol.gg URL."""
    cursor = conn.cursor()
    cursor.execute("SELECT team_id, team_name, gol_team_url FROM teams WHERE gol_team_url IS NOT NULL;")
    return cursor.fetchall()

def scrape_team_players(team_id, team_name, team_url, conn):
    print(f"Scraping players for {team_name}...")
    try:
        response = fetch_with_retries(team_url)
    except Exception:
        return

    soup = BeautifulSoup(response.text, 'html.parser') # type: ignore
    cursor = conn.cursor()

    # Find all table rows that contain a player link
    player_links = soup.find_all('a', href=re.compile(r'/players/player-stats/'))
    
    # Keep track of players processed to avoid duplicates if a player is listed multiple times
    processed_players = set()

    for a_tag in player_links:
        # Navigate up to the parent <tr> to get the full row context
        row = a_tag.find_parent('tr')
        if not row:
            continue

        player_name = a_tag.text.strip()
        if not player_name or player_name in processed_players:
            continue

        processed_players.add(player_name)

        # 1. Extract Role (from the first <td>)
        # Using regex to clean out hidden spaces/newlines and just grab the alpha characters (e.g., "TOP", "MID")
        role_td = row.find('td')
        raw_role = role_td.text.strip() if role_td else "UNKNOWN"
        clean_role = re.sub(r'[^a-zA-Z]', '', raw_role).upper()

        # 2. Extract internal gol.gg Player ID to build a clean URL
        raw_href = a_tag['href']
        id_match = re.search(r'/player-stats/(\d+)/', raw_href)
        
        gol_player_url = None
        if id_match:
            gol_internal_id = id_match.group(1)
            # Build a clean, filter-free global player URL
            gol_player_url = f"https://gol.gg/players/player-stats/{gol_internal_id}/season-ALL/split-ALL/tournament-ALL/"

        # 3. Insert or Update Database
        sql = """
            INSERT INTO players (player_name, current_team_id, role, gol_player_url)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (player_name) 
            DO UPDATE SET 
                current_team_id = EXCLUDED.current_team_id, 
                role = EXCLUDED.role, 
                gol_player_url = EXCLUDED.gol_player_url;
        """
        cursor.execute(sql, (player_name, team_id, clean_role, gol_player_url))
        print(f"  -> Saved {player_name} ({clean_role})")

    conn.commit()

# ==========================================
# 4. MAIN EXECUTION
# ==========================================
def build_players_database():
    conn = psycopg2.connect(**DB_CONFIG) # type: ignore
    setup_players_table(conn)
    
    teams = get_teams_from_db(conn)
    print(f"Found {len(teams)} teams to scrape for rosters.\n")
    
    for team_id, team_name, gol_team_url in teams:
        scrape_team_players(team_id, team_name, gol_team_url, conn)
        time.sleep(1.5) # Polite scraping delay

    conn.close()
    print("\nPlayer database build complete!")

if __name__ == "__main__":
    build_players_database()