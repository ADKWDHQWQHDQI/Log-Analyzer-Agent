import sqlite3

conn = sqlite3.connect("builds.db")
cursor = conn.cursor()

cursor.execute("DELETE FROM build_history")
cursor.execute("DELETE FROM sqlite_sequence WHERE name='build_history'")

conn.commit()
conn.close()

print("DB reset complete")
