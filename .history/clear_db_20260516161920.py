import psycopg2

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

def clear_matches_table():
    try:
        # Connect to your PostgreSQL database
        conn = psycopg2.connect(
            dbname=DB_CONFIG["dbname"],
            user=DB_CONFIG["user"],
            password=DB_CONFIG["password"],
            host=DB_CONFIG["host"],
            port=DB_CONFIG["port"]
        )
        cursor = conn.cursor()
        
        # TRUNCATE deletes all data inside the table but keeps the columns/schema
        print("Clearing all data from the 'matches' table...")
        cursor.execute("TRUNCATE TABLE matches;")
        
        conn.commit()
        print("Success! The 'matches' table is now completely empty.")
        
    except Exception as e:
        print(f"An error occurred: {e}")
        
    finally:
        if conn: # type: ignore
            cursor.close() # type: ignore
            conn.close()
            print("Database connection closed.")

if __name__ == "__main__":
    clear_matches_table()