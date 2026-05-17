import time, requests, psycopg2, re
from bs4 import BeautifulSoup
from requests.exceptions import RequestException

DB_CONFIG = {"dbname": "RiftQuant", "user": "postgres", "password": "LeBronJames2016!", "host": "localhost", "port": "5432"}
ROLE_ORDER = ['TOP', 'JUNGLE', 'MID', 'ADC', 'SUPPORT']

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