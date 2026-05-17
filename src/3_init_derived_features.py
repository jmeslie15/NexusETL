import psycopg2
import os
from dotenv import load_dotenv

# ==========================================
# 1. DATABASE SETUP
# ==========================================
load_dotenv() # Loads the variables from the .env file

DB_CONFIG = {
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASS"),
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", "5432")
}

def setup_derived_features_table():
    print("Connecting to database to create participant_derived_features table...")
    conn = psycopg2.connect(**DB_CONFIG) # type: ignore
    cursor = conn.cursor()
    
    # Create the table with strict foreign key constraints
    sql = '''
    CREATE TABLE IF NOT EXISTS participant_derived_features (
        feature_id SERIAL PRIMARY KEY,
        
        -- Relational Links
        participant_id INT UNIQUE REFERENCES match_participants(participant_id) ON DELETE CASCADE,
        match_id BIGINT REFERENCES matches(match_id) ON DELETE CASCADE,
        player_id INT REFERENCES players(player_id) ON DELETE CASCADE,
        team_id INT REFERENCES teams(team_id) ON DELETE CASCADE,
        
        -- General History
        games_played_before INT,
        
        -- KDA & Kill Participation
        avg_kda_last_5 FLOAT,
        avg_kda_last_10 FLOAT,
        avg_kp_last_5 FLOAT,
        avg_kp_last_10 FLOAT,
        
        -- Resource Shares
        avg_gold_share_last_5 FLOAT,
        avg_gold_share_last_10 FLOAT,
        avg_damage_share_last_5 FLOAT,
        avg_damage_share_last_10 FLOAT,
        
        -- Per Minute Metrics
        avg_dpm_last_5 FLOAT,
        avg_dpm_last_10 FLOAT,
        avg_gpm_last_5 FLOAT,
        avg_gpm_last_10 FLOAT,
        avg_cspm_last_5 FLOAT,
        avg_cspm_last_10 FLOAT,
        avg_vision_per_min_last_5 FLOAT,
        
        -- Custom / Advanced Metrics
        avg_volatility_last_5 FLOAT,
        
        -- Champion Specific Context
        champion_games_played INT,
        champion_winrate FLOAT,
        
        -- Team & Patch Context
        team_avg_kills_last_5 FLOAT,
        team_avg_duration_last_5 FLOAT,
        patch_games_played INT,
        patch_winrate FLOAT,
        
        -- Metadata
        created_at TIMESTAMP DEFAULT NOW()
    );
    '''
    
    try:
        cursor.execute(sql)
        conn.commit()
        print("Success! participant_derived_features table created.")
    except Exception as e:
        print(f"Error creating table: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    setup_derived_features_table()