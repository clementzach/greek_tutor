from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
import sqlite3
import os
from datetime import datetime
from typing import List, Optional
import json

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
VOCAB_DB = os.path.join(DATA_DIR, 'vocab.db')
CONCEPTS_DB = os.path.join(DATA_DIR, 'concepts.db')

app = FastAPI(title="Greek Tutor DB API")


def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d


def get_conn(path: str):
    conn = sqlite3.connect(path)
    conn.row_factory = dict_factory
    return conn


class VocabItem(BaseModel):
    user_id: str
    vocab_word: str
    times_reviewed: int = 0
    mastery_score: float = 0.0
    last_reviewed: Optional[str] = None
    ease_factor: float = 2.5
    interval_days: float = 0
    next_review_date: Optional[str] = None


class ConceptItem(BaseModel):
    user_id: str
    concept_name: str
    mastered_at: Optional[str] = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/vocab/{user_id}")
def get_vocab(user_id: str):
    conn = get_conn(VOCAB_DB)
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM vocabulary_progress WHERE user_id = ? ORDER BY (last_reviewed IS NULL) ASC, last_reviewed DESC, id DESC",
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


@app.get("/vocab/{user_id}/due")
def get_due_vocab(user_id: str, limit: int = 20):
    """Get vocabulary cards that are due for review using spaced repetition."""
    now = datetime.utcnow().isoformat()
    conn = get_conn(VOCAB_DB)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM vocabulary_progress
        WHERE user_id = ? AND (next_review_date IS NULL OR next_review_date <= ?)
        ORDER BY (next_review_date IS NULL) DESC, next_review_date ASC, id ASC
        LIMIT ?
        """,
        (user_id, now, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


@app.post("/vocab")
def upsert_vocab(item: VocabItem):
    now = datetime.utcnow().isoformat()
    last = item.last_reviewed or now
    next_review = item.next_review_date or now
    conn = get_conn(VOCAB_DB)
    cur = conn.cursor()
    # Upsert by (user_id, vocab_word)
    cur.execute(
        """
        INSERT INTO vocabulary_progress (user_id, vocab_word, times_reviewed, mastery_score, last_reviewed, ease_factor, interval_days, next_review_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, vocab_word)
        DO UPDATE SET times_reviewed=excluded.times_reviewed,
                      mastery_score=excluded.mastery_score,
                      last_reviewed=excluded.last_reviewed,
                      ease_factor=excluded.ease_factor,
                      interval_days=excluded.interval_days,
                      next_review_date=excluded.next_review_date
        """,
        (item.user_id, item.vocab_word, item.times_reviewed, item.mastery_score, last, item.ease_factor, item.interval_days, next_review),
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}


class ReviewUpdate(BaseModel):
    user_id: str
    vocab_word: str
    mastery_delta: float = 0.0


@app.post("/vocab/increment_review")
def increment_review(payload: ReviewUpdate):
    now = datetime.utcnow().isoformat()
    conn = get_conn(VOCAB_DB)
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM vocabulary_progress WHERE user_id=? AND vocab_word=?",
        (payload.user_id, payload.vocab_word),
    )
    row = cur.fetchone()
    if row is None:
        # create with initial review
        cur.execute(
            "INSERT INTO vocabulary_progress(user_id, vocab_word, times_reviewed, mastery_score, last_reviewed) VALUES (?, ?, ?, ?, ?)",
            (payload.user_id, payload.vocab_word, 1, max(0.0, min(1.0, payload.mastery_delta)), now),
        )
    else:
        new_times = (row.get("times_reviewed") or 0) + 1
        new_mastery = max(0.0, min(1.0, (row.get("mastery_score") or 0.0) + payload.mastery_delta))
        cur.execute(
            "UPDATE vocabulary_progress SET times_reviewed=?, mastery_score=?, last_reviewed=? WHERE id=?",
            (new_times, new_mastery, now, row["id"]),
        )
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.get("/concepts/{user_id}")
def get_concepts(user_id: str):
    conn = get_conn(CONCEPTS_DB)
    cur = conn.cursor()
    cur.execute("SELECT * FROM concepts_mastery WHERE user_id=? ORDER BY mastered_at DESC, id DESC", (user_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


@app.post("/concepts")
def add_concept(item: ConceptItem):
    when = item.mastered_at or datetime.utcnow().isoformat()
    conn = get_conn(CONCEPTS_DB)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO concepts_mastery(user_id, concept_name, mastered_at) VALUES (?, ?, ?)",
        (item.user_id, item.concept_name, when),
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.get("/relevant_vocab")
def relevant_vocab(user_id: str, concept: str = "", limit: int = 20):
    """Naive relevance: choose lower-mastery items first; optionally filter by simple substring in word."""
    conn = get_conn(VOCAB_DB)
    cur = conn.cursor()
    if concept:
        cur.execute(
            """
            SELECT * FROM vocabulary_progress
            WHERE user_id=? AND (vocab_word LIKE ? OR mastery_score < 0.7)
            ORDER BY mastery_score ASC, times_reviewed ASC, (last_reviewed IS NULL) ASC, last_reviewed DESC
            LIMIT ?
            """,
            (user_id, f"%{concept}%", limit),
        )
    else:
        cur.execute(
            """
            SELECT * FROM vocabulary_progress
            WHERE user_id=?
            ORDER BY mastery_score ASC, times_reviewed ASC, (last_reviewed IS NULL) ASC, last_reviewed DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
    rows = cur.fetchall()
    conn.close()
    return rows


# Interests endpoints
class InterestItem(BaseModel):
    user_id: str
    interest_type: str  # topic|book|chapter|passage
    topic: Optional[str] = None
    book: Optional[str] = None
    chapter: Optional[int] = None
    passage_ref: Optional[str] = None
    created_at: Optional[str] = None


@app.post("/interests")
def add_interest(item: InterestItem):
    when = item.created_at or datetime.utcnow().isoformat()
    conn = get_conn(CONCEPTS_DB)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO user_interests(user_id, interest_type, topic, book, chapter, passage_ref, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (item.user_id, item.interest_type, item.topic, item.book, item.chapter, item.passage_ref, when),
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.get("/interests/{user_id}")
def list_interests(user_id: str, limit: int = 50):
    conn = get_conn(CONCEPTS_DB)
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM user_interests WHERE user_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
        (user_id, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


# Vocab set generation logs
class VocabSetRecord(BaseModel):
    user_id: str
    mode: str  # global|book|chapter
    book: Optional[str] = None
    chapter: Optional[int] = None
    count_requested: int
    count_inserted: int
    source: Optional[str] = None
    created_at: Optional[str] = None


@app.post("/vocab_sets")
def add_vocab_set(rec: VocabSetRecord):
    when = rec.created_at or datetime.utcnow().isoformat()
    conn = get_conn(CONCEPTS_DB)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO vocab_sets(user_id, mode, book, chapter, count_requested, count_inserted, source, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (rec.user_id, rec.mode, rec.book, rec.chapter, rec.count_requested, rec.count_inserted, rec.source, when),
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {"status": "ok", "id": new_id}


@app.get("/vocab_sets/{user_id}")
def list_vocab_sets(user_id: str, limit: int = 50):
    conn = get_conn(CONCEPTS_DB)
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM vocab_sets WHERE user_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
        (user_id, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


@app.delete("/vocab_sets/{user_id}/{set_id}")
def delete_vocab_set(user_id: str, set_id: int):
    conn = get_conn(CONCEPTS_DB)
    cur = conn.cursor()
    # Ensure ownership and existence
    cur.execute("SELECT id FROM vocab_sets WHERE id=? AND user_id=?", (set_id, user_id))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Set not found")
    # Delete items then set
    cur.execute("DELETE FROM vocab_set_items WHERE set_id=? AND user_id=?", (set_id, user_id))
    cur.execute("DELETE FROM vocab_sets WHERE id=? AND user_id=?", (set_id, user_id))
    conn.commit()
    conn.close()
    return {"status": "ok"}


class VocabSetItems(BaseModel):
    user_id: str
    set_id: int
    words: List[str]


@app.post("/vocab_set_items")
def add_vocab_set_items(payload: VocabSetItems):
    conn = get_conn(CONCEPTS_DB)
    cur = conn.cursor()
    for w in payload.words:
        if not w:
            continue
        cur.execute(
            "INSERT INTO vocab_set_items(user_id, set_id, vocab_word) VALUES (?, ?, ?)",
            (payload.user_id, payload.set_id, w),
        )
    conn.commit()
    conn.close()
    return {"status": "ok", "inserted": len(payload.words)}


@app.get("/vocab_set_items/{user_id}")
def get_vocab_set_items(user_id: str, set_ids: Optional[str] = Query(None, description="Comma-separated set IDs")):
    conn = get_conn(CONCEPTS_DB)
    cur = conn.cursor()
    if set_ids:
        ids = [s.strip() for s in set_ids.split(',') if s.strip().isdigit()]
        if not ids:
            conn.close()
            return []
        placeholders = ','.join('?' for _ in ids)
        cur.execute(f"SELECT * FROM vocab_set_items WHERE user_id=? AND set_id IN ({placeholders})", (user_id, *ids))
    else:
        cur.execute("SELECT * FROM vocab_set_items WHERE user_id=?", (user_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


# Gloss cache endpoints
class GlossEntry(BaseModel):
    word: str
    glosses: List[str]


class GlossBatch(BaseModel):
    entries: List[GlossEntry]


@app.get("/glosses")
def get_glosses(words: Optional[str] = Query(None, description="Comma-separated tokens")):
    if not words:
        return []
    toks = [w for w in (words or '').split(',') if w]
    if not toks:
        return []
    conn = get_conn(CONCEPTS_DB)
    cur = conn.cursor()
    placeholders = ','.join('?' for _ in toks)
    cur.execute(f"SELECT word, glosses FROM gloss_cache WHERE word IN ({placeholders})", toks)
    rows = cur.fetchall()
    conn.close()
    out = []
    for r in rows:
        gs = r.get('glosses')
        try:
            gl = json.loads(gs) if isinstance(gs, str) else (gs or [])
        except Exception:
            gl = []
        out.append({'word': r.get('word'), 'glosses': gl})
    return out


@app.post("/glosses")
def upsert_glosses(batch: GlossBatch):
    if not batch.entries:
        return {"status": "ok", "upserted": 0}
    now = datetime.utcnow().isoformat()
    conn = get_conn(CONCEPTS_DB)
    cur = conn.cursor()
    count = 0
    for e in batch.entries:
        try:
            cur.execute(
                "INSERT INTO gloss_cache(word, glosses, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(word) DO UPDATE SET glosses=excluded.glosses, updated_at=excluded.updated_at",
                (e.word, json.dumps(e.glosses, ensure_ascii=False), now),
            )
            count += 1
        except Exception:
            pass
    conn.commit()
    conn.close()
    return {"status": "ok", "upserted": count}
