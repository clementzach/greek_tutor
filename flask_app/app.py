import os
import sys
import json
from datetime import datetime
from functools import wraps
from markupsafe import Markup
import markdown as md
import bleach
import urllib.request
import urllib.parse

from flask import Flask, render_template, request, redirect, url_for, session, flash, Blueprint
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
# Ensure project root is importable when running from various CWDs
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from agent.agent import GreekTutorAgent, load_memory, save_memory
DATA_DIR = os.path.join(BASE_DIR, 'data')
USERS_PATH = os.path.join(DATA_DIR, 'users.json')
FASTAPI_URL = os.environ.get('FASTAPI_URL', 'http://127.0.0.1:8000')


def http_get_json(url: str, params=None):
    if params:
        qs = urllib.parse.urlencode(params)
        url = f"{url}?{qs}"
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read().decode('utf-8'))


def http_post_json(url: str, payload: dict):
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode('utf-8'))


def load_users():
    if not os.path.exists(USERS_PATH):
        return {"users": []}
    with open(USERS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_users(data):
    with open(USERS_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_user_by_username(username: str):
    users = load_users().get('users', [])
    for u in users:
        if u.get('username') == username:
            return u
    return None


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper


def create_app():
    app = Flask(__name__, template_folder=os.path.join(BASE_DIR, 'templates'), static_folder=os.path.join(BASE_DIR, 'static'))
    app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key')

    # Configure for reverse proxy with /greek prefix
    app.config['APPLICATION_ROOT'] = '/greek'
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    @app.route('/')
    def index():
        if 'user_id' in session:
            return redirect(url_for('dashboard'))
        return redirect(url_for('login'))

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            if not username or not password:
                flash('Username and password required.', 'error')
                return render_template('register.html')
            if get_user_by_username(username):
                flash('Username already exists.', 'error')
                return render_template('register.html')
            data = load_users()
            user_id = username  # keep simple: username is ID
            data['users'].append({
                'id': user_id,
                'username': username,
                'password_hash': generate_password_hash(password),
                'created_at': datetime.utcnow().isoformat(),
            })
            save_users(data)
            flash('Registration successful. Please log in.', 'success')
            return redirect(url_for('login'))
        return render_template('register.html')

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            u = get_user_by_username(username)
            if not u or not check_password_hash(u.get('password_hash', ''), password):
                flash('Invalid credentials.', 'error')
                return render_template('login.html')
            session['user_id'] = u['id']
            session['username'] = u['username']
            return redirect(url_for('dashboard'))
        return render_template('login.html')

    @app.route('/logout')
    def logout():
        # Try summarizing before logout
        try:
            if 'user_id' in session:
                agent = GreekTutorAgent(user_id=session['user_id'])
                mem = load_memory(session['user_id'])
                current_sid = mem.get('active_session_id')
                cur_sess = next((s for s in mem.get('sessions', []) if s.get('id') == current_sid), None)
                if current_sid and cur_sess and cur_sess.get('messages'):
                    agent.summarize_session(current_sid)
        except Exception:
            pass
        session.clear()
        return redirect(url_for('login'))

    @app.route('/dashboard')
    @login_required
    def dashboard():
        user_id = session['user_id']
        mem = load_memory(user_id)
        level = mem.get('level')
        # Load interests and due cards count
        interests = []
        due_count = 0
        total_vocab = 0
        try:
            interests = http_get_json(f"{FASTAPI_URL}/interests/{user_id}")
        except Exception:
            interests = []
        try:
            due_cards = http_get_json(f"{FASTAPI_URL}/vocab/{user_id}/due", {"limit": 1000})
            due_count = len(due_cards)
        except Exception:
            due_count = 0
        try:
            all_vocab = http_get_json(f"{FASTAPI_URL}/vocab/{user_id}")
            total_vocab = len(all_vocab)
        except Exception:
            total_vocab = 0
        return render_template('dashboard.html', level=level, interests=interests, due_count=due_count, total_vocab=total_vocab)

    @app.route('/set_level', methods=['POST'])
    @login_required
    def set_level():
        level = request.form.get('level', '').strip()
        user_id = session['user_id']
        mem = load_memory(user_id)
        mem['level'] = level
        save_memory(user_id, mem)
        flash('Level updated.', 'success')
        return redirect(url_for('dashboard'))

    @app.route('/tutor', methods=['GET', 'POST'])
    @login_required
    def tutor():
        user_id = session['user_id']
        agent = GreekTutorAgent(user_id=user_id)
        mem = load_memory(user_id)
        chat = mem.get('chat', [])
        reply = None
        latest_reply_html = None
        # Quiz state
        quiz = (mem.get('quiz') or {})
        quiz_active = bool(quiz.get('active'))
        # Session handling
        sid_param = request.args.get('sid')
        new_param = request.args.get('new')
        if request.method == 'GET':
            # Summarize current chat before switching/starting new
            current_mem = load_memory(user_id)
            current_sid = current_mem.get('active_session_id')
            if current_sid:
                try:
                    # Only summarize if there are messages
                    cur_sess = next((s for s in current_mem.get('sessions', []) if s.get('id') == current_sid), None)
                    if cur_sess and cur_sess.get('messages'):
                        agent.summarize_session(current_sid)
                except Exception:
                    pass
            if sid_param:
                agent.set_active_session(sid_param)
            else:
                # By default start a new chat when arriving at Tutor
                agent.new_session()
            mem = load_memory(user_id)
        # Handle POST early for quiz grading or ending
        if request.method == 'POST':
            action = request.form.get('action', '').strip()
            user_text = request.form.get('message', '').strip()
            try:
                if action == 'end_quiz' and quiz_active:
                    summary = agent.tool_end_quiz()
                    flash(f"Quiz ended. Score: {summary.get('correct', 0)}/{summary.get('asked', 0)}.", 'success')
                elif quiz_active and (quiz.get('current') or {}).get('token') and user_text:
                    # Grade answer, then advance
                    result = agent.tool_grade_quiz_answer(user_text)
                    verdict = result.get('verdict', 'graded')
                    expl = result.get('explanation') or ''
                    remaining = result.get('remaining', 0)
                    flash(f"Answer: {verdict}. {expl}", 'success' if verdict == 'correct' else 'error')
                    if remaining > 0:
                        agent.tool_next_quiz_question()
                    else:
                        summary = agent.tool_end_quiz()
                        flash(f"Quiz complete. Score: {summary.get('correct', 0)}/{summary.get('total', 0)}.", 'success')
                else:
                    # Regular chat
                    if user_text:
                        reply = agent.chat(user_text)
            except Exception as e:
                reply = f"Error during tutoring: {e}"
            # Refresh state after handling
            mem = load_memory(user_id)
            quiz = (mem.get('quiz') or {})
            quiz_active = bool(quiz.get('active'))
        # If quiz active but no current question, fetch one
        if quiz_active and not (quiz.get('current') or {}).get('token') and (quiz.get('queue') or []):
            try:
                agent = GreekTutorAgent(user_id=user_id)
                agent.tool_next_quiz_question()
                mem = load_memory(user_id)
                quiz = (mem.get('quiz') or {})
                quiz_active = bool(quiz.get('active'))
            except Exception:
                pass
        # markdown rendering and sanitize
        allowed_tags = set(bleach.sanitizer.ALLOWED_TAGS).union({
            'p','pre','code','h1','h2','h3','h4','h5','h6','table','thead','tbody','tr','th','td',
            'ul','ol','li','em','strong','hr','br','blockquote'
        })
        allowed_attrs = {
            'a': ['href', 'title', 'rel', 'target'],
            'th': ['align'], 'td': ['align']
        }
        def to_html(text: str) -> Markup:
            html = md.markdown(text or '', extensions=['fenced_code', 'tables', 'sane_lists', 'nl2br'])
            clean = bleach.clean(html, tags=allowed_tags, attributes=allowed_attrs, strip=True)
            return Markup(clean)
        # reload chat after any update
        mem = load_memory(user_id)
        raw_chat = mem.get('chat', [])
        display_chat = []
        for m in raw_chat:
            display_chat.append({'role': m.get('role'), 'html': to_html(m.get('content', ''))})
        if reply:
            latest_reply_html = to_html(reply)
        # Build sessions sidebar and quiz display
        sessions = agent.list_sessions()
        quiz_display = None
        if quiz_active:
            token = (quiz.get('current') or {}).get('token')
            asked = int(quiz.get('asked') or 0)
            total = int(quiz.get('total') or max(asked, 0))
            remaining = len(quiz.get('queue') or [])
            if token:
                quiz_display = {
                    'question': f"What does '{token}' mean?",
                    'asked': asked,
                    'total': total,
                    'remaining': remaining,
                }
            else:
                quiz_display = {
                    'question': None,
                    'asked': asked,
                    'total': total,
                    'remaining': remaining,
                }
        return render_template('tutor.html', chat=display_chat, latest_reply_html=latest_reply_html, quiz=quiz_display, sessions=sessions)

    @app.post('/new_chat')
    @login_required
    def new_chat():
        user_id = session['user_id']
        agent = GreekTutorAgent(user_id=user_id)
        # Summarize current active chat before creating new
        try:
            mem = load_memory(user_id)
            current_sid = mem.get('active_session_id')
            cur_sess = next((s for s in mem.get('sessions', []) if s.get('id') == current_sid), None)
            if current_sid and cur_sess and cur_sess.get('messages'):
                agent.summarize_session(current_sid)
        except Exception:
            pass
        agent.new_session()
        return redirect(url_for('tutor'))

    

    @app.post('/rename_chat')
    @login_required
    def rename_chat():
        user_id = session['user_id']
        sid = request.form.get('sid', '').strip()
        title = request.form.get('title', '').strip()
        agent = GreekTutorAgent(user_id=user_id)
        if not sid:
            flash('Invalid chat.', 'error')
            return redirect(url_for('tutor'))
        ok = agent.rename_session(sid, title)
        if ok:
            flash('Chat renamed.', 'success')
        else:
            flash('Chat not found.', 'error')
        return redirect(url_for('tutor', sid=sid))

    @app.post('/delete_chat')
    @login_required
    def delete_chat():
        user_id = session['user_id']
        sid = request.form.get('sid', '').strip()
        agent = GreekTutorAgent(user_id=user_id)
        if not sid:
            flash('Invalid chat.', 'error')
            return redirect(url_for('tutor'))
        ok = agent.delete_session(sid)
        if ok:
            flash('Chat deleted.', 'success')
        else:
            flash('Chat not found.', 'error')
        return redirect(url_for('tutor'))

    @app.route('/interests', methods=['POST'])
    @login_required
    def interests():
        # Now handled via Dashboard, keep POST for form submission
        user_id = session['user_id']
        itype = request.form.get('interest_type', '').strip()
        topic = request.form.get('topic', '').strip() or None
        book = request.form.get('book', '').strip() or None
        chapter_str = request.form.get('chapter', '').strip()
        passage_ref = request.form.get('passage_ref', '').strip() or None
        chapter = int(chapter_str) if chapter_str.isdigit() else None
        if itype not in ('topic', 'book', 'chapter', 'passage'):
            flash('Select a valid interest type.', 'error')
        else:
            try:
                http_post_json(f"{FASTAPI_URL}/interests", {
                    'user_id': user_id,
                    'interest_type': itype,
                    'topic': topic,
                    'book': book,
                    'chapter': chapter,
                    'passage_ref': passage_ref,
                })
                flash('Interest recorded.', 'success')
            except Exception as e:
                flash(f'Failed to save interest: {e}', 'error')
        return redirect(url_for('dashboard'))

    @app.post('/generate_vocab')
    @login_required
    def generate_vocab():
        user_id = session['user_id']
        mode = request.form.get('mode', 'global')
        count_str = request.form.get('count', '20')
        book = request.form.get('g_book', '').strip() or None
        chapter_str = request.form.get('g_chapter', '').strip()
        chapter = int(chapter_str) if chapter_str.isdigit() else None
        normalize = request.form.get('normalize', 'on') == 'on'
        try:
            agent = GreekTutorAgent(user_id=user_id)
            res_json = agent.tool_generate_and_insert_vocab(mode=mode, count=int(count_str or 20), book=book, chapter=chapter, normalize=normalize)
            if 'error' in res_json:
                flash(f"{res_json['error']}", 'error')
            else:
                inserted = res_json.get('inserted', [])
                flash(f"Inserted {len(inserted)} words.", 'success')
        except Exception as e:
            flash(f'Failed to generate vocab: {e}', 'error')
        # Log vocab set summary
        try:
            http_post_json(f"{FASTAPI_URL}/vocab_sets", {
                'user_id': user_id,
                'mode': mode,
                'book': book,
                'chapter': chapter,
                'count_requested': int(count_str or 20),
                'count_inserted': int(len(res_json.get('inserted', []))) if isinstance(res_json, dict) else 0,
                'source': res_json.get('source') if isinstance(res_json, dict) else None,
            })
        except Exception:
            pass
        return redirect(url_for('dashboard'))

    @app.post('/start_quiz')
    @login_required
    def start_quiz():
        user_id = session['user_id']
        count_str = request.form.get('count', '20')
        try:
            agent = GreekTutorAgent(user_id=user_id)
            # Always use 'due' mode for spaced repetition
            res = agent.tool_start_quiz(mode='due', count=int(count_str or 20))
            if 'error' in res:
                flash(f"Quiz not started: {res['error']}", 'error')
                return redirect(url_for('dashboard'))
            flash(f"Quiz started with {res.get('total', 0)} questions.", 'success')
            return redirect(url_for('tutor'))
        except Exception as e:
            flash(f'Failed to start quiz: {e}', 'error')
            return redirect(url_for('dashboard'))


    @app.get('/usage')
    @login_required
    def usage():
        # Summarize active chat when navigating to usage guide
        try:
            agent = GreekTutorAgent(user_id=session['user_id'])
            mem = load_memory(session['user_id'])
            current_sid = mem.get('active_session_id')
            cur_sess = next((s for s in mem.get('sessions', []) if s.get('id') == current_sid), None)
            if current_sid and cur_sess and cur_sess.get('messages'):
                agent.summarize_session(current_sid)
        except Exception:
            pass
        return render_template('usage.html')

    @app.post('/autosummarize')
    @login_required
    def autosummarize():
        # Best-effort: summarize current active chat, used by navigator.sendBeacon on unload
        try:
            agent = GreekTutorAgent(user_id=session['user_id'])
            mem = load_memory(session['user_id'])
            current_sid = mem.get('active_session_id')
            cur_sess = next((s for s in mem.get('sessions', []) if s.get('id') == current_sid), None)
            if current_sid and cur_sess and cur_sess.get('messages'):
                agent.summarize_session(current_sid)
        except Exception:
            pass
        return ('', 204)

    return app


app = create_app()
