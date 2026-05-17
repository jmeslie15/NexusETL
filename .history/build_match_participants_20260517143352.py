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
    "user": "postgres",
    "password": "LeBronJames2016!",
    "host": "localhost",
    "port": "5432"
}

def setup_participants_table(conn):
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS match_participants (
        participant_id SERIAL PRIMARY KEY,
        match_id BIGINT REFERENCES matches(match_id) ON DELETE CASCADE,
        player_id INT REFERENCES players(player_id) ON DELETE SET NULL,
        team_id INT REFERENCES teams(team_id) ON DELETE SET NULL,
        
        side TEXT NOT NULL,
        role TEXT NOT NULL,
        champion TEXT NOT NULL,
        
        kills INT,
        deaths INT,
        assists INT,
        cs INT,
        gold INT,
        damage INT,
        wards_placed INT,
        wards_killed INT,
        
        kill_participation DECIMAL(5,4),
        gold_share DECIMAL(5,4),
        damage_share DECIMAL(5,4),
        
        cs_per_min DECIMAL(6,2),
        gold_per_min DECIMAL(6,2),
        damage_per_min DECIMAL(6,2),
        
        UNIQUE (match_id, player_id)
    );
    ''')
    conn.commit()

# ==========================================
# 2. NETWORK HELPER
# ==========================================
def fetch_with_retries(url, max_retries=5): # Increased to 5 retries
    # Expanded headers to look like a legitimate Google Chrome browser
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Connection': 'keep-alive',
    }
    
    for attempt in range(1, max_retries + 1):
        try:
            # Increased timeout to 15 seconds for slower server responses
            response = requests.get(url, headers=headers, timeout=15)
            
            # Explicitly check if we got hit by a Rate Limit (HTTP 429)
            if response.status_code == 429:
                wait_time = 5 * attempt
                print(f"    [Rate Limited] Server caught us. Cooling down for {wait_time}s...")
                time.sleep(wait_time)
                continue # Skip the rest of the loop and try again
                
            response.raise_for_status() # Catch 500 Server Errors or 404s
            return response
            
        except (RequestException, IncompleteRead, ConnectionError) as e:
            print(f"    [Warning] Hiccup on attempt {attempt}/{max_retries}: {type(e).__name__}")
            if attempt < max_retries:
                # Exponential backoff: Wait 3s, then 6s, then 12s, then 24s
                sleep_time = 3 * (2 ** (attempt - 1))
                print(f"    Sleeping for {sleep_time} seconds before retrying...")
                time.sleep(sleep_time)
            else:
                print(f"    [Failed] Completely giving up on {url}")
                raise

# ==========================================
# 3. HELPER FUNCTIONS
# ==========================================
def safe_int(val):
    if not val: return 0
    # Strip commas and any text (like "k" if they format gold as 15.2k)
    clean_val = re.sub(r'[^\d.]', '', str(val))
    try:
        # If there's a decimal, it might be thousands (e.g. 15.2 -> 15200)
        if '.' in clean_val and 'k' in str(val).lower():
            return int(float(clean_val) * 1000)
        return int(float(clean_val))
    except ValueError:
        return 0

def safe_div(numerator, denominator):
    return numerator / denominator if denominator and denominator > 0 else 0.0

# ==========================================
# 4. SCRAPING LOGIC
# ==========================================
def scrape_match_participants(match_id, game_url, duration_sec, blue_team_id, red_team_id, conn):
    # Convert standard gol_match_url to the fullstats tab
    fullstats_url = game_url.replace('page-summary', 'page-fullstats').replace('page-game', 'page-fullstats')
    print(f"  Scraping Participants: {fullstats_url}")
    
    try:
        response = fetch_with_retries(fullstats_url)
    except Exception:
        return

    soup = BeautifulSoup(response.text, 'html.parser') # type: ignore
    cursor = conn.cursor()

    # The fullstats page usually has one main large table.
    # We find the table that has headers containing "Player" or "Kills"
    main_table = soup.find('table', class_=re.compile('table'))
    if not main_table:
        print("    [Error] Could not find the stats table on page.")
        return

    # Dynamically map column indices to handle layout changes gracefully
    headers = main_table.find_all('th')
    col_map = {}
    for idx, th in enumerate(headers):
        header_text = th.text.strip().lower()
        if 'player' in header_text: col_map['player'] = idx
        elif 'kills' in header_text: col_map['kills'] = idx
        elif 'deaths' in header_text: col_map['deaths'] = idx
        elif 'assists' in header_text: col_map['assists'] = idx
        elif 'cs' in header_text: col_map['cs'] = idx
        elif 'gold' in header_text: col_map['gold'] = idx
        elif 'damage' in header_text: col_map['damage'] = idx
        elif 'wards placed' in header_text or 'placed' in header_text: col_map['wards_placed'] = idx
        elif 'wards destroyed' in header_text or 'cleared' in header_text or 'killed' in header_text: col_map['wards_killed'] = idx

    rows = main_table.find('tbody').find_all('tr') if main_table.find('tbody') else main_table.find_all('tr')[1:]
    
    # Ensure we got 10 players
    if len(rows) < 10:
        print(f"    [Error] Found {len(rows)} players instead of 10.")
        return

    # Standard competitive draft order for rows 0-4 (Blue) and 5-9 (Red)
    ROLE_ORDER = ['TOP', 'JUNGLE', 'MID', 'ADC', 'SUPPORT']
    parsed_players = []
    
    # 1st Pass: Extract raw data
    for row_idx, row in enumerate(rows[:10]):
        cols = row.find_all('td')
        
        # Determine side and team_id based on standard gol.gg row ordering
        side = 'blue' if row_idx < 5 else 'red'
        team_id = blue_team_id if side == 'blue' else red_team_id
        
        # Assign role strictly by scoreboard position
        assigned_role = ROLE_ORDER[row_idx % 5]

        # Target the Player cell
        player_td = cols[col_map.get('player', 0)]
        
        # 1. Champion: Grab the very first image in the cell
        champ_img = player_td.find('img')
        champion = champ_img['alt'] if champ_img and 'alt' in champ_img.attrs else "Unknown"

        # 2. Player Name: Target the anchor <a> tag to ignore country flags
        player_a = player_td.find('a')
        player_name = player_a.text.strip() if player_a else player_td.text.strip()
        
        player_data = {
            'player_name': player_name,
            'team_id': team_id,
            'side': side,
            'champion': champion,
            'role': assigned_role,
            'kills': safe_int(cols[col_map.get('kills', 2)].text),
            'deaths': safe_int(cols[col_map.get('deaths', 3)].text),
            'assists': safe_int(cols[col_map.get('assists', 4)].text),
            'cs': safe_int(cols[col_map.get('cs', 5)].text),
            'gold': safe_int(cols[col_map.get('gold', 6)].text),
            'damage': safe_int(cols[col_map.get('damage', 7)].text),
            'wards_placed': safe_int(cols[col_map.get('wards_placed', 8)].text),
            'wards_killed': safe_int(cols[col_map.get('wards_killed', 9)].text)
        }
        parsed_players.append(player_data)

    # Calculate Team Totals for Metric Shares
    totals = {'blue': {'kills': 0, 'gold': 0, 'damage': 0}, 'red': {'kills': 0, 'gold': 0, 'damage': 0}}
    for p in parsed_players:
        totals[p['side']]['kills'] += p['kills']
        totals[p['side']]['gold'] += p['gold']
        totals[p['side']]['damage'] += p['damage']

    duration_min = safe_div(duration_sec, 60.0)

    # 2nd Pass: Calculate advanced metrics and Insert
    for p in parsed_players:
        t_totals = totals[p['side']]
        
        kill_participation = safe_div((p['kills'] + p['assists']), t_totals['kills'])
        gold_share = safe_div(p['gold'], t_totals['gold'])
        damage_share = safe_div(p['damage'], t_totals['damage'])
        
        cs_per_min = safe_div(p['cs'], duration_min)
        gold_per_min = safe_div(p['gold'], duration_min)
        damage_per_min = safe_div(p['damage'], duration_min)

        # Lookup player_id case-insensitively from our players table
        cursor.execute("SELECT player_id FROM players WHERE player_name ILIKE %s", (p['player_name'],))
        player_row = cursor.fetchone()
        player_id = player_row[0] if player_row else None

        sql = """
            INSERT INTO match_participants (
                match_id, player_id, team_id, side, role, champion, 
                kills, deaths, assists, cs, gold, damage, wards_placed, wards_killed,
                kill_participation, gold_share, damage_share, 
                cs_per_min, gold_per_min, damage_per_min
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (match_id, player_id) 
            DO UPDATE SET 
                team_id=EXCLUDED.team_id, side=EXCLUDED.side, role=EXCLUDED.role, champion=EXCLUDED.champion,
                kills=EXCLUDED.kills, deaths=EXCLUDED.deaths, assists=EXCLUDED.assists, 
                cs=EXCLUDED.cs, gold=EXCLUDED.gold, damage=EXCLUDED.damage, wards_placed=EXCLUDED.wards_placed, wards_killed=EXCLUDED.wards_killed,
                kill_participation=EXCLUDED.kill_participation, gold_share=EXCLUDED.gold_share, 
                damage_share=EXCLUDED.damage_share, cs_per_min=EXCLUDED.cs_per_min, 
                gold_per_min=EXCLUDED.gold_per_min, damage_per_min=EXCLUDED.damage_per_min;
        """
        
        cursor.execute(sql, (
            match_id, player_id, p['team_id'], p['side'], p['role'], p['champion'],
            p['kills'], p['deaths'], p['assists'], p['cs'], p['gold'], p['damage'], p['wards_placed'], p['wards_killed'],
            round(kill_participation, 4), round(gold_share, 4), round(damage_share, 4),
            round(cs_per_min, 2), round(gold_per_min, 2), round(damage_per_min, 2)
        ))
        
    conn.commit()
    print(f"  --> Saved 10 participants for match {match_id}")

# ==========================================
# 5. MAIN EXECUTION
# ==========================================
def build_participants_database():
    conn = psycopg2.connect(**DB_CONFIG) # type: ignore
    setup_participants_table(conn)
    
    cursor = conn.cursor()
    # Find matches that exist but don't have 10 participants logged yet
    query = """
        SELECT m.match_id, m.gol_match_url, m.duration_seconds, m.blue_team_id, m.red_team_id 
        FROM matches m
        LEFT JOIN match_participants mp ON m.match_id = mp.match_id
        GROUP BY m.match_id, m.gol_match_url, m.duration_seconds, m.blue_team_id, m.red_team_id
        HAVING COUNT(mp.participant_id) < 10;
    """
    cursor.execute(query)
    matches_to_process = cursor.fetchall()
    
    if not matches_to_process:
        print("All matches already have 10 participants logged. Database is fully up to date!")
        return

    print(f"Found {len(matches_to_process)} matches needing participant data. Commencing scrape...\n")
    
    for match_id, url, duration, blue_id, red_id in matches_to_process:
        # Failsafe if game duration is somehow missing from matches table
        if not duration or duration <= 0:
            print(f"    [Skipped] Match {match_id} has 0 duration in matches table. Cannot calculate per-min stats.")
            continue
            
        scrape_match_participants(match_id, url, duration, blue_id, red_id, conn)
        time.sleep(3)

    conn.close()
    print("\nParticipant database build complete!")

if __name__ == "__main__":
    build_participants_database()