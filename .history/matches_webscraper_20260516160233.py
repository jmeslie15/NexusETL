import time
import requests
from bs4 import BeautifulSoup
import psycopg2
import re

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
# 3. INDIVIDUAL GAME SCRAPING LOGIC
# ==========================================
def scrape_individual_game(game_url, game_number, conn):
    print(f"  Scraping Game URL: {game_url}")
    headers = {'User-Agent': 'Mozilla/5.0'}
    response = requests.get(game_url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')

    data = {
        'gol_match_url': game_url,
        'game_number': game_number,
        'best_of': 3 # Default assumption
    }
    
    try:
        # Extract Match ID
        match_id_search = re.search(r'/stats/(\d+)/', game_url)
        data['match_id'] = int(match_id_search.group(1)) if match_id_search else None
        if not data['match_id']:
            return

        # Contextual Info (Date, Stage)
        date_block = soup.find('div', class_='col-12 col-sm-5 text-right')
        if date_block:
            date_text = date_block.text.strip()
            date_match = re.search(r'\d{4}-\d{2}-\d{2}', date_text)
            if date_match: data['game_date'] = date_match.group(0)
            
            stage_match = re.search(r'\((.*?)\)', date_text)
            if stage_match: data['stage'] = stage_match.group(1)

        # Tournament & patch
        h1_tags = soup.find_all('h1')
        if len(h1_tags) > 0:
            data['tournament'] = "LTA North 2025 Split 3" 
            data['region'] = "NA"
        
        patch_elem = soup.find(string=re.compile(r'v\d+\.\d+'))
        if patch_elem: data['patch_version'] = patch_elem.strip()

        # Extract Winner and Match Duration
        winner_tag = soup.find('h1', string=re.compile(r'WIN|LOSS'))
        if winner_tag and "WIN" in winner_tag.text:
            data['winner_side'] = "Blue"
        else:
            data['winner_side'] = "Red"

        time_elem = soup.find('h1', string=re.compile(r'\d{2}:\d{2}'))
        data['duration_seconds'] = parse_duration(time_elem.text) if time_elem else 0

        # Teams and Objectives Data Parsing
        team_blocks = soup.find_all('div', class_='col-12 col-sm-6')
        
        if len(team_blocks) >= 2:
            blue_team_block = team_blocks[0]
            red_team_block = team_blocks[1]
            
            def parse_team_stats(team_html):
                stats = {'name': 'Unknown', 'kills': 0, 'towers': 0, 'dragons': 0, 'barons': 0, 'gold': 0}
                
                a_tags = team_html.find_all('a')
                for a in a_tags:
                    if '/teams/' in a['href'] and a.text.strip():
                        stats['name'] = a.text.strip().replace(" - WIN", "").replace(" - LOSS", "")
                        break
                
                kills_icon = team_html.find('img', src=re.compile('ic_kills'))
                if kills_icon and kills_icon.parent: stats['kills'] = extract_number(kills_icon.parent.text)

                tours_icon = team_html.find('img', src=re.compile('ic_tours'))
                if tours_icon and tours_icon.parent: stats['towers'] = extract_number(tours_icon.parent.text)

                dragons_icon = team_html.find('img', src=re.compile('ic_dragons'))
                if dragons_icon and dragons_icon.parent: stats['dragons'] = extract_number(dragons_icon.parent.text)

                barons_icon = team_html.find('img', src=re.compile('ic_barons'))
                if barons_icon and barons_icon.parent: stats['barons'] = extract_number(barons_icon.parent.text)
                    
                golds_icon = team_html.find('img', src=re.compile('ic_golds'))
                if golds_icon and golds_icon.parent: stats['gold'] = extract_gold(golds_icon.parent.text)

                return stats

            blue_stats = parse_team_stats(blue_team_block)
            red_stats = parse_team_stats(red_team_block)

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
    print(f"  --> Successfully inserted/updated match {data.get('match_id')}")

# ==========================================
# 5. TOURNAMENT SCRAPING LOGIC
# ==========================================
def get_series_links_from_tournament(tournament_url):
    print(f"Fetching tournament page: {tournament_url}")
    headers = {'User-Agent': 'Mozilla/5.0'}
    response = requests.get(tournament_url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    series_links = []
    for a in soup.find_all('a', href=True):
        if '/game/stats/' in a['href'] and 'page-summary' in a['href']:
            full_url = "https://gol.gg" + a['href'].replace('..', '')
            series_links.append(full_url)
            
    return list(dict.fromkeys(series_links))

def scrape_tournament(tournament_url, conn):
    series_links = get_series_links_from_tournament(tournament_url)
    print(f"Found {len(series_links)} series in the tournament.")
    
    for series_idx, series_url in enumerate(series_links, start=1):
        print(f"\n--- Processing Series {series_idx}/{len(series_links)}: {series_url} ---")
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(series_url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        game_links = []
        for a in soup.find_all('a', href=True):
            if 'page-game' in a['href']:
                full_game_url = "https://gol.gg" + a['href'].replace('..', '')
                game_links.append(full_game_url)
        
        unique_game_links = list(dict.fromkeys(game_links))
        
        if not unique_game_links:
            game_id_match = re.search(r'/stats/(\d+)/', series_url)
            if game_id_match:
                fallback_url = f"https://gol.gg/game/stats/{game_id_match.group(1)}/page-game/"
                unique_game_links.append(fallback_url)

        print(f"Found {len(unique_game_links)} games in this series.")
        
        for game_number, game_url in enumerate(unique_game_links, start=1):
            scrape_individual_game(game_url, game_number, conn)
            time.sleep(1) 
            
        time.sleep(2) 

# ==========================================
# 6. MAIN EXECUTION
# ==========================================
if __name__ == "__main__":
    db_connection = setup_database()
    tournament_url = "https://gol.gg/tournament/tournament-matchlist/LTA%20North%202025%20Split%202%20Playoffs/"
    
    try:
        scrape_tournament(tournament_url, db_connection)
        print("\nTournament scraping completed successfully!")
    except KeyboardInterrupt:
        print("\nScraping interrupted by user. Safely closing database connection...")
    finally:
        if db_connection:
            db_connection.close()