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

def safe_int(val):
    clean = re.sub(r'[^\d.]', '', str(val))
    if not clean: return 0
    if '.' in clean and 'k' in str(val).lower(): return int(float(clean) * 1000)
    return int(float(clean))

def safe_div(num, den):
    return num / den if den and den > 0 else 0.0

def update_advanced_stats():
    conn = psycopg2.connect(**DB_CONFIG) # type: ignore
    cursor = conn.cursor()
    
    # Target matches that have identities, but are missing advanced metrics
    cursor.execute("""
        SELECT DISTINCT m.match_id, m.gol_match_url, m.duration_seconds
        FROM matches m JOIN match_participants mp ON m.match_id = mp.match_id
        WHERE mp.damage IS NULL OR mp.damage = 0;
    """)
    matches = cursor.fetchall()
    
    if not matches:
        print("No matches need advanced stats updating!")
        return

    for match_id, game_url, duration_sec in matches:
        fullstats_url = game_url.replace('page-summary', 'page-fullstats').replace('page-game', 'page-fullstats')
        print(f"Updating Metrics for Match {match_id}...")
        
        resp = fetch_with_retries(fullstats_url)
        if not resp: continue
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # 1. Setup a dictionary to hold stats for columns 1 through 10
        parsed_columns = {i: {} for i in range(1, 11)} 
        found_stats = False
        
        # 2. Iterate through ALL rows to find the transposed labels
        for row in soup.find_all('tr'):
            cols = row.find_all(['td', 'th'])
            if len(cols) < 11: continue # Skip rows that don't have all 10 players
                
            label = cols[0].text.strip().lower()
            
            # Map the row's data left-to-right across the 10 player columns
            if label == 'kills':
                for i in range(1, 11): parsed_columns[i]['kills'] = safe_int(cols[i].text)
            elif label == 'deaths':
                for i in range(1, 11): parsed_columns[i]['deaths'] = safe_int(cols[i].text)
            elif label == 'assists':
                for i in range(1, 11): parsed_columns[i]['assists'] = safe_int(cols[i].text)
            elif label == 'cs':
                for i in range(1, 11): parsed_columns[i]['cs'] = safe_int(cols[i].text)
                found_stats = True
            elif label == 'golds':
                for i in range(1, 11): parsed_columns[i]['gold'] = safe_int(cols[i].text)
            elif label == 'total damage to champion':
                for i in range(1, 11): parsed_columns[i]['damage'] = safe_int(cols[i].text)
            elif label == 'wards placed':
                for i in range(1, 11): parsed_columns[i]['wards_placed'] = safe_int(cols[i].text)
            elif label == 'wards destroyed':
                for i in range(1, 11): parsed_columns[i]['wards_killed'] = safe_int(cols[i].text)

        if not found_stats: 
            print(f"    [Warning] Could not parse transposed stats for {match_id}.")
            continue

        # 3. Restructure from Column-Index to Database Format
        parsed_data = []
        totals = {'blue': {'kills': 0, 'gold': 0, 'damage': 0}, 'red': {'kills': 0, 'gold': 0, 'damage': 0}}
        
        for col_idx in range(1, 11):
            side = 'blue' if col_idx <= 5 else 'red'
            role = ROLE_ORDER[(col_idx - 1) % 5]
            stats = parsed_columns[col_idx]
            
            data = {
                'side': side,
                'role': role,
                'kills': stats.get('kills', 0),
                'deaths': stats.get('deaths', 0),
                'assists': stats.get('assists', 0),
                'cs': stats.get('cs', 0),
                'gold': stats.get('gold', 0),
                'damage': stats.get('damage', 0),
                'wards_placed': stats.get('wards_placed', 0),
                'wards_killed': stats.get('wards_killed', 0)
            }
            parsed_data.append(data)
            
            totals[side]['kills'] += data['kills']
            totals[side]['gold'] += data['gold']
            totals[side]['damage'] += data['damage']

        duration_min = safe_div(duration_sec, 60.0)

        # 4. Calculate Advanced Metrics and Execute Update
        for p in parsed_data:
            t_tot = totals[p['side']]
            kp = safe_div((p['kills'] + p['assists']), t_tot['kills'])
            gs = safe_div(p['gold'], t_tot['gold'])
            ds = safe_div(p['damage'], t_tot['damage'])
            csm = safe_div(p['cs'], duration_min)
            gpm = safe_div(p['gold'], duration_min)
            dpm = safe_div(p['damage'], duration_min)
            
            sql = """
                UPDATE match_participants SET 
                    kills=%s, deaths=%s, assists=%s, cs=%s, gold=%s, damage=%s, wards_placed=%s, wards_killed=%s,
                    kill_participation=%s, gold_share=%s, damage_share=%s, cs_per_min=%s, gold_per_min=%s, damage_per_min=%s
                WHERE match_id = %s AND side = %s AND role = %s;
            """
            cursor.execute(sql, (
                p['kills'], p['deaths'], p['assists'], p['cs'], p['gold'], p['damage'], p['wards_placed'], p['wards_killed'],
                round(kp, 4), round(gs, 4), round(ds, 4), round(csm, 2), round(gpm, 2), round(dpm, 2),
                match_id, p['side'], p['role']
            ))
            
        conn.commit()
        time.sleep(1.5)
        
    conn.close()
    print("Advanced metrics update complete!")

if __name__ == "__main__":
    update_advanced_stats()