import time
import requests
from bs4 import BeautifulSoup
import psycopg2
import re
from requests.exceptions import RequestException
from http.client import IncompleteRead
import os
import sys
from dotenv import load_dotenv

# ==========================================
# 1. DATABASE SETUP
# ==========================================
load_dotenv() # Loads the variables from the .env file

DB_CONFIG = {
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASS"),
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", "5432")
}

def setup_database():
    conn = psycopg2.connect(**DB_CONFIG) # type: ignore
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS drafts (
        draft_id SERIAL PRIMARY KEY,
        match_id BIGINT REFERENCES matches(match_id) ON DELETE CASCADE,
        team_id INT REFERENCES teams(team_id) ON DELETE SET NULL,
        side TEXT NOT NULL,
        action_number INT NOT NULL,
        phase INT NOT NULL,
        action_type TEXT NOT NULL,
        champion TEXT NOT NULL,
        role_guess TEXT,
        UNIQUE (match_id, action_number)
    );
    ''')
    conn.commit()
    return conn

# ==========================================
# 2. DRAFT ORDER MAPPING LOGIC
# ==========================================
BLUE_FIRST_PICK_SEQUENCE = [
    {"side": "blue", "phase": 1, "action_type": "ban"},
    {"side": "red",  "phase": 1, "action_type": "ban"},
    {"side": "blue", "phase": 1, "action_type": "ban"},
    {"side": "red",  "phase": 1, "action_type": "ban"},
    {"side": "blue", "phase": 1, "action_type": "ban"},
    {"side": "red",  "phase": 1, "action_type": "ban"},
    {"side": "blue", "phase": 1, "action_type": "pick"},
    {"side": "red",  "phase": 1, "action_type": "pick"},
    {"side": "red",  "phase": 1, "action_type": "pick"},
    {"side": "blue", "phase": 1, "action_type": "pick"},
    {"side": "blue", "phase": 1, "action_type": "pick"},
    {"side": "red",  "phase": 1, "action_type": "pick"},
    {"side": "red",  "phase": 2, "action_type": "ban"},
    {"side": "blue", "phase": 2, "action_type": "ban"},
    {"side": "red",  "phase": 2, "action_type": "ban"},
    {"side": "blue", "phase": 2, "action_type": "ban"},
    {"side": "red",  "phase": 2, "action_type": "pick"},
    {"side": "blue", "phase": 2, "action_type": "pick"},
    {"side": "blue", "phase": 2, "action_type": "pick"},
    {"side": "red",  "phase": 2, "action_type": "pick"}
]

def get_draft_sequence(is_blue_first_pick):
    if is_blue_first_pick:
        return BLUE_FIRST_PICK_SEQUENCE
    red_first_sequence = []
    for action in BLUE_FIRST_PICK_SEQUENCE:
        inverted_action = action.copy()
        inverted_action["side"] = "red" if action["side"] == "blue" else "blue"
        red_first_sequence.append(inverted_action)
    return red_first_sequence

# ==========================================
# 3. NETWORK HELPER
# ==========================================
def fetch_with_retries(url, max_retries=3):
    headers = {'User-Agent': 'Mozilla/5.0'}
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=15) 
            return response
        except (RequestException, IncompleteRead, ConnectionError) as e:
            print(f"    [Warning] Network hiccup on attempt {attempt}/{max_retries}: {e}")
            if attempt < max_retries:
                time.sleep(2 * attempt)
            else:
                print(f"    [Failed] Skipping {url} after {max_retries} attempts.")
                raise

# ==========================================
# 4. INDIVIDUAL GAME DRAFT PARSING
# ==========================================
def parse_champion_names(container):
    champions = []
    if not container: return champions
    imgs = container.find_all('img', class_=re.compile(r'champion_icon'))
    for img in imgs:
        alt_text = img.get('alt', '').strip()
        if alt_text and alt_text not in ['Kills', 'Towers', 'Dragons', 'Nashor', 'Team Gold', 'First Pick', 'First Blood', 'First Tower']:
            champions.append(alt_text)
    return champions

def scrape_match_draft(game_url, conn):
    match_id_search = re.search(r'/stats/(\d+)/', game_url)
    if not match_id_search: return
    match_id = int(match_id_search.group(1))

    cursor = conn.cursor()
    cursor.execute("SELECT blue_team_id, red_team_id FROM matches WHERE match_id = %s;", (match_id,))
    match_data = cursor.fetchone()
    
    if not match_data:
        return
        
    blue_team_id, red_team_id = match_data

    print(f"  Parsing Draft Data from: {game_url}")
    try:
        response = fetch_with_retries(game_url)
    except Exception:
        return 
    
    soup = BeautifulSoup(response.text, 'html.parser') # type: ignore

    team_blocks = soup.find_all('div', class_='col-12 col-sm-6')
    if len(team_blocks) < 2: return

    blue_block = team_blocks[0]
    red_block = team_blocks[1]

    is_blue_first = blue_block.find('img', src=re.compile('first.png')) is not None
    draft_sequence = get_draft_sequence(is_blue_first)

    def find_draft_row(block, keyword):
        return block.find(lambda tag: tag.name == 'div' and keyword in tag.text and tag.get('class') == ['col-2'])

    blue_bans_row = find_draft_row(blue_block, 'Bans')
    blue_picks_row = find_draft_row(blue_block, 'Picks')
    red_bans_row = find_draft_row(red_block, 'Bans')
    red_picks_row = find_draft_row(red_block, 'Picks')

    blue_bans = parse_champion_names(blue_bans_row.find_next_sibling('div') if blue_bans_row else None)
    blue_picks = parse_champion_names(blue_picks_row.find_next_sibling('div') if blue_picks_row else None)
    red_bans = parse_champion_names(red_bans_row.find_next_sibling('div') if red_bans_row else None)
    red_picks = parse_champion_names(red_picks_row.find_next_sibling('div') if red_picks_row else None)

    pointers = {"blue_ban": 0, "blue_pick": 0, "red_ban": 0, "red_pick": 0}

    for idx, template in enumerate(draft_sequence, start=1):
        side = template["side"]
        action_type = template["action_type"]
        phase = template["phase"]
        key = f"{side}_{action_type}"
        current_pointer = pointers[key]
        
        team_id = blue_team_id if side == 'blue' else red_team_id
        champion_list = blue_bans if key == "blue_ban" else blue_picks if key == "blue_pick" else red_bans if key == "red_ban" else red_picks
        
        if current_pointer < len(champion_list):
            champion_name = champion_list[current_pointer]
            pointers[key] += 1
            
            sql = """
                INSERT INTO drafts (match_id, team_id, side, action_number, phase, action_type, champion)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (match_id, action_number)
                DO UPDATE SET team_id = EXCLUDED.team_id, side = EXCLUDED.side, phase = EXCLUDED.phase, 
                              action_type = EXCLUDED.action_type, champion = EXCLUDED.champion;
            """
            cursor.execute(sql, (match_id, team_id, side, idx, phase, action_type, champion_name))
    
    conn.commit()
    print(f"  --> Saved 20 draft rows for match {match_id}")

# ==========================================
# 5. STATE-DRIVEN TARGET EXTRACTION
# ==========================================
def get_missing_draft_urls_from_tournament(tournament_url, conn):
    """
    CRITICAL FIX: Uses the dynamic tournament URL parameter to look up matches, 
    but filters down to ONLY games that do not have 20 draft rows logged.
    """
    headers = {'User-Agent': 'Mozilla/5.0'}
    response = requests.get(tournament_url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # 1. Discover all unique game link IDs listed on the page
    scraped_game_ids = []
    for a in soup.find_all('a', href=True):
        if '/game/stats/' in a['href']:
            match_id_search = re.search(r'/stats/(\d+)/', a['href'])
            if match_id_search:
                scraped_game_ids.append(int(match_id_search.group(1)))
                
    scraped_game_ids = list(set(scraped_game_ids))
    if not scraped_game_ids:
        return []

    # 2. State Cross-Check: See which of these IDs have incomplete draft representations
    cursor = conn.cursor()
    cursor.execute("""
        SELECT m.match_id, m.gol_match_url 
        FROM matches m 
        LEFT JOIN drafts d ON m.match_id = d.match_id
        WHERE m.match_id IN %s
        GROUP BY m.match_id, m.gol_match_url
        HAVING COUNT(d.draft_id) < 20;
    """, (tuple(scraped_game_ids),))
    
    return cursor.fetchall()

def scrape_tournament_drafts(tournament_url, conn):
    # Fetching missing targets based purely on the active parameter
    missing_games = get_missing_draft_urls_from_tournament(tournament_url, conn)
    
    if not missing_games:
        print("✅ State Check: All games for this tournament have complete draft profiles. Skipping scrape.")
        return
        
    print(f"Found {len(missing_games)} specific game maps missing complete pick/ban data.\n")
    
    for match_id, raw_url in missing_games:
        # Re-construct clean structure targeting individual maps directly
        game_url = f"https://gol.gg/game/stats/{match_id}/page-game/"
        try:
            scrape_match_draft(game_url, conn)
            time.sleep(1.5)
        except Exception as e:
            print(f"  Error processing match {match_id}: {e}")

# ==========================================
# 6. RUN SCRIPT
# ==========================================
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("❌ Error: No tournament URL provided to drafts_webscraper.py.")
        sys.exit(1)
        
    # CRITICAL FIX: Clean out any literal wrapped quotes passed down by the orchestrator
    target_url = sys.argv[1].strip("'\"")
    
    db_connection = setup_database()
    try:
        scrape_tournament_drafts(target_url, db_connection)
    finally:
        if db_connection:
            db_connection.close()