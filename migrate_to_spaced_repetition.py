#!/usr/bin/env python3
"""
Migration script to add spaced repetition fields to existing vocab.db
Run this once to upgrade existing databases.
"""
import sqlite3
import os
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
VOCAB_DB = os.path.join(DATA_DIR, 'vocab.db')

def migrate():
    if not os.path.exists(VOCAB_DB):
        print(f"No database found at {VOCAB_DB}")
        return

    conn = sqlite3.connect(VOCAB_DB)
    cur = conn.cursor()

    # Check if migration is needed
    cur.execute("PRAGMA table_info(vocabulary_progress)")
    columns = [row[1] for row in cur.fetchall()]

    if 'ease_factor' in columns:
        print("Database already migrated.")
        conn.close()
        return

    print("Adding spaced repetition fields...")

    # Add new columns with defaults
    cur.execute("ALTER TABLE vocabulary_progress ADD COLUMN ease_factor REAL NOT NULL DEFAULT 2.5")
    cur.execute("ALTER TABLE vocabulary_progress ADD COLUMN interval_days REAL NOT NULL DEFAULT 0")
    cur.execute("ALTER TABLE vocabulary_progress ADD COLUMN next_review_date TEXT")

    # Create index for efficient queries
    cur.execute('CREATE INDEX IF NOT EXISTS idx_vocab_next_review ON vocabulary_progress(user_id, next_review_date)')

    # Set initial next_review_date for existing cards (mark as due now)
    now = datetime.utcnow().isoformat()
    cur.execute("UPDATE vocabulary_progress SET next_review_date = ? WHERE next_review_date IS NULL", (now,))

    conn.commit()
    conn.close()
    print("Migration completed successfully!")

if __name__ == '__main__':
    migrate()
