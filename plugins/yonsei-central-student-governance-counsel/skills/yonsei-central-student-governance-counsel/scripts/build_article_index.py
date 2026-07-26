#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from _common import (
    INDEX_PATH,
    MANIFEST_PATH,
    load_json,
    normalize_text,
    resolve_skill_path,
    sha256_bytes,
    utc_now,
    write_jsonl,
)

ARTICLE_RE = re.compile(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?\s*\(([^)]{1,120})\)")


def reader_class():
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("pypdf is required to build the corpus") from exc
    return PdfReader


def extract_segment(document: dict) -> tuple[str, list[tuple[int, str]]]:
    reader = reader_class()(resolve_skill_path(document["local_pdf"]))
    start = int(document["page_start"])
    end = int(document["page_end"])
    if start < 1 or end > len(reader.pages) or start > end:
        raise ValueError(f"{document['document_id']}: invalid page range {start}-{end}/{len(reader.pages)}")
    page_texts: list[tuple[int, str]] = []
    for page_number in range(start, end + 1):
        text = reader.pages[page_number - 1].extract_text() or ""
        page_texts.append((page_number, normalize_text(text)))
    return "\n\f\n".join(text for _, text in page_texts), page_texts


def page_for_offset(page_texts: list[tuple[int, str]], offset: int) -> int | None:
    cursor = 0
    for page_number, text in page_texts:
        cursor += len(text) + 3
        if offset < cursor:
            return page_number
    return page_texts[-1][0] if page_texts else None


def article_rows(document: dict, text: str, page_texts: list[tuple[int, str]]) -> list[dict]:
    matches = list(ARTICLE_RE.finditer(text))
    rows: list[dict] = []
    seen_ids: set[str] = set()
    retrieved_at = utc_now()
    for index, match in enumerate(matches):
        number = match.group(1)
        sub = match.group(2)
        article = f"제{number}조" + (f"의{sub}" if sub else "")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        full_text = normalize_text(text[match.start() : end])
        if len(full_text) < len(match.group(0)):
            continue
        row_id = f"{document['document_id']}:{number}" + (f"-{sub}" if sub else "")
        page = page_for_offset(page_texts, match.start())
        if row_id in seen_ids:
            base_id = f"{row_id}@p{page}"
            row_id = base_id
            occurrence = 2
            while row_id in seen_ids:
                row_id = f"{base_id}-{occurrence}"
                occurrence += 1
        seen_ids.add(row_id)
        rows.append(
            {
                "id": row_id,
                "document_id": document["document_id"],
                "document_title": document["title"],
                "domain": document["domain"],
                "target": document["target"],
                "article": article,
                "title": match.group(3).strip(),
                "full_text": full_text,
                "source_url": document["source_page_url"],
                "download_url": document["download_url"],
                "page": page,
                "effective_from": document.get("effective_from"),
                "effective_to": document.get("effective_to"),
                "status": document["status"],
                "quality": document["quality"],
                "content_verified": True,
                "retrieved_at": retrieved_at,
                "content_sha256": sha256_bytes(full_text.encode("utf-8")),
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract official PDFs and build an article-level JSONL index.")
    parser.add_argument("--index", type=Path, default=INDEX_PATH)
    args = parser.parse_args()
    manifest = load_json(MANIFEST_PATH)
    rows: list[dict] = []
    for document in manifest["documents"]:
        text, page_texts = extract_segment(document)
        text_path = resolve_skill_path(document["local_text"])
        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(text + "\n", encoding="utf-8")
        extracted = article_rows(document, text, page_texts)
        if not extracted:
            raise RuntimeError(f"{document['document_id']}: no articles extracted")
        rows.extend(extracted)
    identifiers = [row["id"] for row in rows]
    if len(identifiers) != len(set(identifiers)):
        duplicates = sorted({item for item in identifiers if identifiers.count(item) > 1})
        raise RuntimeError(f"duplicate article identifiers: {duplicates}")
    write_jsonl(args.index, rows)
    print({"index": str(args.index), "documents": len(manifest["documents"]), "articles": len(rows)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
