Systemd Services

Files in this folder help run the stack under systemd. Adjust paths for your host.

Units
- greek-tutor-fastapi.service: Uvicorn for the FastAPI DB API
- greek-tutor-flask.service: Gunicorn for the Flask web app
- greek-tutor.target: Optional umbrella target to manage both

Environment
- Copy services/greek-tutor.env.example to /etc/default/greek-tutor and edit:
  - PROJECT_DIR: absolute path to the project, e.g., /opt/greek_tutor
  - VENV_DIR: absolute path to venv, e.g., /opt/greek_tutor/.venv
  - OPENAI_API_KEY, FLASK_SECRET_KEY, FASTAPI_URL, ports, workers

Install
1) Create venv (Python 3.13), install deps, initialize DBs
   cd /opt/greek_tutor
   python3.13 -m venv .venv
   . .venv/bin/activate
   pip install flask fastapi uvicorn openai pydantic gunicorn markdown bleach
   python db_init.py

2) Place env file
   sudo cp services/greek-tutor.env.example /etc/default/greek-tutor
   sudo chmod 640 /etc/default/greek-tutor
   sudo chown root:root /etc/default/greek-tutor
   # Edit values inside (OPENAI_API_KEY, paths, etc.)

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
- Optionally set a runtime User= in each service to a non-root account.
- Bind to 127.0.0.1 and front with a reverse proxy (e.g., Nginx) for TLS.
- Ensure /etc/default/greek-tutor has correct absolute paths and secrets.
