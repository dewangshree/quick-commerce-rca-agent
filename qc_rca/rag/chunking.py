"""Markdown loading and heading-aware chunking."""
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)
HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.M)

# Doc types for the reference docs that ship without front matter.
REFERENCE_DOC_TYPES = {
    "order_ready_to_assignment.md": "metric_definition",
    "quick_commerce_orders_gold.md": "schema",
    "quick_commerce_rca_logic.md": "playbook",
}


@dataclass
class LoadedDocument:
    source_path: str
    title: str
    doc_type: str
    is_sample: bool
    body: str
    sha256: str
    meta: dict = field(default_factory=dict)


@dataclass
class Chunk:
    index: int
    heading: str | None
    content: str
    meta: dict


def _parse_front_matter(text: str) -> tuple[dict, str]:
    m = FRONT_MATTER_RE.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"')
    return meta, text[m.end():]


def load_document(path: Path, root: Path) -> LoadedDocument:
    raw = path.read_text(encoding="utf-8")
    meta, body = _parse_front_matter(raw)
    first_heading = HEADING_RE.search(body)
    title = meta.get("title") or (first_heading.group(2).strip() if first_heading else path.stem)
    doc_type = meta.get("doc_type") or REFERENCE_DOC_TYPES.get(path.name, "reference")
    is_sample = str(meta.get("sample", "false")).lower() == "true"
    return LoadedDocument(
        source_path=str(path.relative_to(root)), title=title, doc_type=doc_type, is_sample=is_sample,
        body=body, sha256=hashlib.sha256(raw.encode()).hexdigest(), meta=meta,
    )


def _split_long(text: str, size: int, overlap: int) -> list[str]:
    if len(text) <= size:
        return [text]
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    out, cur = [], ""
    for p in paras:
        if len(cur) + len(p) + 2 <= size:
            cur = f"{cur}\n\n{p}" if cur else p
            continue
        if cur:
            out.append(cur)
            cur = cur[-overlap:] + "\n\n" + p if overlap else p
        else:
            cur = p
        while len(cur) > size:  # single huge paragraph
            out.append(cur[:size])
            cur = cur[size - overlap:]
    if cur.strip():
        out.append(cur)
    return out


def chunk_document(doc: LoadedDocument, size: int = 900, overlap: int = 150) -> list[Chunk]:
    """Split on markdown headings; long sections are split on paragraphs with overlap."""
    sections: list[tuple[str | None, str]] = []
    matches = list(HEADING_RE.finditer(doc.body))
    if not matches or matches[0].start() > 0:
        pre = doc.body[: matches[0].start()] if matches else doc.body
        if pre.strip():
            sections.append((None, pre.strip()))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(doc.body)
        text = doc.body[m.end():end].strip()
        if text:
            sections.append((m.group(2).strip(), text))

    chunks: list[Chunk] = []
    for heading, text in sections:
        for piece in _split_long(text, size, overlap):
            chunks.append(Chunk(
                index=len(chunks), heading=heading,
                content=f"{doc.title}\n{heading or ''}\n{piece}".strip(),
                meta={"title": doc.title, "doc_type": doc.doc_type, "is_sample": doc.is_sample,
                      "source_path": doc.source_path},
            ))
    return chunks
