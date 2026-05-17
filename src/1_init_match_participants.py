import os
import time, requests, psycopg2, re
from bs4 import BeautifulSoup
from requests.exceptions import RequestException
from dotenv import load_dotenv

load_dotenv() # Loads the variables from the .env file

DB_CONFIG = {
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASS"),
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", "5432")
}

ROLE_ORDER = ['TOP', 'JUNGLE', 'MID', 'ADC', 'SUPPORT']

def setup_participants_table(conn):
    """Creates the match_participants table and optimized indexes if they don't exist."""
    cursor = conn.cursor()
    
    # 1. Generate the table structure with strict constraints
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS match_participants (
        participant_id SERIAL PRIMARY KEY,
        match_id BIGINT REFERENCES matches(match_id) ON DELETE CASCADE,
        player_id INT REFERENCES players(player_id) ON DELETE CASCADE,
        team_id INT REFERENCES teams(team_id) ON DELETE CASCADE,
        side TEXT NOT NULL CHECK (side IN ('blue', 'red')),
        role TEXT NOT NULL CHECK (role IN ('TOP', 'JUNGLE', 'MID', 'ADC', 'SUPPORT')),
        champion TEXT DEFAULT 'Unknown',
        
        -- Core Metrics
        kills INT DEFAULT 0,
        deaths INT DEFAULT 0,
        assists INT DEFAULT 0,
        cs INT DEFAULT 0,
        gold INT DEFAULT 0,
        damage INT DEFAULT 0,
        wards_placed INT DEFAULT 0,
        wards_killed INT DEFAULT 0,
        
        -- Advanced Features
        kill_participation FLOAT DEFAULT 0.0,
        gold_share FLOAT DEFAULT 0.0,
        damage_share FLOAT DEFAULT 0.0,
        cs_per_min FLOAT DEFAULT 0.0,
        gold_per_min FLOAT DEFAULT 0.0,
        damage_per_min FLOAT DEFAULT 0.0,
        
        -- Enforce identity uniqueness per match map
        CONSTRAINT unique_match_player UNIQUE (match_id, player_id)
    );
    ''')
    
    # 2. Add highly strategic indexes to optimize rolling feature calculations (Script 7 performance)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_participants_match ON match_participants(match_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_participants_player ON match_participants(player_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_participants_team ON match_participants(team_id);")
    
    conn.commit()

def fetch_with_retries(url, max_retries=3):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 429:
                time.sleep(5 * attempt)
                continue
            return response
        except Exception:
            time.sleep(3 * attempt)
    return None

def init_participants():
    conn = psycopg2.connect(**DB_CONFIG) # type: ignore
    
    # Automate table generation before querying or inserting
    setup_participants_table(conn)
    
    cursor = conn.cursor()
    
    # Target matches that don't have 10 identities logged yet
    cursor.execute("""
        SELECT m.match_id, m.gol_match_url, m.blue_team_id, m.red_team_id 
        FROM matches m LEFT JOIN match_participants mp ON m.match_id = mp.match_id
        GROUP BY m.match_id, m.gol_match_url, m.blue_team_id, m.red_team_id
        HAVING COUNT(mp.participant_id) < 10;
    """)
    matches = cursor.fetchall()
    
    for match_id, game_url, blue_team_id, red_team_id in matches:
        print(f"Initializing identities for Match {match_id}...")
        resp = fetch_with_retries(game_url)
        if not resp: continue
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        player_links = soup.find_all('a', class_='link-blanc', href=re.compile(r'/players/player-stats/'))
        
        if len(player_links) < 10:
            continue
            
        for row_idx, p_link in enumerate(player_links[:10]):
            player_name = p_link.text.strip()
            champ_img = p_link.find_parent('td').find('img', class_=re.compile('champion_icon'))
            champion = champ_img.get('alt', 'Unknown') if champ_img else 'Unknown'
            
            side = 'blue' if row_idx < 5 else 'red'
            team_id = blue_team_id if side == 'blue' else red_team_id
            role = ROLE_ORDER[row_idx % 5]
            
            cursor.execute("SELECT player_id FROM players WHERE player_name ILIKE %s", (player_name,))
            pid_row = cursor.fetchone()
            player_id = pid_row[0] if pid_row else None
            
            sql = """
                INSERT INTO match_participants (match_id, player_id, team_id, side, role, champion)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (match_id, player_id) DO NOTHING;
            """
            cursor.execute(sql, (match_id, player_id, team_id, side, role, champion))
            
        conn.commit()
        time.sleep(1.5)
        
    conn.close()

if __name__ == "__main__":
    init_participants()