import psycopg2
import pandas as pd

# Use your actual credentials here
DB_CONFIG = {
    "dbname": "RiftQuant",
    "user": "postgres",
    "password": "LeBronJames2016!",
    "host": "localhost",
    "port": "5432"
}

# Connect to the database
conn = psycopg2.connect(
    dbname=DB_CONFIG["dbname"],
    user=DB_CONFIG["user"],
    password=DB_CONFIG["password"],
    host=DB_CONFIG["host"],
    port=DB_CONFIG["port"]
)

# Use pandas to run a SQL query and load it into a DataFrame
df = pd.read_sql_query("SELECT * FROM matches LIMIT 10;", conn)

# Set pandas to show all columns without truncating them
pd.set_option('display.max_columns', None)

print(df)

conn.close()