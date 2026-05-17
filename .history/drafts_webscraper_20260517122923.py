import time
import requests
from bs4 import BeautifulSoup
import psycopg2
import re
from requests.exceptions import RequestException
from http.client import IncompleteRead

def fetch_with_retries(url, max_retries=3):
    """Safely fetches a URL and automatically retries if the server drops the connection."""
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    for attempt in range(1, max_retries + 1):
        try:
            # Added a 10-second timeout so it doesn't hang forever
            response = requests.get(url, headers=headers, timeout=15) 
            return response
        except (RequestException, IncompleteRead, ConnectionError) as e:
            print(f"    [Warning] Network hiccup on attempt {attempt}/{max_retries}: {e}")
            if attempt < max_retries:
                time.sleep(2 * attempt) # Wait 2s, then 4s, etc. before trying again
            else:
                print(f"    [Failed] Skipping {url} after {max_retries} attempts.")
                raise # Pass the error up if it completely fails

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

def setup_database():
    conn = psycopg2.connect(**DB_CONFIG) # type: ignore
    cursor = conn.cursor()
    
    # Updated to include team_id
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
# 3. INDIVIDUAL GAME DRAFT PARSING
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
    
    # Fetch blue and red team IDs from the matches table
    cursor.execute("SELECT blue_team_id, red_team_id FROM matches WHERE match_id = %s;", (match_id,))
    match_data = cursor.fetchone()
    
    if not match_data:
        print(f"  [Skipped] Match ID {match_id} missing from matches table. Run the primary scraper first.")
        return
        
    blue_team_id, red_team_id = match_data

    print(f"  Parsing Draft Data from: {game_url}")
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = fetch_with_retries(game_url)
    except Exception:
        return # Skip this match entirely if all 3 retries fail
    
    soup = BeautifulSoup(response.text, 'html.parser') # type: ignore

    team_blocks = soup.find_all('div', class_='col-12 col-sm-6')
    if len(team_blocks) < 2: return

    blue_block = team_blocks[0]
    red_block = team_blocks[1]

    is_blue_first = blue_block.find('img', src=re.compile('first.png')) is not None
    draft_sequence = get_draft_sequence(is_blue_first)

    # --- THE FIX: Safer targeting logic using .text ---
    def find_draft_row(block, keyword):
        return block.find(lambda tag: tag.name == 'div' and keyword in tag.text and tag.get('class') == ['col-2'])

    blue_bans_row = find_draft_row(blue_block, 'Bans')
    blue_picks_row = find_draft_row(blue_block, 'Picks')
    red_bans_row = find_draft_row(red_block, 'Bans')
    red_picks_row = find_draft_row(red_block, 'Picks')
    # --------------------------------------------------

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
# 4. TOURNAMENT LOOP LOGIC
# ==========================================
def get_series_links_from_tournament(tournament_url):
    headers = {'User-Agent': 'Mozilla/5.0'}
    response = requests.get(tournament_url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')
    series_links = []
    for a in soup.find_all('a', href=True):
        if '/game/stats/' in a['href'] and ('page-summary' in a['href'] or 'page-game' in a['href']):
            clean_href = a['href'].replace('..', '').replace('page-game', 'page-summary')
            series_links.append("https://gol.gg" + clean_href)
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
        if not unique_game_links:
            game_id_match = re.search(r'/stats/(\d+)/', series_url)
            if game_id_match:
                unique_game_links.append(f"https://gol.gg/game/stats/{game_id_match.group(1)}/page-game/")

        for game_url in unique_game_links:
            try:
                scrape_match_draft(game_url, conn)
                time.sleep(1)
            except Exception as e:
                print(f"  Error processing game {game_url}: {e}")
        time.sleep(1.5)

# ==========================================
# 5. RUN SCRIPT
# ==========================================
if __name__ == "__main__":
    db_connection = setup_database()
    tournament_url = "https://gol.gg/tournament/tournament-matchlist/LTA%20North%202025%20Split%203/"
    
    try:
        scrape_tournament_drafts(tournament_url, db_connection)
    finally:
        if db_connection:
            db_connection.close()