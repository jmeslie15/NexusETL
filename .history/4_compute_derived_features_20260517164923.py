import psycopg2
import pandas as pd
import numpy as np
from psycopg2.extras import execute_values

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

def get_db_connection():
    return psycopg2.connect(**DB_CONFIG) # type: ignore

# ==========================================
# 2. FEATURE COMPUTATION LOGIC
# ==========================================
def compute_and_load_features():
    conn = get_db_connection()
    
    print("1. Extracting raw match data from database...")
    # Pull all relevant data into a Pandas DataFrame
    query = """
        SELECT 
            mp.participant_id, mp.match_id, mp.player_id, mp.team_id, mp.side, mp.champion,
            mp.kills, mp.deaths, mp.assists, mp.kill_participation, 
            mp.gold_share, mp.damage_share, mp.cs_per_min, mp.gold_per_min, mp.damage_per_min,
            mp.wards_placed, mp.wards_killed,
            m.game_date, m.patch_version, m.duration_seconds, m.winner_side,
            m.blue_kills, m.red_kills
        FROM match_participants mp
        JOIN matches m ON mp.match_id = m.match_id
        ORDER BY m.game_date ASC, m.match_id ASC;
    """
    df = pd.read_sql_query(query, conn)
    
    if df.empty:
        print("No participant data found. Please run earlier scrapers.")
        return

    print("2. Engineering base metrics...")
    # Calculate KDA (avoid division by zero)
    df['kda'] = (df['kills'] + df['assists']) / df['deaths'].replace(0, 1)
    
    # Calculate Vision Score Per Minute (VSPM)
    df['duration_minutes'] = df['duration_seconds'] / 60.0
    df['vpm'] = (df['wards_placed'] + df['wards_killed']) / df['duration_minutes']
    
    # Boolean win flag for winrate calculations
    df['is_win'] = (df['side'] == df['winner_side'].str.lower()).astype(int)
    
    # Team kills abstraction
    df['team_kills'] = np.where(df['side'] == 'blue', df['blue_kills'], df['red_kills'])

    print("3. Computing historical rolling averages (preventing data leakage)...")
    # Sort strictly by time to ensure rolling logic is accurate
    df = df.sort_values(by=['game_date', 'match_id'])
    
    # Helper function to shift and calculate rolling means
    def roll(group, col, window):
        return group[col].transform(lambda x: x.shift(1).rolling(window, min_periods=1).mean())

    grouped = df.groupby('player_id')
    
    # General History
    df['games_played_before'] = grouped.cumcount()
    
    # KDA & Kill Participation
    df['avg_kda_last_5'] = roll(grouped, 'kda', 5)
    df['avg_kda_last_10'] = roll(grouped, 'kda', 10)
    df['avg_kp_last_5'] = roll(grouped, 'kill_participation', 5)
    df['avg_kp_last_10'] = roll(grouped, 'kill_participation', 10)
    
    # Resource Shares
    df['avg_gold_share_last_5'] = roll(grouped, 'gold_share', 5)
    df['avg_gold_share_last_10'] = roll(grouped, 'gold_share', 10)
    df['avg_damage_share_last_5'] = roll(grouped, 'damage_share', 5)
    df['avg_damage_share_last_10'] = roll(grouped, 'damage_share', 10)
    
    # Per Minute Metrics
    df['avg_dpm_last_5'] = roll(grouped, 'damage_per_min', 5)
    df['avg_dpm_last_10'] = roll(grouped, 'damage_per_min', 10)
    df['avg_gpm_last_5'] = roll(grouped, 'gold_per_min', 5)
    df['avg_gpm_last_10'] = roll(grouped, 'gold_per_min', 10)
    df['avg_cspm_last_5'] = roll(grouped, 'cs_per_min', 5)
    df['avg_cspm_last_10'] = roll(grouped, 'cs_per_min', 10)
    df['avg_vision_per_min_last_5'] = roll(grouped, 'vpm', 5)
    
    # Volatility (Standard Deviation of KDA over last 5 games)
    df['avg_volatility_last_5'] = grouped['kda'].transform(lambda x: x.shift(1).rolling(5, min_periods=2).std())

    print("4. Computing contextual histories (Champions, Patches, Teams)...")
    # Champion Specific
    champ_group = df.groupby(['player_id', 'champion'])
    df['champion_games_played'] = champ_group.cumcount()
    df['champion_winrate'] = champ_group['is_win'].transform(lambda x: x.shift(1).expanding().mean())

    # Patch Specific
    patch_group = df.groupby(['player_id', 'patch_version'])
    df['patch_games_played'] = patch_group.cumcount()
    df['patch_winrate'] = patch_group['is_win'].transform(lambda x: x.shift(1).expanding().mean())

    # Team Historical Averages (Calculated at the team level, then merged back)

    team_df = df[['match_id', 'team_id', 'team_kills', 'duration_seconds', 'game_date']].drop_duplicates(subset=['match_id', 'team_id']).sort_values(['game_date', 'match_id'])
    team_group = team_df.groupby('team_id')
    
    team_df['team_avg_kills_last_5'] = roll(team_group, 'team_kills', 5)
    team_df['team_avg_duration_last_5'] = roll(team_group, 'duration_seconds', 5)
    
    # Merge team rolling stats back to main dataframe
    df = df.merge(team_df[['match_id', 'team_id', 'team_avg_kills_last_5', 'team_avg_duration_last_5']], 
                  on=['match_id', 'team_id'], how='left')

    # Clean up NaNs created by shifting/rolling (replace with SQL-friendly None)
    # This prevents the database from rejecting empty "first game" rows
    # CRITICAL FIX: Ensure no duplicate participant_ids exist before upserting
    # CRITICAL FIX: Explicitly cast ID columns to avoid type handling errors


    print("5. Upserting computed features into the database...")

    # Ensure no duplicate participant_ids exist before upserting
    df = df.drop_duplicates(subset=['participant_id'], keep='last')
    
    # CRITICAL FIX: Use uppercase "Int64" to safely handle missing/NaN values
    df['participant_id'] = df['participant_id'].astype("Int64")
    df['player_id'] = df['player_id'].astype("Int64")
    df['team_id'] = df['team_id'].astype("Int64")
    df['match_id'] = df['match_id'].astype("Int64")
    
    # Replace any remnant NaN objects with None right before tuple conversion
    # This ensures psycopg2 passes actual SQL NULLs to PostgreSQL
    df = df.replace({np.nan: None})
    # Prepare the specific columns for database insertion
    cols_to_insert = [
        'participant_id', 'match_id', 'player_id', 'team_id',
        'games_played_before', 'avg_kda_last_5', 'avg_kda_last_10', 'avg_kp_last_5', 'avg_kp_last_10',
        'avg_gold_share_last_5', 'avg_gold_share_last_10', 'avg_damage_share_last_5', 'avg_damage_share_last_10',
        'avg_dpm_last_5', 'avg_dpm_last_10', 'avg_gpm_last_5', 'avg_gpm_last_10', 'avg_cspm_last_5', 'avg_cspm_last_10',
        'avg_vision_per_min_last_5', 'avg_volatility_last_5',
        'champion_games_played', 'champion_winrate',
        'team_avg_kills_last_5', 'team_avg_duration_last_5',
        'patch_games_played', 'patch_winrate'
    ]
    
    insert_df = df[cols_to_insert]
    data_tuples = [tuple(row) for row in insert_df.to_numpy()]
    
    cursor = conn.cursor()
    
    # Execute batch upsert for performance
    upsert_sql = f"""
        INSERT INTO participant_derived_features ({', '.join(cols_to_insert)})
        VALUES %s
        ON CONFLICT (participant_id) DO UPDATE SET 
            {', '.join([f"{col}=EXCLUDED.{col}" for col in cols_to_insert if col != 'participant_id'])}
    """
    
    execute_values(cursor, upsert_sql, data_tuples)
    conn.commit()
    
    cursor.close()

        # ==========================================
    # FINAL STEP: RELATIONAL TEAM_ID BACKFILL
    # ==========================================
    print("6. Resolving missing team_ids in participant_derived_features...")
    
    backfill_features_sql = """
        UPDATE participant_derived_features pdf
        SET team_id = mp.team_id
        FROM match_participants mp
        WHERE pdf.participant_id = mp.participant_id
          AND pdf.team_id IS NULL 
          AND mp.team_id IS NOT NULL;
    """
    
    cursor.execute(backfill_features_sql)
    features_affected = cursor.rowcount
    print(f"   Successfully backfilled team_ids for {features_affected} derived feature rows.")
    
    conn.commit()
    
    # Existing close logic
    cursor.close()
    conn.close()
    print("Database update complete! Your features are ready for machine learning.")


if __name__ == "__main__":
    compute_and_load_features()