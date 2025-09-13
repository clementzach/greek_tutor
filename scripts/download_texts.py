#!/usr/bin/env python3
"""
Downloads the full Greek New Testament (GNT) and King James Version (KJV) NT
into data/ as JSON verse lists. Prefers LogosBible SBLGNT per-book sources.

Notes:
- Sources are public/open. If a URL fails, try the next fallback or provide
  your own via CLI args.
- Output:
  - data/gnt_full.json  -> [{book, chapter, verse, text_grc}]
  - data/kjv_nt.json    -> [{book, chapter, verse, text_eng}]

Usage:
  python scripts/download_texts.py
  python scripts/download_texts.py --gnt-url <custom verse-per-line url> --kjv-url <full bible json url>
"""
import argparse
import json
import os
import sys
import urllib.request
import urllib.error

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')


def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)


def fetch(url: str) -> str:
    with urllib.request.urlopen(url) as resp:
        data = resp.read()
        # Decode using utf-8-sig to gracefully strip BOM if present
        try:
            return data.decode('utf-8-sig')
        except Exception:
            return data.decode('utf-8')


def save_json(path: str, obj):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def parse_kjv_json(raw_json: str):
    """
    Expecting structure like thiagobodruk/bible JSON with full Bible.
    We filter for NT books only and flatten to {book, chapter, verse, text_eng}.
    """
    data = json.loads(raw_json)
    nt_books = {
        'Matthew','Mark','Luke','John','Acts','Romans','1 Corinthians','2 Corinthians','Galatians',
        'Ephesians','Philippians','Colossians','1 Thessalonians','2 Thessalonians','1 Timothy','2 Timothy',
        'Titus','Philemon','Hebrews','James','1 Peter','2 Peter','1 John','2 John','3 John','Jude','Revelation'
    }
    out = []
    for book in data:
        name = book.get('name') or book.get('book') or ''
        if name not in nt_books:
            continue
        chapters = book.get('chapters', [])
        for ci, ch in enumerate(chapters, start=1):
            for vi, verse in enumerate(ch, start=1):
                out.append({
                    'book': name,
                    'chapter': ci,
                    'verse': vi,
                    'text_eng': verse,
                })
    return out


def parse_gnt_lines_to_json(raw_text: str):
    """
    Parse a simple verse-per-line GNT format like:
      Matthew 1:1 <tab or space> Greek text...
    and output as list of {book, chapter, verse, text_grc}.
    The exact format may vary by source; we implement a robust splitter.
    """
    out = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Try to split on first 2 tokens: BookName Chapter:Verse
        # Book name may include spaces; so find first token that contains ':'
        parts = line.split()
        ref_idx = None
        for i, tok in enumerate(parts):
            if ':' in tok:
                ref_idx = i
                break
        if ref_idx is None:
            continue
        book = ' '.join(parts[:ref_idx-0])
        ref = parts[ref_idx]
        text = ' '.join(parts[ref_idx+1:]).strip()
        try:
            ch_str, vs_str = ref.split(':', 1)
            chapter = int(ch_str)
            verse = int(''.join([c for c in vs_str if c.isdigit()]))
        except Exception:
            # If parsing fails, skip line
            continue
        if book and chapter and verse and text:
            out.append({'book': book, 'chapter': chapter, 'verse': verse, 'text_grc': text})
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--gnt-url', default=None, help='URL to Greek NT (verse-per-line).')
    parser.add_argument('--kjv-url', default=None, help='URL to KJV JSON (full Bible).')
    args = parser.parse_args()

    ensure_dirs()

    # Potential sources (public):
    # KJV JSON (full Bible): https://raw.githubusercontent.com/thiagobodruk/bible/master/json/kjv.json
    kjv_urls = [
        args.kjv_url,
        'https://raw.githubusercontent.com/thiagobodruk/bible/master/json/en_kjv.json',
    ]
    kjv_urls = [u for u in kjv_urls if u]

    # Download KJV JSON and filter to NT
    kjv_out_path = os.path.join(DATA_DIR, 'kjv_nt.json')
    for url in kjv_urls:
        try:
            print(f'Downloading KJV from {url}...')
            raw = fetch(url)
            verses = parse_kjv_json(raw)
            if verses:
                save_json(kjv_out_path, verses)
                print(f'Saved {kjv_out_path} with {len(verses)} verses.')
                break
        except Exception as e:
            print(f'KJV download failed from {url}: {e}')
    else:
        print('Failed to download KJV. Provide --kjv-url or place data/kjv_nt.json manually.')

    # Download GNT (SBLGNT per-book) or custom single file if provided
    gnt_out_path = os.path.join(DATA_DIR, 'gnt_full.json')

    # If custom URL provided, try that first
    if args.gnt_url:
        try:
            print(f'Downloading GNT (custom) from {args.gnt_url}...')
            raw = fetch(args.gnt_url)
            verses = parse_gnt_lines_to_json(raw)
            if verses:
                save_json(gnt_out_path, verses)
                print(f'Saved {gnt_out_path} with {len(verses)} verses.')
                return
            else:
                print('Custom GNT URL parsed but yielded 0 verses; falling back to SBLGNT per-book.')
        except Exception as e:
            print(f'GNT custom download failed: {e}. Falling back to SBLGNT per-book...')

    # SBLGNT per-book files from LogosBible
    sbl_base = 'https://raw.githubusercontent.com/LogosBible/SBLGNT/master/data/sblgnt/text'
    sbl_codes = [
        'Matt','Mark','Luke','John','Acts','Rom','1Cor','2Cor','Gal','Eph','Phil','Col',
        '1Thess','2Thess','1Tim','2Tim','Titus','Phlm','Heb','Jas','1Pet','2Pet','1John','2John','3John','Jude','Rev'
    ]
    abb_map = {
        'Matt':'Matthew','Mark':'Mark','Luke':'Luke','John':'John','Acts':'Acts','Rom':'Romans',
        '1Cor':'1 Corinthians','2Cor':'2 Corinthians','Gal':'Galatians','Eph':'Ephesians','Phil':'Philippians','Col':'Colossians',
        '1Thess':'1 Thessalonians','2Thess':'2 Thessalonians','1Tim':'1 Timothy','2Tim':'2 Timothy',
        'Titus':'Titus','Phlm':'Philemon','Heb':'Hebrews','Jas':'James','1Pet':'1 Peter','2Pet':'2 Peter',
        '1John':'1 John','2John':'2 John','3John':'3 John','Jude':'Jude','Rev':'Revelation'
    }

    def parse_sblgnt_book(raw_text: str, abbrev: str):
        lines = raw_text.splitlines()
        out = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if '\t' not in line:
                # Likely the book title line in Greek; skip
                continue
            try:
                ref_part, text = line.split('\t', 1)
            except ValueError:
                continue
            parts = ref_part.strip().split()
            if len(parts) != 2:
                continue
            ab, cv = parts
            if ':' not in cv:
                continue
            ch_s, vs_s = cv.split(':', 1)
            try:
                ch_i = int(ch_s)
                vs_i = int(''.join([c for c in vs_s if c.isdigit()]))
            except Exception:
                continue
            book_name = abb_map.get(abbrev)
            if not book_name:
                continue
            out.append({'book': book_name, 'chapter': ch_i, 'verse': vs_i, 'text_grc': text.strip()})
        return out

    all_verses = []
    for code in sbl_codes:
        url = f"{sbl_base}/{code}.txt"
        try:
            print(f'Downloading SBLGNT {code}...')
            raw = fetch(url)
            verses = parse_sblgnt_book(raw, code)
            print(f'  {code}: {len(verses)} verses')
            all_verses.extend(verses)
        except urllib.error.HTTPError as e:
            print(f'  {code}: HTTP error {e.code} at {url}')
        except Exception as e:
            print(f'  {code}: failed to parse: {e}')
    if all_verses:
        all_verses.sort(key=lambda r: (r['book'], r['chapter'], r['verse']))
        save_json(gnt_out_path, all_verses)
        print(f'Saved {gnt_out_path} with {len(all_verses)} verses from SBLGNT.')
    else:
        print('Failed to download any SBLGNT books. Provide --gnt-url or place data/gnt_full.json manually.')


if __name__ == '__main__':
    main()
