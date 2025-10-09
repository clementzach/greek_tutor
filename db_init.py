import json
import os
import sqlite3
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)

def init_vocab_db():
    path = os.path.join(DATA_DIR, 'vocab.db')
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        '''CREATE TABLE IF NOT EXISTS vocabulary_progress (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               user_id TEXT NOT NULL,
               vocab_word TEXT NOT NULL,
               times_reviewed INTEGER NOT NULL DEFAULT 0,
               mastery_score REAL NOT NULL DEFAULT 0.0,
               last_reviewed TEXT,
               ease_factor REAL NOT NULL DEFAULT 2.5,
               interval_days REAL NOT NULL DEFAULT 0,
               next_review_date TEXT
           )'''
    )
    cur.execute(
        'CREATE UNIQUE INDEX IF NOT EXISTS idx_vocab_user_word ON vocabulary_progress(user_id, vocab_word)'
    )
    cur.execute(
        'CREATE INDEX IF NOT EXISTS idx_vocab_next_review ON vocabulary_progress(user_id, next_review_date)'
    )
    conn.commit()
    conn.close()
    print(f"Initialized {path}")

def init_concepts_db():
    path = os.path.join(DATA_DIR, 'concepts.db')
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        '''CREATE TABLE IF NOT EXISTS concepts_mastery (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               user_id TEXT NOT NULL,
               concept_name TEXT NOT NULL,
               mastered_at TEXT NOT NULL
           )'''
    )
    cur.execute(
        'CREATE INDEX IF NOT EXISTS idx_concepts_user ON concepts_mastery(user_id)'
    )
    # Track user interests in topics/passages/books/chapters
    cur.execute(
        '''CREATE TABLE IF NOT EXISTS user_interests (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               user_id TEXT NOT NULL,
               interest_type TEXT NOT NULL,   -- topic|book|chapter|passage
               topic TEXT,
               book TEXT,
               chapter INTEGER,
               passage_ref TEXT,
               created_at TEXT NOT NULL
           )'''
    )
    cur.execute('CREATE INDEX IF NOT EXISTS idx_interests_user ON user_interests(user_id)')
    # Log vocab generation sets for summaries
    cur.execute(
        '''CREATE TABLE IF NOT EXISTS vocab_sets (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               user_id TEXT NOT NULL,
               mode TEXT NOT NULL,  -- global|book|chapter
               book TEXT,
               chapter INTEGER,
               count_requested INTEGER NOT NULL,
               count_inserted INTEGER NOT NULL,
               source TEXT,         -- full|sample
               created_at TEXT NOT NULL
           )'''
    )
    cur.execute('CREATE INDEX IF NOT EXISTS idx_vocabsets_user ON vocab_sets(user_id)')
    # Items within a vocab set (words chosen)
    cur.execute(
        '''CREATE TABLE IF NOT EXISTS vocab_set_items (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               user_id TEXT NOT NULL,
               set_id INTEGER NOT NULL,
               vocab_word TEXT NOT NULL
           )'''
    )
    cur.execute('CREATE INDEX IF NOT EXISTS idx_vocabsetitems_set ON vocab_set_items(set_id)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_vocabsetitems_user ON vocab_set_items(user_id)')
    # Cache for glosses to avoid repeated LLM calls
    cur.execute(
        '''CREATE TABLE IF NOT EXISTS gloss_cache (
               word TEXT PRIMARY KEY,
               glosses TEXT NOT NULL,  -- JSON array as text
               updated_at TEXT NOT NULL
           )'''
    )
    conn.commit()
    conn.close()
    print(f"Initialized {path}")

def init_users_json():
    path = os.path.join(DATA_DIR, 'users.json')
    if not os.path.exists(path):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({"users": []}, f, ensure_ascii=False, indent=2)
        print(f"Initialized {path}")
    else:
        print(f"Exists {path}")

def init_gnt_samples():
    path = os.path.join(DATA_DIR, 'gnt_samples.json')
    if not os.path.exists(path):
        samples = [
            {
                "ref": "John 1:1",
                "grc": "Ἐν ἀρχῇ ἦν ὁ Λόγος, καὶ ὁ Λόγος ἦν πρὸς τὸν Θεόν, καὶ Θεὸς ἦν ὁ Λόγος.",
                "eng": "In the beginning was the Word, and the Word was with God, and the Word was God."
            },
            {
                "ref": "John 3:16",
                "grc": "Οὕτως γὰρ ἠγάπησεν ὁ Θεὸς τὸν κόσμον, ὥστε τὸν Υἱὸν τὸν μονογενῆ ἔδωκεν, ἵνα πᾶς ὁ πιστεύων εἰς αὐτὸν μὴ ἀπόληται ἀλλ᾽ ἔχῃ ζωὴν αἰώνιον.",
                "eng": "For God so loved the world, that he gave his only Son, that whoever believes in him should not perish but have eternal life."
            },
            {
                "ref": "1 John 4:8",
                "grc": "ὁ μὴ ἀγαπῶν οὐκ ἔγνω τὸν Θεόν, ὅτι ὁ Θεὸς ἀγάπη ἐστίν.",
                "eng": "Anyone who does not love does not know God, because God is love."
            }
        ]
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(samples, f, ensure_ascii=False, indent=2)
        print(f"Initialized {path}")
    else:
        print(f"Exists {path}")

if __name__ == '__main__':
    ensure_dirs()
    init_vocab_db()
    init_concepts_db()
    init_users_json()
    init_gnt_samples()
    print("Done.")
