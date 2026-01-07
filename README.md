# KAZINO

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./run.sh
```

Open:
- http://YOUR_VPS_IP:8000

`./run.sh` prints all local IPs to open the site.

## Public access

- Open the port in firewall (8000) or proxy through Nginx.
- Use `--host 0.0.0.0` so the server is reachable externally.

## Vercel

- Deploy the repo to Vercel (Python runtime).
- The API runs from `api/index.py`, static files are served from the root.
- SQLite on Vercel uses `/tmp` and resets between cold starts. For persistence, set `KAZINO_DB_PATH` to an external volume or switch to a managed DB.

## Config

- Cases and weapons are stored in `.env`.
- Case images go to `case/`.
- Weapon images go to `guns/`.
- Image names should match the case name slug (spaces -> `-`, lowercase), e.g. `Dreams & Nightmares` -> `dreams-nightmares.jpg`.

## Sync cases

```bash
./tools/sync_env.py
```

The script scans `case/` and `guns/`, auto-generates case prices, and adds missing `CASE:`/`WEAPON:` entries to `.env`.
