import os
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from openai import OpenAI

import urllib.request
import urllib.parse

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
MEMORY_DIR = os.path.join(DATA_DIR, 'memory')
os.makedirs(MEMORY_DIR, exist_ok=True)

from .bible import load_gnt, load_kjv, get_verses, parse_ref, canonical_book, frequency_gnt, load_gnt_samples_as_full, strip_diacritics

def load_memory(user_id: str) -> Dict[str, Any]:
    path = os.path.join(MEMORY_DIR, f"{user_id}.json")
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"level": None, "chat": [], "sessions": [], "active_session_id": None}


def save_memory(user_id: str, mem: Dict[str, Any]):
    path = os.path.join(MEMORY_DIR, f"{user_id}.json")
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(mem, f, ensure_ascii=False, indent=2)


def http_get_json(url: str, params: Dict[str, Any] = None):
    if params:
        qs = urllib.parse.urlencode(params)
        url = f"{url}?{qs}"
    with urllib.request.urlopen(url) as resp:
        data = resp.read()
        return json.loads(data.decode('utf-8'))


def http_post_json(url: str, payload: Dict[str, Any]):
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req) as resp:
        body = resp.read()
        return json.loads(body.decode('utf-8'))


class GreekTutorAgent:
    def __init__(self, user_id: str, fastapi_url: Optional[str] = None, model: str = "gpt-4o-mini"):
        self.user_id = user_id
        self.client = OpenAI()
        self.model = model
        self.fastapi_url = fastapi_url or os.environ.get('FASTAPI_URL', 'http://localhost:8000')
        self.memory = load_memory(user_id)
        self._last_user_text: str = ""
        # one-time migration from single-chat to sessions
        if self.memory.get('sessions') is None:
            self.memory['sessions'] = []
        if self.memory.get('active_session_id') is None:
            # migrate prior flat chat if present
            prior = self.memory.get('chat') or []
            if prior:
                sid = self._new_session_id()
                self.memory['sessions'].append({
                    'id': sid,
                    'created_at': datetime.utcnow().isoformat(),
                    'updated_at': datetime.utcnow().isoformat(),
                    'messages': prior,
                    'summary': None,
                    'title': None,
                })
                self.memory['active_session_id'] = sid
                self.memory['chat'] = []
                save_memory(self.user_id, self.memory)

    # Tool implementations
    def tool_explain_concept(self, concept: str, level: Optional[str] = None) -> str:
        prompt = (
            "You are a concise Biblical Greek tutor. Explain the concept clearly, "
            "step-by-step, with 1-2 simple examples. Use Koine Greek terminology when useful.\n"
            f"Concept: {concept}\n"
            f"Student level: {level or self.memory.get('level') or 'unknown'}\n"
        )
        r = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "You explain Koine Greek concepts succinctly for learners."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.5,
        )
        return r.choices[0].message.content or ""

    def tool_provide_gnt_examples(self, query: str) -> List[Dict[str, str]]:
        path = os.path.join(DATA_DIR, 'gnt_samples.json')
        try:
            with open(path, 'r', encoding='utf-8') as f:
                samples = json.load(f)
        except Exception:
            samples = []
        q = (query or "").lower()
        if not q:
            return samples[:3]
        results = []
        for s in samples:
            if q in s.get('grc', '').lower() or q in s.get('eng', '').lower() or q in s.get('ref', '').lower():
                results.append(s)
            if len(results) >= 5:
                break
        return results or samples[:3]

    def tool_get_relevant_vocabulary(self, concept: str = "", limit: int = 20) -> List[Dict[str, Any]]:
        url = f"{self.fastapi_url}/relevant_vocab"
        return http_get_json(url, {"user_id": self.user_id, "concept": concept, "limit": limit})

    def tool_set_user_level(self, level: str) -> str:
        self.memory['level'] = level
        save_memory(self.user_id, self.memory)
        return f"Level set to {level}"

    def tool_insert_vocabulary_progress(self, vocab_word: str, mastery_score: float, times_reviewed: int) -> str:
        url = f"{self.fastapi_url}/vocab"
        http_post_json(url, {
            "user_id": self.user_id,
            "vocab_word": vocab_word,
            "times_reviewed": times_reviewed,
            "mastery_score": mastery_score,
        })
        return "ok"

    def tool_insert_concept_mastery(self, concept_name: str) -> str:
        url = f"{self.fastapi_url}/concepts"
        http_post_json(url, {
            "user_id": self.user_id,
            "concept_name": concept_name,
        })
        return "ok"

    # Quiz tools
    def tool_start_quiz(self, mode: str = "global", count: int = 10,
                        book: Optional[str] = None, chapter: Optional[int] = None,
                        normalize: bool = True) -> Dict[str, Any]:
        b = canonical_book(book) if book else None
        ch = chapter if chapter is not None else None
        if mode == 'chapter' and (not b or ch is None):
            return {"error": "chapter mode requires book and chapter"}
        if mode == 'book' and not b:
            return {"error": "book mode requires book"}

        data = load_gnt()
        if not data:
            data = load_gnt_samples_as_full()
        if not data:
            return {"error": "GNT dataset not available"}

        if mode == 'global':
            freq = frequency_gnt(data=data, normalize=normalize)
        elif mode == 'book':
            freq = frequency_gnt(data=data, book=b, normalize=normalize)
        else:
            freq = frequency_gnt(data=data, book=b, chapter=ch, normalize=normalize)
        if not freq:
            return {"error": "no words found for scope"}
        # Exclude obviously non-lexical tokens (very short? still allow particles of length>=1)
        items = sorted(freq.items(), key=lambda kv: kv[1], reverse=True)
        queue: List[str] = []
        for w, _f in items:
            if any(c.isdigit() for c in w):
                continue
            if len(queue) >= count:
                break
            queue.append(w)

        mem = load_memory(self.user_id)
        mem['quiz'] = {
            'active': True,
            'mode': mode,
            'book': b,
            'chapter': ch,
            'normalize': normalize,
            'queue': queue,
            'asked': 0,
            'correct': 0,
            'total': len(queue),
            'current': None,
        }
        save_memory(self.user_id, mem)
        return {"status": "started", "total": len(queue), "mode": mode, "book": b, "chapter": ch}

    def tool_next_quiz_question(self) -> Dict[str, Any]:
        mem = load_memory(self.user_id)
        q = mem.get('quiz') or {}
        if not q.get('active'):
            return {"error": "no active quiz"}
        if not q.get('queue'):
            return {"done": True, "message": "Quiz complete."}
        token = q['queue'].pop(0)
        # Ask LLM for common glosses
        r = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "Given a Koine Greek token from the NT, list 3-6 common English glosses (lowercase), short words/phrases only. Reply as JSON: {\"glosses\":[...]}"},
                {"role": "user", "content": f"Token: {token}"},
            ],
            temperature=0.2,
        )
        glosses: List[str] = []
        try:
            payload = json.loads(r.choices[0].message.content or "{}")
            glosses = [str(x).lower().strip() for x in payload.get('glosses', []) if str(x).strip()]
        except Exception:
            glosses = []
        if not glosses:
            glosses = ["unknown"]
        q['current'] = {"token": token, "glosses": glosses}
        mem['quiz'] = q
        save_memory(self.user_id, mem)
        return {"question": f"What does '{token}' mean?", "token": token, "glosses_hint": glosses[:1]}

    def tool_grade_quiz_answer(self, user_answer: str) -> Dict[str, Any]:
        mem = load_memory(self.user_id)
        q = mem.get('quiz') or {}
        curr = q.get('current') or {}
        token = curr.get('token')
        glosses = curr.get('glosses') or []
        if not token:
            return {"error": "no current question"}
        ua = (user_answer or "").strip()
        # Ask LLM to judge correctness
        prompt = (
            "You are grading a vocab quiz. Compare the user's answer to acceptable glosses. "
            "Return JSON: {\"verdict\": \"correct|partial|incorrect\", \"explanation\": "
            "short rationale}. Be lenient with synonyms."
        )
        r = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps({"token": token, "glosses": glosses, "answer": ua})},
            ],
            temperature=0.0,
        )
        verdict = "incorrect"
        expl = ""
        try:
            payload = json.loads(r.choices[0].message.content or "{}")
            verdict = (payload.get('verdict') or 'incorrect').lower()
            expl = payload.get('explanation') or ''
        except Exception:
            pass
        # Update stats
        q['asked'] = int(q.get('asked') or 0) + 1
        if verdict == 'correct':
            q['correct'] = int(q.get('correct') or 0) + 1
            mastery_delta = 0.05
        elif verdict == 'partial':
            mastery_delta = 0.02
        else:
            mastery_delta = -0.02
        # Update DB review for token
        try:
            http_post_json(f"{self.fastapi_url}/vocab/increment_review", {
                "user_id": self.user_id,
                "vocab_word": token,
                "mastery_delta": mastery_delta,
            })
        except Exception:
            pass
        q['current'] = None
        mem['quiz'] = q
        save_memory(self.user_id, mem)
        return {"verdict": verdict, "explanation": expl, "asked": q['asked'], "correct": q['correct'], "remaining": len(q.get('queue') or [])}

    def tool_end_quiz(self) -> Dict[str, Any]:
        mem = load_memory(self.user_id)
        q = mem.get('quiz') or {}
        asked = int(q.get('asked') or 0)
        correct = int(q.get('correct') or 0)
        total = int(q.get('total') or asked)
        mem['quiz'] = {"active": False}
        save_memory(self.user_id, mem)
        return {"status": "ended", "asked": asked, "correct": correct, "total": total}

    # Chat session helpers
    def _new_session_id(self) -> str:
        return datetime.utcnow().strftime('%Y%m%d%H%M%S%f')

    def new_session(self) -> str:
        mem = load_memory(self.user_id)
        sid = self._new_session_id()
        mem.setdefault('sessions', [])
        mem['sessions'].append({
            'id': sid,
            'created_at': datetime.utcnow().isoformat(),
            'updated_at': datetime.utcnow().isoformat(),
            'messages': [],
            'summary': None,
            'title': None,
        })
        mem['active_session_id'] = sid
        save_memory(self.user_id, mem)
        self.memory = mem
        return sid

    def set_active_session(self, sid: str) -> bool:
        mem = load_memory(self.user_id)
        if not any(s.get('id') == sid for s in mem.get('sessions', [])):
            return False
        mem['active_session_id'] = sid
        save_memory(self.user_id, mem)
        self.memory = mem
        return True

    def list_sessions(self) -> List[Dict[str, Any]]:
        mem = load_memory(self.user_id)
        return list(reversed(sorted(mem.get('sessions', []), key=lambda s: s.get('updated_at') or s.get('created_at') or '')))

    def summarize_session(self, sid: str) -> Optional[str]:
        mem = load_memory(self.user_id)
        sess = next((s for s in mem.get('sessions', []) if s.get('id') == sid), None)
        if not sess:
            return None
        msgs = sess.get('messages', [])
        # Build a compact transcript
        transcript = []
        for m in msgs[-20:]:
            role = m.get('role')
            content = m.get('content') or ''
            transcript.append(f"{role}: {content}")
        prompt = (
            "Summarize this tutoring chat in one short sentence (<=12 words) highlighting topic or goal. "
            "Return only the summary text, no quotes.\n\n" + "\n".join(transcript)
        )
        r = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "You write brief, helpful chat summaries."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
        )
        summary = (r.choices[0].message.content or '').strip()
        sess['summary'] = summary
        sess['updated_at'] = datetime.utcnow().isoformat()
        save_memory(self.user_id, mem)
        return summary

    def rename_session(self, sid: str, title: str) -> bool:
        mem = load_memory(self.user_id)
        title = (title or '').strip()
        changed = False
        for s in mem.get('sessions', []):
            if s.get('id') == sid:
                s['title'] = title or None
                s['updated_at'] = datetime.utcnow().isoformat()
                changed = True
                break
        if changed:
            save_memory(self.user_id, mem)
        return changed

    def delete_session(self, sid: str) -> bool:
        mem = load_memory(self.user_id)
        sessions = mem.get('sessions', [])
        new_sessions = [s for s in sessions if s.get('id') != sid]
        if len(new_sessions) == len(sessions):
            return False
        mem['sessions'] = new_sessions
        if mem.get('active_session_id') == sid:
            mem['active_session_id'] = None
        save_memory(self.user_id, mem)
        self.memory = mem
        return True

    # Bible tools
    def tool_get_gnt_verses(self, ref: str = "", book: str = "", chapter: Optional[int] = None, verses: Optional[List[int]] = None):
        data = load_gnt()
        if ref:
            parsed = parse_ref(ref)
            if not parsed:
                return []
            b, ch, vs = parsed
        else:
            b = canonical_book(book) if book else None
            ch = chapter if chapter is not None else 1
            vs = verses or []
        if not b or not ch or not vs:
            return []
        return get_verses(data, b, ch, vs, 'text_grc')

    def tool_get_kjv_verses(self, ref: str = "", book: str = "", chapter: Optional[int] = None, verses: Optional[List[int]] = None):
        data = load_kjv()
        if ref:
            parsed = parse_ref(ref)
            if not parsed:
                return []
            b, ch, vs = parsed
        else:
            b = canonical_book(book) if book else None
            ch = chapter if chapter is not None else 1
            vs = verses or []
        if not b or not ch or not vs:
            return []
        return get_verses(data, b, ch, vs, 'text_eng')

    def tool_explain_verse_alignment(self, ref: str) -> str:
        """Use LLM to explain word-by-word Greek→English mapping with reasons."""
        gnt = self.tool_get_gnt_verses(ref=ref)
        kjv = self.tool_get_kjv_verses(ref=ref)
        if not gnt or not kjv:
            return "Verses not found in local datasets. Make sure you've downloaded GNT and KJV."
        g_join = '\n'.join([f"{v['book']} {v['chapter']}:{v['verse']} — {v['text_grc']}" for v in gnt])
        k_join = '\n'.join([f"{v['book']} {v['chapter']}:{v['verse']} — {v['text_eng']}" for v in kjv])
        prompt = (
            "You are a Biblical Greek tutor. For the passage, provide a word-by-word alignment "
            "from the Greek text to the English KJV, explaining for each notable Greek word "
            "its typical gloss, morphology where helpful (e.g., case/number/tense), and why the KJV renders it so. "
            "Keep explanations concise, but complete. Then provide a brief translation check.\n\n"
            f"Greek (GNT):\n{g_join}\n\nKJV:\n{k_join}\n\n"
            "Format as a list: for each verse, list Greek tokens with short rationale."
        )
        r = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "You align Greek NT verses with KJV and explain translation choices succinctly."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
        )
        return r.choices[0].message.content or ""

    def tool_insert_user_interest(self, interest_type: str, topic: Optional[str] = None,
                                  book: Optional[str] = None, chapter: Optional[int] = None,
                                  passage_ref: Optional[str] = None) -> str:
        url = f"{self.fastapi_url}/interests"
        http_post_json(url, {
            "user_id": self.user_id,
            "interest_type": interest_type,
            "topic": topic,
            "book": canonical_book(book) if book else None,
            "chapter": chapter,
            "passage_ref": passage_ref,
        })
        return "ok"

    def tool_generate_and_insert_vocab(self, mode: str = "global", count: int = 20,
                                       book: Optional[str] = None, chapter: Optional[int] = None,
                                       normalize: bool = True) -> Dict[str, Any]:
        """
        Generate top-frequency Greek tokens from GNT and upsert them for this user.
        mode: 'global' (full NT), 'book', or 'chapter'. If 'book'/'chapter', provide book/chapter
        or these may be inferred later from interests in future iterations.
        """
        b = canonical_book(book) if book else None
        ch = chapter if (chapter is not None) else None
        if mode == 'chapter' and (not b or ch is None):
            return {"error": "chapter mode requires book and chapter"}
        if mode == 'book' and not b:
            return {"error": "book mode requires book"}

        # Load dataset (full if available, else small sample)
        data = load_gnt()
        source = 'full'
        if not data:
            data = load_gnt_samples_as_full()
            source = 'sample'
        if not data:
            return {"error": "GNT dataset not available. Run 'python scripts/download_texts.py' or place data/gnt_full.json."}

        # Compute frequency from chosen scope
        if mode == 'global':
            freq = frequency_gnt(data=data, book=None, chapter=None, normalize=normalize)
        elif mode == 'book':
            freq = frequency_gnt(data=data, book=b, chapter=None, normalize=normalize)
            if not freq:
                return {"error": f"No verses found for book '{b}' in dataset ({source})."}
        else:  # chapter
            freq = frequency_gnt(data=data, book=b, chapter=ch, normalize=normalize)
            if not freq:
                return {"error": f"No verses found for {b} {ch} in dataset ({source})."}

        # Current user vocab to avoid duplicates (normalize if needed)
        existing = http_get_json(f"{self.fastapi_url}/vocab/{self.user_id}")
        have = {row.get('vocab_word') for row in existing}
        if normalize:
            have_norm = {strip_diacritics((w or '').lower()) for w in have}
        else:
            have_norm = set()

        # Sort by frequency desc
        items = sorted(freq.items(), key=lambda kv: kv[1], reverse=True)
        selected: List[str] = []
        for w, f in items:
            if w in have:
                continue
            if normalize and strip_diacritics(w.lower()) in have_norm:
                continue
            selected.append(w)
            if len(selected) >= count:
                break

        # Insert selected with initial mastery 0
        for w in selected:
            self.tool_insert_vocabulary_progress(vocab_word=w, mastery_score=0.0, times_reviewed=0)
        # Log vocab set and items for dashboard
        try:
            rec = {
                "user_id": self.user_id,
                "mode": mode,
                "book": b,
                "chapter": ch,
                "count_requested": count,
                "count_inserted": len(selected),
                "source": source,
            }
            resp = http_post_json(f"{self.fastapi_url}/vocab_sets", rec)
            set_id = resp.get('id')
            if set_id and selected:
                http_post_json(f"{self.fastapi_url}/vocab_set_items", {
                    "user_id": self.user_id,
                    "set_id": set_id,
                    "words": selected,
                })
        except Exception:
            pass

        return {"inserted": selected, "count": len(selected), "mode": mode, "book": b, "chapter": ch, "source": source}

    def tool_start_quiz_from_words(self, words: List[str], count: Optional[int] = None) -> Dict[str, Any]:
        unique = []
        seen = set()
        for w in words or []:
            if not w:
                continue
            key = strip_diacritics(w.lower())
            if key in seen:
                continue
            seen.add(key)
            unique.append(w)
        if count is not None:
            unique = unique[: max(0, int(count))]
        mem = load_memory(self.user_id)
        mem['quiz'] = {
            'active': True,
            'mode': 'custom',
            'book': None,
            'chapter': None,
            'normalize': True,
            'queue': unique,
            'asked': 0,
            'correct': 0,
            'total': len(unique),
            'current': None,
        }
        save_memory(self.user_id, mem)
        return {"status": "started", "total": len(unique)}

    def tool_gloss_tokens(self, words: List[str]) -> Dict[str, List[str]]:
        # Batch gloss suggestion for display; return mapping token -> glosses
        tokens = [w for w in (words or []) if w]
        if not tokens:
            return {}
        # 1) Check cache
        cached = {}
        try:
            qs = ','.join(tokens)
            rows = http_get_json(f"{self.fastapi_url}/glosses", {"words": qs})
            for r in rows:
                w = r.get('word')
                gl = r.get('glosses') or []
                if w and gl:
                    cached[w] = [str(x).lower().strip() for x in gl if str(x).strip()]
        except Exception:
            cached = {}
        missing = [t for t in tokens if t not in cached]
        result = dict(cached)
        if missing:
            # 2) Query LLM for missing tokens
            prompt = (
                "For each Koine Greek token, provide 2-5 common English glosses (lowercase). "
                "Return strictly JSON mapping tokens to lists, e.g., {\"λόγος\":[\"word\",\"message\"]}."
            )
            r = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps({"tokens": missing}, ensure_ascii=False)},
                ],
                temperature=0.2,
            )
            new_map: Dict[str, List[str]] = {}
            try:
                data = json.loads(r.choices[0].message.content or "{}")
                for k, v in (data.items() if isinstance(data, dict) else []):
                    if isinstance(v, list):
                        new_map[str(k)] = [str(x).lower().strip() for x in v if str(x).strip()]
            except Exception:
                new_map = {}
            if new_map:
                result.update(new_map)
                # 3) Upsert to cache
                try:
                    entries = [{"word": k, "glosses": v} for k, v in new_map.items()]
                    http_post_json(f"{self.fastapi_url}/glosses", {"entries": entries})
                except Exception:
                    pass
        return result

    # Tool schemas for function calling
    def tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "explain_concept",
                    "description": "Explain a Koine Greek concept tailored to a level.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "concept": {"type": "string"},
                            "level": {"type": "string", "nullable": True},
                        },
                        "required": ["concept"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "provide_gnt_examples",
                    "description": "Return example verses from a small GNT sample matching a query.",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_relevant_vocabulary",
                    "description": "Fetch relevant vocabulary for the user using the DB API.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "concept": {"type": "string"},
                            "limit": {"type": "integer"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "set_user_level",
                    "description": "Set and persist the user's Koine Greek level. Only call this if the user explicitly requests to set/change level (e.g., 'Set my level to beginner', 'I am B1'). Do NOT infer from quiz answers.",
                    "parameters": {
                        "type": "object",
                        "properties": {"level": {"type": "string"}},
                        "required": ["level"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "insert_vocabulary_progress",
                    "description": "Insert or update a vocabulary progress row for the user.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "vocab_word": {"type": "string"},
                            "mastery_score": {"type": "number"},
                            "times_reviewed": {"type": "integer"},
                        },
                        "required": ["vocab_word", "mastery_score", "times_reviewed"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "insert_concept_mastery",
                    "description": "Insert a mastered concept for the user.",
                    "parameters": {
                        "type": "object",
                        "properties": {"concept_name": {"type": "string"}},
                        "required": ["concept_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_gnt_verses",
                    "description": "Fetch specific verses from Greek NT (requires downloaded dataset).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "ref": {"type": "string", "description": "e.g., 'John 1:1-3'"},
                            "book": {"type": "string"},
                            "chapter": {"type": "integer"},
                            "verses": {"type": "array", "items": {"type": "integer"}},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_kjv_verses",
                    "description": "Fetch specific verses from English KJV NT (requires downloaded dataset).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "ref": {"type": "string", "description": "e.g., 'John 1:1-3'"},
                            "book": {"type": "string"},
                            "chapter": {"type": "integer"},
                            "verses": {"type": "array", "items": {"type": "integer"}},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "explain_verse_alignment",
                    "description": "Explain Greek-to-English (KJV) mapping for a verse reference.",
                    "parameters": {
                        "type": "object",
                        "properties": {"ref": {"type": "string"}},
                        "required": ["ref"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "insert_user_interest",
                    "description": "Record a user's interest (topic/book/chapter/passage).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "interest_type": {"type": "string", "enum": ["topic", "book", "chapter", "passage"]},
                            "topic": {"type": "string"},
                            "book": {"type": "string"},
                            "chapter": {"type": "integer"},
                            "passage_ref": {"type": "string"}
                        },
                        "required": ["interest_type"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "generate_and_insert_vocab",
                    "description": "Generate high-frequency Greek tokens from NT (global/book/chapter) and insert into user's vocab DB.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "mode": {"type": "string", "enum": ["global", "book", "chapter"], "default": "global"},
                            "count": {"type": "integer", "default": 20},
                            "book": {"type": "string"},
                            "chapter": {"type": "integer"},
                            "normalize": {"type": "boolean", "default": True}
                        }
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "start_quiz",
                    "description": "Start a lightweight vocab quiz over Greek tokens (global/book/chapter).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "mode": {"type": "string", "enum": ["global", "book", "chapter"], "default": "global"},
                            "count": {"type": "integer", "default": 10},
                            "book": {"type": "string"},
                            "chapter": {"type": "integer"},
                            "normalize": {"type": "boolean", "default": True}
                        }
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "start_quiz_from_words",
                    "description": "Start a vocab quiz from an explicit list of Greek tokens.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "words": {"type": "array", "items": {"type": "string"}},
                            "count": {"type": "integer"}
                        },
                        "required": ["words"]
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "next_quiz_question",
                    "description": "Advance to the next quiz question and return it.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "grade_quiz_answer",
                    "description": "Grade the user's answer for the current quiz question.",
                    "parameters": {"type": "object", "properties": {"user_answer": {"type": "string"}}, "required": ["user_answer"]},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "end_quiz",
                    "description": "End the quiz and return a summary.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]

    def handle_tool_call(self, name: str, arguments: Dict[str, Any]) -> str:
        if name == 'explain_concept':
            return json.dumps({"explanation": self.tool_explain_concept(arguments.get('concept', ''), arguments.get('level'))})
        if name == 'provide_gnt_examples':
            return json.dumps({"examples": self.tool_provide_gnt_examples(arguments.get('query', ''))})
        if name == 'get_relevant_vocabulary':
            return json.dumps({"vocabulary": self.tool_get_relevant_vocabulary(arguments.get('concept', ''), arguments.get('limit', 20))})
        if name == 'set_user_level':
            level_arg = (arguments.get('level', '') or '').strip()
            if not self._explicit_level_request(self._last_user_text, level_arg):
                return json.dumps({"status": "ignored", "reason": "no explicit level request"})
            return json.dumps({"status": self.tool_set_user_level(level_arg)})
        if name == 'insert_vocabulary_progress':
            return json.dumps({"status": self.tool_insert_vocabulary_progress(arguments.get('vocab_word', ''), float(arguments.get('mastery_score', 0)), int(arguments.get('times_reviewed', 0)))})
        if name == 'insert_concept_mastery':
            return json.dumps({"status": self.tool_insert_concept_mastery(arguments.get('concept_name', ''))})
        if name == 'get_gnt_verses':
            return json.dumps({"verses": self.tool_get_gnt_verses(
                arguments.get('ref', ''), arguments.get('book', ''), arguments.get('chapter'), arguments.get('verses'))})
        if name == 'get_kjv_verses':
            return json.dumps({"verses": self.tool_get_kjv_verses(
                arguments.get('ref', ''), arguments.get('book', ''), arguments.get('chapter'), arguments.get('verses'))})
        if name == 'explain_verse_alignment':
            return json.dumps({"explanation": self.tool_explain_verse_alignment(arguments.get('ref', ''))})
        if name == 'start_quiz':
            return json.dumps(self.tool_start_quiz(
                arguments.get('mode', 'global'), int(arguments.get('count', 10)), arguments.get('book'), arguments.get('chapter'), bool(arguments.get('normalize', True))
            ))
        if name == 'start_quiz_from_words':
            return json.dumps(self.tool_start_quiz_from_words(arguments.get('words', []), arguments.get('count')))
        if name == 'next_quiz_question':
            return json.dumps(self.tool_next_quiz_question())
        if name == 'grade_quiz_answer':
            return json.dumps(self.tool_grade_quiz_answer(arguments.get('user_answer', '')))
        if name == 'end_quiz':
            return json.dumps(self.tool_end_quiz())
        if name == 'insert_user_interest':
            return json.dumps({"status": self.tool_insert_user_interest(
                arguments.get('interest_type', ''), arguments.get('topic'), arguments.get('book'), arguments.get('chapter'), arguments.get('passage_ref'))})
        if name == 'generate_and_insert_vocab':
            return json.dumps(self.tool_generate_and_insert_vocab(
                arguments.get('mode', 'global'), int(arguments.get('count', 20)), arguments.get('book'), arguments.get('chapter'), bool(arguments.get('normalize', True))
            ))
        return json.dumps({"error": f"unknown tool {name}"})

    def chat(self, user_text: str) -> str:
        # Load latest memory to include any external updates
        self.memory = load_memory(self.user_id)
        system = (
            "You are a friendly, concise Biblical Greek tutor. "
            "You can call tools to explain concepts, show Greek NT examples, retrieve/store vocab progress, record interests, and run a lightweight vocab quiz. "
            "Quiz flow: when the user asks to be quizzed, call start_quiz with appropriate scope, then next_quiz_question, then on user reply call grade_quiz_answer, then either next_quiz_question or end_quiz if done. "
            "Only call set_user_level if the user explicitly requests a level change. Do not infer from quiz answers."
        )
        level = self.memory.get('level')
        memory_preamble = f"Student level: {level}." if level else "Student level unknown; you may ask."
        self._last_user_text = user_text or ""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": memory_preamble},
            {"role": "user", "content": user_text},
        ]

        # Re-usable loop to process tool calls
        for _ in range(3):
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=self.tool_schemas(),
                tool_choice="auto",
                temperature=0.4,
            )
            msg = resp.choices[0].message
            if msg.tool_calls:
                # Execute each tool call and append results
                messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
                    {"id": tc.id, "type": tc.type, "function": {"name": tc.function.name, "arguments": tc.function.arguments}} for tc in msg.tool_calls
                ]})
                for tc in msg.tool_calls:
                    args = json.loads(tc.function.arguments or "{}")
                    result = self.handle_tool_call(tc.function.name, args)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.function.name,
                        "content": result,
                    })
                # Continue the loop to let the model incorporate tool results
                continue
            # Final answer
            answer = msg.content or ""
            # Save into active session
            mem = load_memory(self.user_id)
            # ensure session exists
            sid = mem.get('active_session_id')
            if not sid:
                sid = self.new_session()
                mem = load_memory(self.user_id)
            # append messages
            for s in mem.get('sessions', []):
                if s.get('id') == sid:
                    msgs = s.get('messages', [])
                    msgs.append({"role": "user", "content": user_text, "ts": datetime.utcnow().isoformat()})
                    msgs.append({"role": "assistant", "content": answer, "ts": datetime.utcnow().isoformat()})
                    s['messages'] = msgs[-50:]
                    s['updated_at'] = datetime.utcnow().isoformat()
                    break
            save_memory(self.user_id, mem)
            return answer
        # Fallback if no final message
        return "Let’s continue. What would you like to learn in Koine Greek today?"

    def _explicit_level_request(self, text: str, level_arg: str) -> bool:
        """Return True if the text explicitly asks to set/change level or states it clearly."""
        if not text:
            return False
        t = text.lower()
        # Common explicit phrases
        patterns = [
            r"\bset (my )?level to\b",
            r"\bchange (my )?level to\b",
            r"\bmy level is\b",
            r"\bi am (a |an )?(beginner|intermediate|advanced)\b",
            r"\bi'm (a |an )?(beginner|intermediate|advanced)\b",
            r"\bi am (a1|a2|b1|b2|c1|c2)\b",
            r"\bi'm (a1|a2|b1|b2|c1|c2)\b",
        ]
        if any(re.search(p, t) for p in patterns):
            return True
        # If the model provided a level arg, ensure it's actually mentioned
        if level_arg and level_arg.lower() in t:
            # e.g., "Set my level to Beginner"
            return True
        return False
