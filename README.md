# Attendance Manager (Flask + PostgreSQL)

## Run
    docker compose up --build
Open http://localhost:8000 and log in as `admin` / `admin123` (set ADMIN_PASSWORD to change the first password).
You must set a new password at first login. Then go to Admin > Setup and paste students (`roll, name` per line) and subjects.
Students log in with their roll number (first password = roll number in lowercase); admin adds teachers in Admin > Accounts.

## Before real use
- Set strong DB_PASSWORD and SECRET_KEY (env vars or a .env file next to docker-compose.yml).
- Serve over HTTPS (Caddy, Nginx or a host like Render/Railway) and set COOKIE_SECURE=1.
- Backup: `docker compose exec db pg_dump -U attendance attendance > backup.sql`
- Local run without Docker: `pip install -r requirements.txt && python -m flask --app app run` (uses SQLite unless DATABASE_URL is set).

## ClassPulse (hackathon build)
- Dashboard: risk level (High/Medium/Low) per student with plain-language reasons, teacher follow-up notes, and an "early warning" count (flagged but still above the minimum).
- Marks: teachers create tests and enter marks; students see their own marks.
- Admin > Demo data: loads 45 synthetic students (DEMO01-DEMO45, password demo1234) for a safe demo; "Remove demo data" clears only those.
- Data is stored in `data/attendance.db` (SQLite) and kept between restarts. Back it up by copying the `data` folder. Do not delete it.
- Flags are advice only; teachers decide follow-up. Demo data is synthetic.
