import json
import os
from typing import List, Dict, Any, Optional, Tuple
import re
import unicodedata

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')

BOOK_MAP = {
    # Canonical names used in data sources
    'matthew': 'Matthew',
    'mark': 'Mark',
    'luke': 'Luke',
    'john': 'John',
    'acts': 'Acts',
    'romans': 'Romans',
    '1 corinthians': '1 Corinthians', '1corinthians': '1 Corinthians', 'i corinthians': '1 Corinthians',
    '2 corinthians': '2 Corinthians', '2corinthians': '2 Corinthians', 'ii corinthians': '2 Corinthians',
    'galatians': 'Galatians',
    'ephesians': 'Ephesians',
    'philippians': 'Philippians',
    'colossians': 'Colossians',
    '1 thessalonians': '1 Thessalonians', '1thessalonians': '1 Thessalonians', 'i thessalonians': '1 Thessalonians',
    '2 thessalonians': '2 Thessalonians', '2thessalonians': '2 Thessalonians', 'ii thessalonians': '2 Thessalonians',
    '1 timothy': '1 Timothy', '1timothy': '1 Timothy', 'i timothy': '1 Timothy',
    '2 timothy': '2 Timothy', '2timothy': '2 Timothy', 'ii timothy': '2 Timothy',
    'titus': 'Titus',
    'philemon': 'Philemon',
    'hebrews': 'Hebrews',
    'james': 'James',
    '1 peter': '1 Peter', '1peter': '1 Peter', 'i peter': '1 Peter',
    '2 peter': '2 Peter', '2peter': '2 Peter', 'ii peter': '2 Peter',
    '1 john': '1 John', '1john': '1 John', 'i john': '1 John',
    '2 john': '2 John', '2john': '2 John', 'ii john': '2 John',
    '3 john': '3 John', '3john': '3 John', 'iii john': '3 John',
    'jude': 'Jude',
    'revelation': 'Revelation', 'apocalypse': 'Revelation', 'revelation of john': 'Revelation',
}


def canonical_book(name: str) -> Optional[str]:
    if not name:
        return None
    key = name.strip().lower().replace('.', '')
    return BOOK_MAP.get(key)


def load_json(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_gnt() -> List[Dict[str, Any]]:
    return load_json(os.path.join(DATA_DIR, 'gnt_full.json'))


def load_kjv() -> List[Dict[str, Any]]:
    return load_json(os.path.join(DATA_DIR, 'kjv_nt.json'))


def load_gnt_samples_as_full() -> List[Dict[str, Any]]:
    """Load small samples and coerce into full-verse schema for fallback frequency.
    Expected sample entries: {ref, grc, eng}
    Output schema: {book, chapter, verse, text_grc}
    """
    path = os.path.join(DATA_DIR, 'gnt_samples.json')
    samples = load_json(path)
    out: List[Dict[str, Any]] = []
    for s in samples:
        ref = s.get('ref', '')
        parsed = parse_ref(ref)
        if not parsed:
            continue
        book, chapter, verses = parsed
        for v in verses:
            out.append({'book': book, 'chapter': chapter, 'verse': v, 'text_grc': s.get('grc', '')})
    return out


def get_verses(data: List[Dict[str, Any]], book: str, chapter: int, verses: List[int], text_key: str) -> List[Dict[str, Any]]:
    out = []
    for row in data:
        if row.get('book') == book and int(row.get('chapter', 0)) == chapter and int(row.get('verse', 0)) in verses:
            out.append({'book': book, 'chapter': chapter, 'verse': int(row['verse']), text_key: row[text_key]})
    out.sort(key=lambda r: r['verse'])
    return out


def parse_ref(ref: str) -> Optional[Tuple[str, int, List[int]]]:
    """Parse strings like 'John 1:1-3,5' into (book, chapter, [verses])."""
    if not ref:
        return None
    parts = ref.strip().split()
    if len(parts) < 2:
        return None
    book = canonical_book(' '.join(parts[:-1]))
    cv = parts[-1]
    if not book or ':' not in cv:
        return None
    ch_s, vs_s = cv.split(':', 1)
    try:
        chapter = int(ch_s)
    except Exception:
        return None
    verses = []
    for seg in vs_s.split(','):
        if '-' in seg:
            a, b = seg.split('-', 1)
            try:
                a, b = int(a), int(b)
            except Exception:
                continue
            verses.extend(list(range(min(a, b), max(a, b)+1)))
        else:
            try:
                verses.append(int(seg))
            except Exception:
                continue
    verses = sorted(set(verses))
    return (book, chapter, verses) if verses else None


_GREEK_RE = re.compile(r"[\u0370-\u03FF\u1F00-\u1FFF]+", re.UNICODE)


def strip_diacritics(text: str) -> str:
    # Normalize to NFD and drop combining marks
    norm = unicodedata.normalize('NFD', text)
    return ''.join(ch for ch in norm if not unicodedata.combining(ch))


def tokenize_grc(text: str, normalize: bool = True) -> List[str]:
    if not text:
        return []
    # Keep only Greek runs; split by non-Greek
    tokens = _GREEK_RE.findall(text)
    out: List[str] = []
    for t in tokens:
        t2 = t.lower()
        if normalize:
            t2 = strip_diacritics(t2)
        if t2:
            out.append(t2)
    return out


def frequency_gnt(data: Optional[List[Dict[str, Any]]] = None,
                  book: Optional[str] = None,
                  chapter: Optional[int] = None,
                  normalize: bool = True) -> Dict[str, int]:
    if data is None:
        data = load_gnt()
    counts: Dict[str, int] = {}
    for row in data:
        if book and row.get('book') != book:
            continue
        if chapter is not None and int(row.get('chapter', 0)) != chapter:
            continue
        for tok in tokenize_grc(row.get('text_grc', ''), normalize=normalize):
            counts[tok] = counts.get(tok, 0) + 1
    return counts
