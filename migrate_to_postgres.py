"""Copy everything from the local SQLite file into an empty PostgreSQL database.
Usage: export DATABASE_URL="postgresql+psycopg://USER@localhost:5432/attendance"
       python migrate_to_postgres.py [path/to/attendance.db]"""
import os, sys
from sqlalchemy import create_engine, text
if not os.environ.get("DATABASE_URL", "").startswith("postgresql"):
    sys.exit("Set DATABASE_URL to your PostgreSQL URL first.")
import app as A  # importing creates the empty tables in PostgreSQL
src_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(A.DATA, "attendance.db")
if not os.path.exists(src_path): sys.exit(f"SQLite file not found: {src_path}")
src = create_engine("sqlite:///" + os.path.abspath(src_path))
ctx = A.app.app_context(); ctx.push(); dst = A.db.engine
with src.connect() as s, dst.begin() as d:
    if d.execute(text("select count(*) from \"user\"")).scalar() > 1:
        sys.exit("PostgreSQL already has data. Use an empty database.")
    for t in reversed(A.db.metadata.sorted_tables): d.execute(t.delete())
    for t in A.db.metadata.sorted_tables:
        rows = [dict(r._mapping) for r in s.execute(t.select())]
        if rows: d.execute(t.insert(), rows)
        print(f"{t.name}: {len(rows)} rows")
        if "id" in t.c and rows:
            d.execute(text(f"select setval(pg_get_serial_sequence('\"{t.name}\"','id'), (select max(id) from \"{t.name}\"))"))
print("Done. Now run the app with the same DATABASE_URL.")
