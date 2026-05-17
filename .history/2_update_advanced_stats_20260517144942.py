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
    
    # Target matches that have identities, but are missing advanced metrics (like damage)
    cursor.execute("""
        SELECT DISTINCT m.match_id, m.gol_match_url, m.duration_seconds
        FROM matches m JOIN match_participants mp ON m.match_id = mp.match_id
        WHERE mp.damage IS NULL OR mp.damage = 0;
    """)
    matches = cursor.fetchall()
    
    for match_id, game_url, duration_sec in matches:
        fullstats_url = game_url.replace('page-summary', 'page-fullstats').replace('page-game', 'page-fullstats')
        print(f"Updating Metrics for Match {match_id}...")
        
        resp = fetch_with_retries(fullstats_url)
        if not resp: continue
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        main_table = None
        col_map = {}
        
        for table in soup.find_all('table'):
            header_row = table.find('tr')
            if not header_row: continue
            headers = header_row.find_all(['th', 'td'])
            header_texts = [th.text.strip().lower() for th in headers]
            all_text = " ".join(header_texts)
            
            if 'gold' in all_text and ('damage' in all_text or 'dmg' in all_text):
                main_table = table
                for idx, text in enumerate(header_texts):
                    if text in ['k', 'kills']: col_map['kills'] = idx
                    elif text in ['d', 'deaths']: col_map['deaths'] = idx
                    elif text in ['a', 'assists']: col_map['assists'] = idx
                    elif text == 'cs' or 'minions' in text: col_map['cs'] = idx
                    elif 'gold' in text and 'min' not in text and '%' not in text: col_map['gold'] = idx
                    elif ('damage' in text or 'dmg' in text) and 'min' not in text and '%' not in text: col_map['damage'] = idx
                    elif 'placed' in text: col_map['wards_placed'] = idx
                    elif 'destroyed' in text or 'cleared' in text or 'killed' in text: col_map['wards_killed'] = idx
                break
                
        if not main_table: continue
            
        rows = main_table.find('tbody').find_all('tr') if main_table.find('tbody') else main_table.find_all('tr')[1:]
        if len(rows) < 10: continue

        parsed_data = []
        totals = {'blue': {'kills': 0, 'gold': 0, 'damage': 0}, 'red': {'kills': 0, 'gold': 0, 'damage': 0}}
        
        for row_idx, row in enumerate(rows[:10]):
            cols = row.find_all('td')
            side = 'blue' if row_idx < 5 else 'red'
            
            def get_stat(name):
                return safe_int(cols[col_map[name]].text) if name in col_map and col_map[name] < len(cols) else 0

            data = {
                'side': side,
                'role': ROLE_ORDER[row_idx % 5],
                'kills': get_stat('kills'), 'deaths': get_stat('deaths'), 'assists': get_stat('assists'),
                'cs': get_stat('cs'), 'gold': get_stat('gold'), 'damage': get_stat('damage'),
                'wards_placed': get_stat('wards_placed'), 'wards_killed': get_stat('wards_killed')
            }
            parsed_data.append(data)
            
            totals[side]['kills'] += data['kills']
            totals[side]['gold'] += data['gold']
            totals[side]['damage'] += data['damage']

        duration_min = safe_div(duration_sec, 60.0)

        for p in parsed_data:
            t_tot = totals[p['side']]
            kp = safe_div((p['kills'] + p['assists']), t_tot['kills'])
            gs = safe_div(p['gold'], t_tot['gold'])
            ds = safe_div(p['damage'], t_tot['damage'])
            csm = safe_div(p['cs'], duration_min)
            gpm = safe_div(p['gold'], duration_min)
            dpm = safe_div(p['damage'], duration_min)
            
            # Update the DB strictly by matching the side and role (ignoring names entirely!)
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
        time.sleep(2)
        
    conn.close()

if __name__ == "__main__":
    update_advanced_stats()