Systemd Services

Files in this folder help run the stack under systemd.

Units
- greek-tutor-fastapi.service: Uvicorn for the FastAPI DB API
- greek-tutor-flask.service: Gunicorn for the Flask web app
- greek-tutor.target: Optional umbrella target to manage both

**Note:** Service files are pre-configured for `/home/zacharyclement/greek_tutor` and user `zacharyclement`.
If deploying elsewhere, edit the service files to update `WorkingDirectory`, `ExecStart` paths, and `User`.

Environment
- Copy services/greek-tutor.env.example to /etc/default/greek-tutor and edit:
  - OPENAI_API_KEY, FLASK_SECRET_KEY, FASTAPI_URL, ports, workers

Install
1) Create venv (Python 3.13), install deps, initialize DBs
   cd /home/zacharyclement/greek_tutor
   python3.13 -m venv .venv
   source .venv/bin/activate
   pip install flask fastapi uvicorn openai pydantic gunicorn markdown bleach
   python db_init.py

   # If upgrading existing database to spaced repetition:
   python migrate_to_spaced_repetition.py

2) Place env file
   sudo cp services/greek-tutor.env.example /etc/default/greek-tutor
   sudo chmod 640 /etc/default/greek-tutor
   sudo chown root:root /etc/default/greek-tutor
   # Edit values inside (OPENAI_API_KEY, secrets, etc.)

3) Install units
   sudo cp services/greek-tutor-fastapi.service /etc/systemd/system/
   sudo cp services/greek-tutor-flask.service /etc/systemd/system/
   sudo cp services/greek-tutor.target /etc/systemd/system/
   sudo systemctl daemon-reload

4) Enable + start
   sudo systemctl enable greek-tutor-fastapi.service
   sudo systemctl enable greek-tutor-flask.service
   sudo systemctl enable greek-tutor.target
   sudo systemctl start greek-tutor.target

5) Logs
   journalctl -u greek-tutor-fastapi.service -f
   journalctl -u greek-tutor-flask.service -f

Notes
- Services run as user `zacharyclement` (update if deploying under different user)
- Services use .venv at `/home/zacharyclement/greek_tutor/.venv`
- Bind to 127.0.0.1 and front with a reverse proxy (e.g., Nginx) for TLS
- Ensure /etc/default/greek-tutor has correct secrets and configuration
