Greek Tutor Web App (Flask + FastAPI)

Overview
- Flask web app for tutoring students in Biblical (Koine) Greek.
- FastAPI microservice for querying two SQLite databases:
  - vocab.db: users’ vocabulary progress
  - concepts.db: broad concepts mastered
- Simple file-based authentication (users.json). Manual password reset script provided.
- Agentic tutor powered by OpenAI with tools to explain concepts, provide GNT examples, fetch relevant vocab, set user level, and insert vocab/concepts into databases.

Structure
- flask_app/app.py: Flask UI (login, register, dashboard, tutor)
- fastapi_server/api.py: FastAPI service for DB operations
- agent/agent.py: Agent with tools and OpenAI orchestration
- data/gnt_samples.json: Small GNT sample corpus for examples
- data/users.json: Simple user store with hashed passwords
- db_init.py: Initialize vocab.db and concepts.db
- admin/reset_password.py: Manual password reset for users.json
- templates/*, static/*: UI

Requirements
- Python 3.13
- Packages: flask, fastapi, uvicorn, openai, pydantic (v1+), itsdangerous, werkzeug, gunicorn

Setup
1) Create and activate a virtualenv (Python 3.13)
   - python3.13 -m venv .venv
   - source .venv/bin/activate

2) Install dependencies
   - pip install flask fastapi uvicorn openai pydantic gunicorn markdown bleach

3) Initialize databases and data files
   - python db_init.py

4) Set OpenAI API key
   - export OPENAI_API_KEY=sk-...

Run
- Start FastAPI (port 8000):
  - uvicorn fastapi_server.api:app --reload --port 8000

- Start Flask (port 5000):
  - flask --app flask_app.app:app run --port 5000 --debug

- Access at: http://localhost:5000 (local dev) or https://www.zacharyclement.com/greek (production)

Local Scripts
 - One-shot dev runner (both services):
  - bash scripts/setup_and_run_dev.sh
- Or run separately:
  - bash scripts/run_api.sh
  - bash scripts/run_flask.sh

Environment via .env
- Create a `.env` in the project root (see `.env.example`) and the scripts will auto-load it:
  - OPENAI_API_KEY=sk-...
  - FASTAPI_URL=http://127.0.0.1:8000
  - FLASK_SECRET_KEY=...
  - Optional: API_PORT, FLASK_PORT

Configuration
- Environment variables:
  - OPENAI_API_KEY: your OpenAI API key
  - FASTAPI_URL: base URL for the API (default http://localhost:8000)
  - FLASK_SECRET_KEY: override default dev key

Systemd Services
- See services/README.md for full setup.
- Service files are pre-configured for `/home/zacharyclement/greek_tutor` with user `zacharyclement`
- Example steps:
  - Ensure venv exists: `python3.13 -m venv .venv` and install deps
  - Copy services/greek-tutor.env.example to /etc/default/greek-tutor and edit secrets
  - Copy unit files to /etc/systemd/system and enable:
    - sudo systemctl daemon-reload
    - sudo systemctl enable greek-tutor.target
    - sudo systemctl start greek-tutor.target

Nginx Reverse Proxy
- Config at services/nginx/greek-tutor.conf (configured for www.zacharyclement.com/greek)
- Quick steps:
  - Copy location blocks to your existing nginx server config for www.zacharyclement.com
  - Or use as standalone server block (uncomment server sections in config)
  - Update static file path if needed: /home/zacharyclement/greek_tutor/static/
  - sudo nginx -t && sudo systemctl reload nginx
  - See services/nginx/README.md for details

Auth Notes
- Accounts stored in data/users.json
- Passwords hashed with Werkzeug
- Manual reset: python admin/reset_password.py <username> <new_password>

Agent Tools
- explain_concept(concept, level)
- provide_gnt_examples(query)
- get_relevant_vocabulary(user_id, concept)
- set_user_level(user_id, level)
- insert_vocabulary_progress(user_id, vocab_word, mastery_score, times_reviewed)
- insert_concept_mastery(user_id, concept_name)
 - get_gnt_verses(ref|book+chapter+verses)
 - get_kjv_verses(ref|book+chapter+verses)
 - explain_verse_alignment(ref)
 - insert_user_interest(interest_type, topic|book|chapter|passage_ref)
 - generate_and_insert_vocab(mode, count, book?, chapter?)

Caveats
- GNT examples use a small included sample for demonstration.
- Extend data/gnt_samples.json with more verses as needed.

Greek/KJV Verse Download
- A helper script downloads the full Greek NT (SBLGNT) and KJV NT to `data/`:
  - python scripts/download_texts.py
  - Outputs:
    - data/gnt_full.json  [{book, chapter, verse, text_grc}] (from SBLGNT per-book files)
    - data/kjv_nt.json    [{book, chapter, verse, text_eng}]
- If you have a custom verse-per-line GNT, pass it as:
  - python scripts/download_texts.py --gnt-url <url>
- If KJV JSON fallback is needed, you can provide:
  - python scripts/download_texts.py --kjv-url <url>
- Or manually place files at the above paths.

User Interests
- Tracks topics or passages a user is interested in via a new table in concepts.db:
  - user_interests(user_id, interest_type, topic, book, chapter, passage_ref, created_at)
- FastAPI endpoints:
  - POST /interests
  - GET /interests/{user_id}
