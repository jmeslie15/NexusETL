import time
import requests
from bs4 import BeautifulSoup
import psycopg2
import re
from urllib.parse import unquote

# ==========================================
# 1. DATABASE SETUP
# ==========================================
DB_CONFIG = {
    "dbname": "RiftQuant",
    "user": "postgres",         # Update this if necessary
    "password": "LeBronJames2016!", # Update this if necessary
    "host": "localhost",
    "port": "5432"
}

def setup_database():
    """Connects to PostgreSQL and ensures the drafts table exists."""
    conn = psycopg2.connect(
        dbname=DB_CONFIG["dbname"],
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"],
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"]
    )
    cursor = conn.cursor()
    
    # Create drafts table referencing the matches table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS drafts (
        draft_id SERIAL PRIMARY KEY,
        match_id BIGINT REFERENCES matches(match_id) ON DELETE CASCADE,
        side TEXT NOT NULL,          -- 'blue' or 'red'
        action_number INT NOT NULL,  -- 1 to 20 globally ordered
        phase INT NOT NULL,          -- 1 or 2
        action_type TEXT NOT NULL,   -- 'pick' or 'ban'
        champion TEXT NOT NULL,
        role_guess TEXT,             -- Reserved for future logic
        UNIQUE (match_id, action_number)
    );
    ''')
    conn.commit()
    return conn

# ==========================================
# 2. DRAFT ORDER MAPPING LOGIC
# ==========================================
# Standard professional League of Legends snake draft mapping
BLUE_FIRST_PICK_SEQUENCE = [
    # Phase 1 Bans (Actions 1-6)
    {"side": "blue", "phase": 1, "action_type": "ban"},
    {"side": "red",  "phase": 1, "action_type": "ban"},
    {"side": "blue", "phase": 1, "action_type": "ban"},
    {"side": "red",  "phase": 1, "action_type": "ban"},
    {"side": "blue", "phase": 1, "action_type": "ban"},
    {"side": "red",  "phase": 1, "action_type": "ban"},
    # Phase 1 Picks (Actions 7-12)
    {"side": "blue", "phase": 1, "action_type": "pick"},
    {"side": "red",  "phase": 1, "action_type": "pick"},
    {"side": "red",  "phase": 1, "action_type": "pick"},
    {"side": "blue", "phase": 1, "action_type": "pick"},
    {"side": "blue", "phase": 1, "action_type": "pick"},
    {"side": "red",  "phase": 1, "action_type": "pick"},
    # Phase 2 Bans (Actions 13-16)
    {"side": "red",  "phase": 2, "action_type": "ban"},
    {"side": "blue", "phase": 2, "action_type": "ban"},
    {"side": "red",  "phase": 2, "action_type": "ban"},
    {"side": "blue", "phase": 2, "action_type": "ban"},
    # Phase 2 Picks (Actions 17-20)
    {"side": "red",  "phase": 2, "action_type": "pick"},
    {"side": "blue", "phase": 2, "action_type": "pick"},
    {"side": "blue", "phase": 2, "action_type": "pick"},
    {"side": "red",  "phase": 2, "action_type": "pick"}
]

def get_draft_sequence(is_blue_first_pick):
    """Generates the absolute order array based on who gets first pick."""
    if is_blue_first_pick:
        return BLUE_FIRST_PICK_SEQUENCE
    
    # If Red has first pick, invert the sides for all steps
    red_first_sequence = []
    for action in BLUE_FIRST_PICK_SEQUENCE:
        inverted_action = action.copy()
        inverted_action["side"] = "red" if action["side"] == "blue" else "blue"
        red_first_sequence.append(inverted_action)
    return red_first_sequence

# ==========================================
# 3. INDIVIDUAL GAME DRAFT PARSING
# ==========================================
def parse_champion_names(container):
    """Finds all champion asset names within a target block."""
    champions = []
    if not container:
        return champions
    # GOL.gg stores champion names cleanly in the img alt tags within draft rows
    imgs = container.find_all('img', class_=re.compile(r'champion_icon'))
    for img in imgs:
        alt_text = img.get('alt', '').strip()
        # Avoid utility layout assets like first pick or score indicators
        if alt_text and alt_text not in ['Kills', 'Towers', 'Dragons', 'Nashor', 'Team Gold', 'First Pick', 'First Blood', 'First Tower']:
            champions.append(alt_text)
    return champions

def scrape_match_draft(game_url, conn):
    print(f"  Parsing Draft Data from: {game_url}")
    headers = {'User-Agent': 'Mozilla/5.0'}
    response = requests.get(game_url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')

    # Extract Match ID to build relational links
    match_id_search = re.search(r'/stats/(\d+)/', game_url)
    if not match_id_search:
        print(f"  --> Skip: Match ID not found in URL.")
        return
    match_id = int(match_id_search.group(1))

    # Identify blue and red blocks
    team_blocks = soup.find_all('div', class_='col-12 col-sm-6')
    if len(team_blocks) < 2:
        print(f"  --> Skip: Draft layouts not available on page structure.")
        return

    blue_block = team_blocks[0]
    red_block = team_blocks[1]

    # Verify First Pick status
    is_blue_first = blue_block.find('img', src=re.compile('first.png')) is not None
    draft_sequence = get_draft_sequence(is_blue_first)

    # Scrape lists of raw text values from the layouts
    blue_bans_row = blue_block.find('div', string=re.compile('Bans'))
    blue_picks_row = blue_block.find('div', string=re.compile('Picks'))
    red_bans_row = red_block.find('div', string=re.compile('Bans'))
    red_picks_row = red_block.find('div', string=re.compile('Picks'))

    blue_bans = parse_champion_names(blue_bans_row.find_next_sibling('div') if blue_bans_row else None)
    blue_picks = parse_champion_names(blue_picks_row.find_next_sibling('div') if blue_picks_row else None)
    red_bans = parse_champion_names(red_bans_row.find_next_sibling('div') if red_bans_row else None)
    red_picks = parse_champion_names(red_picks_row.find_next_sibling('div') if red_picks_row else None)

    # Use pointers to track positions inside separate champion arrays
    pointers = {
        "blue_ban": 0,
        "blue_pick": 0,
        "red_ban": 0,
        "red_pick": 0
    }

    # Interleave data into the relational PostgreSQL table format sequentially
    cursor = conn.cursor()
    for idx, template in enumerate(draft_sequence, start=1):
        side = template["side"]
        action_type = template["action_type"]
        phase = template["phase"]
        
        key = f"{side}_{action_type}"
        current_pointer = pointers[key]
        
        # Pull specific champion item from text arrays safely
        champion_list = blue_bans if key == "blue_ban" else blue_picks if key == "blue_pick" else red_bans if key == "red_ban" else red_picks
        
        if current_pointer < len(champion_list):
            champion_name = champion_list[current_pointer]
            pointers[key] += 1
            
            # Write row to DB
            sql = """
                INSERT INTO drafts (match_id, side, action_number, phase, action_type, champion)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (match_id, action_number)
                DO UPDATE SET side = EXCLUDED.side, phase = EXCLUDED.phase, 
                              action_type = EXCLUDED.action_type, champion = EXCLUDED.champion;
            """
            cursor.execute(sql, (match_id, side, idx, phase, action_type, champion_name))
    
    conn.commit()
    print(f"  --> Successfully saved 20 draft actions for match {match_id}")

# ==========================================
# 4. TOURNAMENT LOOP LOGIC
# ==========================================
def get_series_links_from_tournament(tournament_url):
    headers = {'User-Agent': 'Mozilla/5.0'}
    response = requests.get(tournament_url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')
    series_links = []
    for a in soup.find_all('a', href=True):
        if '/game/stats/' in a['href'] and 'page-summary' in a['href']:
            full_url = "https://gol.gg" + a['href'].replace('..', '')
            series_links.append(full_url)
    return list(dict.fromkeys(series_links))

def scrape_tournament_drafts(tournament_url, conn):
    series_links = get_series_links_from_tournament(tournament_url)
    print(f"Found {len(series_links)} series to process for draft data.\n")
    
    for series_idx, series_url in enumerate(series_links, start=1):
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(series_url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        game_links = []
        for a in soup.find_all('a', href=True):
            if 'page-game' in a['href']:
                game_links.append("https://gol.gg" + a['href'].replace('..', ''))
        
        unique_game_links = list(dict.fromkeys(game_links))
        
        # Fallback for Bo1 configurations
        if not unique_game_links:
            game_id_match = re.search(r'/stats/(\d+)/', series_url)
            if game_id_match:
                unique_game_links.append(f"https://gol.gg/game/stats/{game_id_match.group(1)}/page-game/")

        for game_url in unique_game_links:
            try:
                scrape_match_draft(game_url, conn)
                time.sleep(1) # Polite tracking delays
            except Exception as e:
                print(f"  Error processing game {game_url}: {e}")
                
        time.sleep(1.5)

# ==========================================
# 5. RUN SCRIPT
# ==========================================
if __name__ == "__main__":
    db_connection = setup_database()
    tournament_url = "https://gol.gg/tournament/tournament-matchlist/LTA%20North%202025%20Split%201/"
    
    try:
        scrape_tournament_drafts(tournament_url, db_connection)
        print("\nDraft data capture completed successfully!")
    except KeyboardInterrupt:
        print("\nProcess canceled manually.")
    finally:
        if db_connection:
            db_connection.close()