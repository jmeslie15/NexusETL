import requests
from bs4 import BeautifulSoup
import psycopg2
import re
from datetime import datetime

# ==========================================
# 1. DATABASE SETUP
# ==========================================
# Update these variables with your PostgreSQL credentials
DB_CONFIG = {
    "dbname": "RiftQuant",
    "user": "postgres",
    "password": "LeBronJames2016!",
    "host": "localhost",
    "port": "5432"
}

def setup_database():
    """Creates the PostgreSQL database connection and matches table."""
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    
    # Using the exact schema provided
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
    match = re.search(r'\d+', text)
    return int(match.group()) if match else 0

# ==========================================
# 3. CORE SCRAPING LOGIC
# ==========================================
def get_match_links_from_tournament(tournament_url):
    headers = {'User-Agent': 'Mozilla/5.0'}
    response = requests.get(tournament_url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    match_links = []
    for a in soup.find_all('a', href=True):
        if '/game/stats/' in a['href'] and 'page-summary' in a['href']:
            full_url = "https://gol.gg" + a['href'].replace('..', '')
            match_links.append(full_url)
            
    return list(set(match_links))

def scrape_individual_game(game_url, game_number, conn):
    print(f"Scraping Game URL: {game_url}")
    headers = {'User-Agent': 'Mozilla/5.0'}
    response = requests.get(game_url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')

    data = {}
    data['gol_match_url'] = game_url
    data['game_number'] = game_number
    
    try:
        # Extract Match ID
        match_id_search = re.search(r'/stats/(\d+)/', game_url)
        data['match_id'] = int(match_id_search.group(1)) if match_id_search else None
        
        # 1. Contextual Info
        data['tournament'] = "LTA North 2025" 
        data['game_date'] = "2025-09-07"
        data['patch_version'] = "15.1"
            
        # 2. Teams and Game Time
        teams = soup.find_all('h1')
        if len(teams) >= 2:
            data['blue_team'] = teams[0].text.strip()
            data['red_team'] = teams[1].text.strip()
            
        time_block = soup.find(string=re.compile(r'\d{2}:\d{2}'))
        data['duration_seconds'] = parse_duration(time_block) if time_block else 0
        
        # 3. Kills, Gold, Winner
        score_boxes = soup.find_all('div', class_='score-box')
        if len(score_boxes) >= 2:
            blue_stats = score_boxes[0].get_text()
            red_stats = score_boxes[1].get_text()
            
            data['blue_kills'] = extract_number(blue_stats)
            data['red_kills'] = extract_number(red_stats)
            data['total_kills'] = data['blue_kills'] + data['red_kills']
            
            blue_gold_match = re.search(r'(\d+\.\d+)k', blue_stats)
            if blue_gold_match: data['blue_gold'] = int(float(blue_gold_match.group(1)) * 1000)
            
            data['winner_side'] = "Blue" if "WIN" in blue_stats else "Red"

        data['blue_towers'] = 0 
        data['red_towers'] = 0 

        # Insert into DB
        insert_match_data(conn, data)
        
    except Exception as e:
        print(f"Error parsing {game_url}: {e}")

def insert_match_data(conn, data):
    cursor = conn.cursor()
    
    columns = [
        'match_id', 'tournament', 'region', 'stage', 'game_date', 'patch_version',
        'blue_team', 'red_team', 'winner_side', 'game_number', 'best_of',
        'duration_seconds', 'blue_kills', 'red_kills', 'total_kills',
        'blue_gold', 'red_gold', 'blue_towers', 'red_towers', 'blue_dragons',
        'red_dragons', 'blue_barons', 'red_barons', 'gol_match_url'
    ]
    
    # Ensure missing keys become None (NULL in Postgres)
    values = tuple(data.get(col, None) for col in columns)
    
    # PostgreSQL uses %s for placeholders
    placeholders = ', '.join(['%s'] * len(columns))
    
    # Create the UPDATE SET string for ON CONFLICT (exclude match_id from update)
    update_clause = ', '.join([f"{col} = EXCLUDED.{col}" for col in columns if col != 'match_id'])
    
    # Postgres UPSERT syntax
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
# 4. MAIN EXECUTION
# ==========================================
if __name__ == "__main__":
    db_connection = setup_database()
    
    tournament_url = "https://gol.gg/tournament/tournament-matchlist/LTA%20North%202025%20Split%203/"
    summary_links = get_match_links_from_tournament(tournament_url)
    
    print(f"Found {len(summary_links)} series summaries.")
    
    for summary in summary_links:
        headers = {'User-Agent': 'Mozilla/5.0'}
        summary_resp = requests.get(summary, headers=headers)
        summary_soup = BeautifulSoup(summary_resp.text, 'html.parser')
        
        game_links = []
        for a in summary_soup.find_all('a', href=True):
            if 'page-game' in a['href']:
                full_game_url = "https://gol.gg" + a['href'].replace('..', '')
                game_links.append(full_game_url)
                
        # Deduplicate while preserving an order-like structure
        unique_game_links = list(dict.fromkeys(game_links))
        
        for idx, game_url in enumerate(unique_game_links, start=1):
            # Pass idx as the game_number in the series
            scrape_individual_game(game_url, game_number=idx, conn=db_connection)
            
    db_connection.close()