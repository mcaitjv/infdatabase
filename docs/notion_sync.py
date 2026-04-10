#!/usr/bin/env python3
"""
docs/notion_sync.py — Metodoloji dokümanını Notion'a senkronize eder.

Kullanım:
    python docs/notion_sync.py
    python docs/notion_sync.py --doc docs/inflation-database-methodology.md
"""

import io
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from notion_client import Client

# Windows terminal UTF-8 uyumu
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

load_dotenv()

PARENT_PAGE_ID = os.environ.get("NOTION_PARENT_PAGE_ID", "3367536c26a58008b46bd893844cff29")
DOC_PATH = Path(__file__).parent / "inflation-database-methodology.md"
PAGE_TITLE = "Türkiye Enflasyon Veritabanı — Metodoloji"
BATCH_SIZE = 100


# ─── Rich text ────────────────────────────────────────────────────────────────

def rich_text(text: str) -> list[dict]:
    """Inline markdown (**bold**, *italic*, `code`) → Notion rich_text listesi."""
    parts = []
    pattern = re.compile(r"(\*\*(.+?)\*\*|\*(.+?)\*|`([^`]+?)`)")
    last = 0
    for m in pattern.finditer(text):
        if m.start() > last:
            parts.append({"type": "text", "text": {"content": text[last:m.start()]}})
        raw = m.group(0)
        if raw.startswith("**"):
            parts.append({
                "type": "text",
                "text": {"content": m.group(2)},
                "annotations": {"bold": True},
            })
        elif raw.startswith("*"):
            parts.append({
                "type": "text",
                "text": {"content": m.group(3)},
                "annotations": {"italic": True},
            })
        else:  # `code`
            parts.append({
                "type": "text",
                "text": {"content": m.group(4)},
                "annotations": {"code": True},
            })
        last = m.end()
    if last < len(text):
        parts.append({"type": "text", "text": {"content": text[last:]}})
    if not parts:
        parts.append({"type": "text", "text": {"content": ""}})
    return parts


# ─── Block builders ───────────────────────────────────────────────────────────

def _heading(level: int, text: str) -> dict:
    t = f"heading_{level}"
    return {"type": t, t: {"rich_text": rich_text(text)}}


def _paragraph(text: str) -> dict:
    return {"type": "paragraph", "paragraph": {"rich_text": rich_text(text)}}


def _divider() -> dict:
    return {"type": "divider", "divider": {}}


def _code(content: str, language: str = "") -> dict:
    lang_map = {
        "python": "python", "json": "json", "bash": "bash", "sh": "bash",
        "shell": "bash", "sql": "sql", "yaml": "yaml", "yml": "yaml",
        "javascript": "javascript", "js": "javascript",
        "typescript": "typescript", "ts": "typescript",
    }
    lang = lang_map.get(language.lower().strip(), "plain text")
    return {
        "type": "code",
        "code": {
            "rich_text": [{"type": "text", "text": {"content": content}}],
            "language": lang,
        },
    }


def _quote(text: str) -> dict:
    return {"type": "quote", "quote": {"rich_text": rich_text(text)}}


def _bullet(text: str) -> dict:
    return {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rich_text(text)}}


def _table(rows: list[list[str]]) -> dict:
    """Markdown tablo satırları → Notion table bloğu (ilk satır başlık)."""
    if not rows:
        return _paragraph("")
    width = max(len(r) for r in rows)
    table_rows = []
    for row in rows:
        cells = [rich_text(cell.strip()) for cell in row]
        while len(cells) < width:
            cells.append([{"type": "text", "text": {"content": ""}}])
        table_rows.append({"type": "table_row", "table_row": {"cells": cells}})
    return {
        "type": "table",
        "table": {
            "table_width": width,
            "has_column_header": True,
            "has_row_header": False,
            "children": table_rows,
        },
    }


# ─── Markdown parser ──────────────────────────────────────────────────────────

def md_to_blocks(markdown: str) -> list[dict]:
    blocks: list[dict] = []
    lines = markdown.splitlines()
    i = 0

    while i < len(lines):
        line = lines[i]

        # Fenced code block
        if line.startswith("```"):
            lang = line[3:].strip()
            code_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code_lines.append(lines[i])
                i += 1
            blocks.append(_code("\n".join(code_lines), lang))
            i += 1
            continue

        # Horizontal rule (yalnızca --- satırları, tablo ayracı değil)
        if re.fullmatch(r"-{3,}", line.strip()):
            blocks.append(_divider())
            i += 1
            continue

        # Başlıklar
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            blocks.append(_heading(len(m.group(1)), m.group(2).strip()))
            i += 1
            continue

        # Blockquote
        if line.startswith(">"):
            blocks.append(_quote(line.lstrip("> ").strip()))
            i += 1
            continue

        # Madde işareti listesi
        if re.match(r"^[-*]\s+", line):
            blocks.append(_bullet(re.sub(r"^[-*]\s+", "", line)))
            i += 1
            continue

        # Markdown tablosu
        if line.startswith("|"):
            table_lines: list[str] = []
            while i < len(lines) and lines[i].startswith("|"):
                table_lines.append(lines[i])
                i += 1
            rows = []
            for tl in table_lines:
                cells = [c.strip() for c in tl.strip("|").split("|")]
                # Ayraç satırını atla (|---|---|)
                if all(re.fullmatch(r"[-: ]+", c) for c in cells if c):
                    continue
                rows.append(cells)
            if rows:
                blocks.append(_table(rows))
            continue

        # Boş satır
        if not line.strip():
            i += 1
            continue

        # Normal paragraf
        blocks.append(_paragraph(line.strip()))
        i += 1

    return blocks


# ─── Notion helpers ───────────────────────────────────────────────────────────

def _find_existing(client: Client, parent_id: str, title: str) -> str | None:
    results = client.search(query=title, filter={"property": "object", "value": "page"})
    for page in results.get("results", []):
        parent = page.get("parent", {})
        if parent.get("page_id", "").replace("-", "") == parent_id.replace("-", ""):
            title_list = page.get("properties", {}).get("title", {}).get("title", [])
            if title_list and title_list[0].get("plain_text") == title:
                return page["id"]
    return None


def _create_page(client: Client, parent_id: str, title: str) -> str:
    page = client.pages.create(
        parent={"type": "page_id", "page_id": parent_id},
        properties={"title": {"title": [{"type": "text", "text": {"content": title}}]}},
    )
    return page["id"]


def _append_batched(client: Client, page_id: str, blocks: list[dict]):
    for i in range(0, len(blocks), BATCH_SIZE):
        batch = blocks[i : i + BATCH_SIZE]
        client.blocks.children.append(block_id=page_id, children=batch)
        done = min(i + BATCH_SIZE, len(blocks))
        print(f"  {done}/{len(blocks)} blok yüklendi")


# ─── Ana fonksiyon ────────────────────────────────────────────────────────────

def main():
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        print("Hata: NOTION_TOKEN ortam değişkeni bulunamadı.", file=sys.stderr)
        print("       .env dosyanıza NOTION_TOKEN=... satırı ekleyin.", file=sys.stderr)
        sys.exit(1)

    doc = DOC_PATH
    if len(sys.argv) == 3 and sys.argv[1] == "--doc":
        doc = Path(sys.argv[2])

    print(f"Doküman: {doc}")
    markdown = doc.read_text(encoding="utf-8")

    print("Markdown → Notion blokları dönüştürülüyor...")
    blocks = md_to_blocks(markdown)
    print(f"  {len(blocks)} blok oluşturuldu")

    client = Client(auth=token)

    existing_id = _find_existing(client, PARENT_PAGE_ID, PAGE_TITLE)
    if existing_id:
        print(f"Mevcut sayfa bulundu ({existing_id}), arşivleniyor...")
        client.pages.update(page_id=existing_id, archived=True)

    print(f"Yeni sayfa oluşturuluyor: '{PAGE_TITLE}'")
    page_id = _create_page(client, PARENT_PAGE_ID, PAGE_TITLE)
    print(f"  Sayfa ID: {page_id}")

    print("Bloklar Notion'a yükleniyor...")
    _append_batched(client, page_id, blocks)

    print(f"\nTamamlandı!")
    print(f"https://notion.so/{page_id.replace('-', '')}")


if __name__ == "__main__":
    main()
