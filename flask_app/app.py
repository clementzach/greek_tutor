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
from config.logging_config import get_flask_logger, log_request, log_error

# Initialize logger
logger = get_flask_logger()
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

    logger.info("Flask application starting...")
    logger.info(f"Application root: {app.config['APPLICATION_ROOT']}")
    logger.info(f"FastAPI URL: {FASTAPI_URL}")

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
            logger.info(f"Registration attempt for username: {username}")
            if not username or not password:
                logger.warning(f"Registration failed: missing credentials for {username}")
                flash('Username and password required.', 'error')
                return render_template('register.html')
            if get_user_by_username(username):
                logger.warning(f"Registration failed: username '{username}' already exists")
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
            logger.info(f"User registered successfully: {username}")
            flash('Registration successful. Please log in.', 'success')
            return redirect(url_for('login'))
        return render_template('register.html')

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            logger.info(f"Login attempt for username: {username}")
            u = get_user_by_username(username)
            if not u or not check_password_hash(u.get('password_hash', ''), password):
                logger.warning(f"Login failed for username: {username}")
                flash('Invalid credentials.', 'error')
                return render_template('login.html')
            session['user_id'] = u['id']
            session['username'] = u['username']
            logger.info(f"User logged in successfully: {username}")
            return redirect(url_for('dashboard'))
        return render_template('login.html')

    @app.route('/logout')
    def logout():
        user_id = session.get('user_id', 'unknown')
        logger.info(f"Logout initiated for user: {user_id}")
        # Try summarizing before logout
        try:
            if 'user_id' in session:
                agent = GreekTutorAgent(user_id=session['user_id'])
                mem = load_memory(session['user_id'])
                current_sid = mem.get('active_session_id')
                cur_sess = next((s for s in mem.get('sessions', []) if s.get('id') == current_sid), None)
                if current_sid and cur_sess and cur_sess.get('messages'):
                    agent.summarize_session(current_sid)
                    logger.debug(f"Session {current_sid} summarized for user {user_id}")
        except Exception as e:
            log_error(logger, e, f"Session summarization during logout for user {user_id}")
        session.clear()
        logger.info(f"User logged out: {user_id}")
        return redirect(url_for('login'))

    @app.route('/dashboard')
    @login_required
    def dashboard():
        user_id = session['user_id']
        logger.debug(f"Dashboard accessed by user: {user_id}")
        mem = load_memory(user_id)
        level = mem.get('level')
        # Load interests and due cards count
        interests = []
        due_count = 0
        total_vocab = 0
        total_reviewed = 0
        mastered_count = 0

        try:
            interests = http_get_json(f"{FASTAPI_URL}/interests/{user_id}")
        except Exception as e:
            log_error(logger, e, f"Failed to load interests for user {user_id}")
            interests = []

        try:
            due_cards = http_get_json(f"{FASTAPI_URL}/vocab/{user_id}/due", {"limit": 1000})
            due_count = len(due_cards)
        except Exception as e:
            log_error(logger, e, f"Failed to load due cards for user {user_id}")
            due_count = 0

        try:
            all_vocab = http_get_json(f"{FASTAPI_URL}/vocab/{user_id}")
            total_vocab = len(all_vocab)
            # Calculate total reviewed (cards with times_reviewed > 0)
            total_reviewed = sum(1 for v in all_vocab if v.get('times_reviewed', 0) > 0)
        except Exception as e:
            log_error(logger, e, f"Failed to load vocabulary for user {user_id}")
            total_vocab = 0
            total_reviewed = 0

        try:
            concepts = http_get_json(f"{FASTAPI_URL}/concepts/{user_id}")
            mastered_count = len(concepts)
        except Exception as e:
            log_error(logger, e, f"Failed to load concepts for user {user_id}")
            mastered_count = 0

        # Compute default quiz count for template
        default_quiz_count = min(due_count, 20)

        return render_template('dashboard.html',
                             level=level,
                             interests=interests,
                             due_count=due_count,
                             total_vocab=total_vocab,
                             total_reviewed=total_reviewed,
                             mastered_count=mastered_count,
                             default_quiz_count=default_quiz_count)

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
        logger.debug(f"Tutor page accessed by user: {user_id}")
        agent = GreekTutorAgent(user_id=user_id)
        mem = load_memory(user_id)
        chat = mem.get('chat', [])
        reply = None
        latest_reply_html = None
        # Quiz state
        quiz = (mem.get('quiz') or {})
        quiz_active = bool(quiz.get('active'))
        quiz_feedback = None  # Store feedback for display in quiz card
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
                # Check for end_quiz first - it can come from either state (awaiting_next or answering)
                if action == 'end_quiz':
                    logger.info(f"User {user_id} ending quiz early")
                    summary = agent.tool_end_quiz()
                    flash(f"Quiz ended. Score: {summary.get('correct', 0)}/{summary.get('asked', 0)}.", 'success')
                elif action == 'next_question' and quiz_active:
                    # Move to next question
                    remaining = len(quiz.get('queue') or [])
                    if remaining > 0:
                        agent.tool_next_quiz_question()
                    else:
                        logger.info(f"User {user_id} completed quiz")
                        summary = agent.tool_end_quiz()
                        flash(f"Quiz complete. Score: {summary.get('correct', 0)}/{summary.get('total', 0)}.", 'success')
                elif action == 'answer_quiz' and quiz_active and user_text:
                    # Grade answer - don't auto-advance
                    logger.debug(f"Grading quiz answer for user {user_id}")
                    result = agent.tool_grade_quiz_answer(user_text)
                    verdict = result.get('verdict', 'graded')
                    expl = result.get('explanation') or ''
                    # Feedback will be stored in quiz state by the agent
                else:
                    # Regular chat
                    if user_text:
                        logger.info(f"Chat message from user {user_id}: {user_text[:50]}...")
                        reply = agent.chat(user_text)
            except Exception as e:
                log_error(logger, e, f"Error during tutoring for user {user_id}")
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
        # reload chat from active session
        mem = load_memory(user_id)
        active_sid = mem.get('active_session_id')
        raw_chat = []
        if active_sid:
            active_sess = next((s for s in mem.get('sessions', []) if s.get('id') == active_sid), None)
            if active_sess:
                raw_chat = active_sess.get('messages', [])

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
            awaiting_next = quiz.get('awaiting_next', False)
            last_feedback = quiz.get('last_feedback')

            if token:
                quiz_display = {
                    'question': f"What does '{token}' mean?",
                    'asked': asked,
                    'total': total,
                    'remaining': remaining,
                    'feedback': last_feedback,
                    'awaiting_next': awaiting_next,
                }
            else:
                quiz_display = {
                    'question': None,
                    'asked': asked,
                    'total': total,
                    'remaining': remaining,
                    'feedback': last_feedback,
                    'awaiting_next': awaiting_next,
                }

        # Build personalized suggestions for empty chat
        suggestions = None
        if len(display_chat) == 0 and not quiz_active:
            # Get user level and interests for personalization
            level = mem.get('level') or 'beginner'
            try:
                interests_data = http_get_json(f"{FASTAPI_URL}/interests/{user_id}")
            except Exception:
                interests_data = []

            # Get completed activities to avoid repetition
            try:
                completed_concepts = set()
                completed_verses = set()
                activities = http_get_json(f"{FASTAPI_URL}/activities/{user_id}")
                for act in activities:
                    if act.get('activity_type') == 'concept':
                        completed_concepts.add(act.get('activity_value'))
                    elif act.get('activity_type') == 'verse':
                        completed_verses.add(act.get('activity_value'))
            except Exception:
                completed_concepts = set()
                completed_verses = set()

            # Try to get mastered concepts
            try:
                concepts_data = http_get_json(f"{FASTAPI_URL}/concepts/{user_id}")
                mastered_concepts = [c.get('concept_name') for c in concepts_data]
            except Exception:
                mastered_concepts = []

            # Personalize concept suggestion based on level and mastered concepts
            # Progressive concept curriculum
            concept_suggestions = {
                'beginner': ['Greek Alphabet', 'Basic Nouns', 'Present Tense Verbs', 'Article Usage', 'Basic Adjectives', 'Pronouns', 'Prepositions'],
                'intermediate': ['Aorist Tense', 'Participles', 'Infinitives', 'Greek Cases', 'Imperfect Tense', 'Middle Voice', 'Dependent Clauses'],
                'advanced': ['Conditional Sentences', 'Subjunctive Mood', 'Optative Mood', 'Discourse Analysis', 'Textual Criticism', 'Rhetorical Devices']
            }

            level_key = 'beginner'
            if level and ('intermediate' in level.lower() or 'b1' in level.lower() or 'b2' in level.lower()):
                level_key = 'intermediate'
            elif level and ('advanced' in level.lower() or 'c1' in level.lower() or 'c2' in level.lower()):
                level_key = 'advanced'

            # Filter out both mastered and already-engaged concepts
            all_completed = completed_concepts.union(set(mastered_concepts))
            available_concepts = [c for c in concept_suggestions.get(level_key, concept_suggestions['beginner']) if c not in all_completed]
            suggested_concept = available_concepts[0] if available_concepts else 'Greek Grammar Review'

            # Find a verse from user interests that hasn't been translated yet
            suggested_verse = None
            for interest in interests_data:
                verse_ref = None
                if interest.get('interest_type') == 'passage' and interest.get('passage_ref'):
                    verse_ref = f"{interest.get('book')} {interest.get('chapter')}:{interest.get('passage_ref')}"
                elif interest.get('interest_type') == 'chapter' and interest.get('book') and interest.get('chapter'):
                    verse_ref = f"{interest.get('book')} {interest.get('chapter')}:1"
                elif interest.get('interest_type') == 'book' and interest.get('book'):
                    verse_ref = f"{interest.get('book')} 1:1"

                if verse_ref and verse_ref not in completed_verses:
                    suggested_verse = verse_ref
                    break

            # Fallback to default verses if no interests or all completed
            if not suggested_verse:
                default_verses = ["John 3:16", "John 1:1", "Romans 3:23", "Philippians 2:5", "1 John 4:8"]
                for v in default_verses:
                    if v not in completed_verses:
                        suggested_verse = v
                        break
                if not suggested_verse:
                    suggested_verse = "John 3:16"

            # Get due cards count
            try:
                due_cards = http_get_json(f"{FASTAPI_URL}/vocab/{user_id}/due", {"limit": 1000})
                due_count = len(due_cards)
            except Exception:
                due_count = 0

            suggestions = {
                'concept': suggested_concept,
                'verse': suggested_verse,
                'due_count': due_count
            }

        return render_template('tutor.html', chat=display_chat, latest_reply_html=latest_reply_html, quiz=quiz_display, sessions=sessions, suggestions=suggestions)

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

        # Get vocab generation options
        generate_vocab = request.form.get('generate_vocab') == 'on'
        count_str = request.form.get('count', '20')
        normalize = request.form.get('normalize') == 'on'

        # Validate interest type
        if itype not in ('topic', 'book', 'chapter', 'passage'):
            flash('Select a valid interest type.', 'error')
            return redirect(url_for('dashboard'))

        # Always record the interest
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

        # Optionally generate vocabulary
        if generate_vocab:
            try:
                agent = GreekTutorAgent(user_id=user_id)

                # Determine mode based on interest type
                if itype == 'chapter' or itype == 'passage':
                    mode = 'chapter'
                elif itype == 'book':
                    mode = 'book'
                else:  # topic
                    mode = 'global'

                count = int(count_str) if count_str.isdigit() else 20
                res = agent.tool_generate_and_insert_vocab(
                    mode=mode,
                    count=count,
                    book=book,
                    chapter=chapter,
                    normalize=normalize
                )

                if 'error' in res:
                    flash(f"Vocab generation failed: {res['error']}", 'warning')
                else:
                    inserted = res.get('inserted', [])
                    flash(f"Generated {len(inserted)} vocabulary words from your interest.", 'success')

                    # Log vocab set summary
                    try:
                        http_post_json(f"{FASTAPI_URL}/vocab_sets", {
                            'user_id': user_id,
                            'mode': mode,
                            'book': book,
                            'chapter': chapter,
                            'count_requested': count,
                            'count_inserted': len(inserted),
                            'source': res.get('source'),
                        })
                    except Exception:
                        pass
            except Exception as e:
                flash(f"Could not generate vocabulary: {e}", 'warning')

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

                # Auto-record interest when generating vocab from book or chapter
                if mode in ('book', 'chapter') and book:
                    try:
                        interest_type = 'chapter' if mode == 'chapter' else 'book'
                        http_post_json(f"{FASTAPI_URL}/interests", {
                            'user_id': user_id,
                            'interest_type': interest_type,
                            'topic': None,
                            'book': book,
                            'chapter': chapter if mode == 'chapter' else None,
                            'passage_ref': None,
                        })
                    except Exception:
                        pass  # Don't fail if interest recording fails

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
        logger.info(f"User {user_id} starting quiz with {count_str} questions")
        try:
            agent = GreekTutorAgent(user_id=user_id)
            # Always use 'due' mode for spaced repetition
            res = agent.tool_start_quiz(mode='due', count=int(count_str or 20))
            if 'error' in res:
                logger.warning(f"Quiz start failed for user {user_id}: {res['error']}")
                flash(f"Quiz not started: {res['error']}", 'error')
                return redirect(url_for('dashboard'))
            logger.info(f"Quiz started for user {user_id} with {res.get('total', 0)} questions")
            flash(f"Quiz started with {res.get('total', 0)} questions.", 'success')
            return redirect(url_for('tutor'))
        except Exception as e:
            log_error(logger, e, f"Failed to start quiz for user {user_id}")
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
