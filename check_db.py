# save as check_db.py in your pumpguru_web folder, then run: python check_db.py
import sqlite3

conn = sqlite3.connect("data/pumpguru.db")
cur = conn.cursor()

print("=== SNAPSHOTS SCHEMA ===")
cur.execute("SELECT sql FROM sqlite_master WHERE name='snapshots'")
print(cur.fetchone()[0])

print("\n=== FAULT_EVENTS SCHEMA ===")
cur.execute("SELECT sql FROM sqlite_master WHERE name='fault_events'")
print(cur.fetchone()[0])

print("\n=== SAMPLE SNAPSHOTS (last 10) ===")
cur.execute("SELECT * FROM snapshots ORDER BY id DESC LIMIT 10")
for row in cur.fetchall():
    print(row)

print("\n=== SAMPLE FAULT EVENTS (last 20) ===")
cur.execute("SELECT * FROM fault_events ORDER BY id DESC LIMIT 20")
for row in cur.fetchall():
    print(row)

conn.close()