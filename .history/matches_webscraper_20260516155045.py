import requests
from bs4 import BeautifulSoup
import psycopg2
import re
from datetime import datetime

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

def setup_database():
    conn = psycopg2.connect(
        dbname=DB_CONFIG["dbname"],
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"],
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"]
    )
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS matches (
        match_id BIGINT PRIMARY KEY,
        tournament TEXT,
        region TEXT,
        stage TEXT,
        game_date DATE,
        patch_version TEXT,
        blue_team TEXT,
        red_team TEXT,
        winner_side TEXT,
        game_number INT,
        best_of INT,
        duration_seconds INT,
        blue_kills INT,
        red_kills INT,
        total_kills INT,
        blue_gold INT,
        red_gold INT,
        blue_towers INT,
        red_towers INT,
        blue_dragons INT,
        red_dragons INT,
        blue_barons INT,
        red_barons INT,
        gol_match_url TEXT
    );
    ''')
    conn.commit()
    return conn

# ==========================================
# 2. HELPER FUNCTIONS
# ==========================================
def parse_duration(duration_str):
    if not duration_str: return 0
    parts = duration_str.strip().split(':')
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    return 0

def extract_number(text):
    if not text: return 0
    match = re.search(r'\d+', text)
    return int(match.group()) if match else 0

def extract_gold(text):
    if not text: return 0
    match = re.search(r'(\d+\.\d+)k', text)
    return int(float(match.group(1)) * 1000) if match else 0

# ==========================================
# 3. CORE SCRAPING LOGIC
# ==========================================
def scrape_individual_game(game_url, game_number, conn):
    print(f"Scraping Game URL: {game_url}")
    headers = {'User-Agent': 'Mozilla/5.0'}
    response = requests.get(game_url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')

    data = {
        'gol_match_url': game_url,
        'game_number': game_number,
        'best_of': 3 # Default assumption, can be made dynamic later
    }
    
    try:
        # Extract Match ID
        match_id_search = re.search(r'/stats/(\d+)/', game_url)
        data['match_id'] = int(match_id_search.group(1)) if match_id_search else None
        if not data['match_id']:
            return

        # 1. Contextual Info (Date, Stage, Tournament)
        # Pulls from format: "LTA North 2025 Split 3 (NA) - Fearless Draft 2025-07-26 (WEEK1)"
        date_block = soup.find('div', class_='col-12 col-sm-5 text-right')
        if date_block:
            date_text = date_block.text.strip()
            # Extracts "2025-07-26"
            date_match = re.search(r'\d{4}-\d{2}-\d{2}', date_text)
            if date_match: data['game_date'] = date_match.group(0)
            
            # Extracts "(WEEK1)"
            stage_match = re.search(r'\((.*?)\)', date_text)
            if stage_match: data['stage'] = stage_match.group(1)

        # Gets tournament & patch
        h1_tags = soup.find_all('h1')
        if len(h1_tags) > 0:
            # First H1 is usually the header
            data['tournament'] = "LTA North 2025 Split 3" # Modify dynamically if needed
            data['region'] = "NA"
        
        patch_elem = soup.find(string=re.compile(r'v\d+\.\d+'))
        if patch_elem: data['patch_version'] = patch_elem.strip()

        # 2. Extract Winner and Match Duration
        winner_tag = soup.find('h1', string=re.compile(r'WIN|LOSS'))
        if winner_tag and "WIN" in winner_tag.text:
            data['winner_side'] = "Blue"
        else:
            data['winner_side'] = "Red"

        time_elem = soup.find('h1', string=re.compile(r'\d{2}:\d{2}'))
        data['duration_seconds'] = parse_duration(time_elem.text) if time_elem else 0

        # 3. Teams and Objectives Data Parsing
        # The page breaks down teams into two distinct chunks (top chunk = blue, bottom chunk = red)
        team_blocks = soup.find_all('div', class_='col-12 col-sm-6')
        
        if len(team_blocks) >= 2:
            blue_team_block = team_blocks[0]
            red_team_block = team_blocks[1]
            
            # Helper to parse stats from a specific team's HTML block
            def parse_team_stats(team_html):
                stats = {
                    'name': 'Unknown', 'kills': 0, 'towers': 0, 'dragons': 0, 'barons': 0, 'gold': 0
                }
                
                # Get Team Name (found in anchor tag usually displaying Team - WIN/LOSS)
                a_tags = team_html.find_all('a')
                for a in a_tags:
                    if '/teams/' in a['href'] and a.text.strip():
                        stats['name'] = a.text.strip().replace(" - WIN", "").replace(" - LOSS", "")
                        break
                
                # Get specific numerical stats by searching for their associated icons
                kills_icon = team_html.find('img', src=re.compile('ic_kills'))
                if kills_icon and kills_icon.parent:
                    stats['kills'] = extract_number(kills_icon.parent.text)

                tours_icon = team_html.find('img', src=re.compile('ic_tours'))
                if tours_icon and tours_icon.parent:
                    stats['towers'] = extract_number(tours_icon.parent.text)

                dragons_icon = team_html.find('img', src=re.compile('ic_dragons'))
                if dragons_icon and dragons_icon.parent:
                    stats['dragons'] = extract_number(dragons_icon.parent.text)

                barons_icon = team_html.find('img', src=re.compile('ic_barons'))
                if barons_icon and barons_icon.parent:
                    stats['barons'] = extract_number(barons_icon.parent.text)
                    
                golds_icon = team_html.find('img', src=re.compile('ic_golds'))
                if golds_icon and golds_icon.parent:
                    stats['gold'] = extract_gold(golds_icon.parent.text)

                return stats

            # Apply parsing helper
            blue_stats = parse_team_stats(blue_team_block)
            red_stats = parse_team_stats(red_team_block)

            # Map stats to the final data dictionary
            data['blue_team'] = blue_stats['name']
            data['red_team'] = red_stats['name']
            
            data['blue_kills'] = blue_stats['kills']
            data['red_kills'] = red_stats['kills']
            data['total_kills'] = data['blue_kills'] + data['red_kills']
            
            data['blue_towers'] = blue_stats['towers']
            data['red_towers'] = red_stats['towers']
            
            data['blue_dragons'] = blue_stats['dragons']
            data['red_dragons'] = red_stats['dragons']
            
            data['blue_barons'] = blue_stats['barons']
            data['red_barons'] = red_stats['barons']
            
            data['blue_gold'] = blue_stats['gold']
            data['red_gold'] = red_stats['gold']

        insert_match_data(conn, data)
        
    except Exception as e:
        print(f"Error parsing {game_url}: {e}")

# ==========================================
# 4. DATABASE INSERT
# ==========================================
def insert_match_data(conn, data):
    cursor = conn.cursor()
    columns = [
        'match_id', 'tournament', 'region', 'stage', 'game_date', 'patch_version',
        'blue_team', 'red_team', 'winner_side', 'game_number', 'best_of',
        'duration_seconds', 'blue_kills', 'red_kills', 'total_kills',
        'blue_gold', 'red_gold', 'blue_towers', 'red_towers', 'blue_dragons',
        'red_dragons', 'blue_barons', 'red_barons', 'gol_match_url'
    ]
    
    values = tuple(data.get(col, None) for col in columns)
    placeholders = ', '.join(['%s'] * len(columns))
    update_clause = ', '.join([f"{col} = EXCLUDED.{col}" for col in columns if col != 'match_id'])
    
    sql = f"""
        INSERT INTO matches ({', '.join(columns)}) 
        VALUES ({placeholders})
        ON CONFLICT (match_id) 
        DO UPDATE SET {update_clause};
    """
    
    cursor.execute(sql, values)
    conn.commit()
    print(f"--> Successfully inserted/updated match {data.get('match_id')}")

# ==========================================
# 5. MAIN EXECUTION
# ==========================================
if __name__ == "__main__":
    db_connection = setup_database()
    
    # We use a direct game url to test the parsing logic
    test_urls = [
        "https://gol.gg/game/stats/69525/page-game/",
        "https://gol.gg/game/stats/69526/page-game/" 
    ]
    
    for idx, url in enumerate(test_urls, start=1):
        scrape_individual_game(url, game_number=idx, conn=db_connection)
            
    db_connection.close()