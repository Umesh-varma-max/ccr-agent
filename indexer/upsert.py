from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from crawler.config import QDRANT_COLLECTION, QDRANT_LOCAL_PATH, SECTIONS_PATH
from indexer.embed import batched, get_embedding_provider
from indexer.schema import metadata_for_vector_store, validate_section
from qdrant_utils import connect_qdrant, get_qdrant_uri


def chunk_markdown(text: str, max_words: int = 500, overlap: int = 80) -> list[str]:
    words = re.findall(r"\S+", text)
    if len(words) <= max_words:
        return [text]
    chunks: list[str] = []
    step = max_words - overlap
    for start in range(0, len(words), step):
        chunk = " ".join(words[start : start + max_words])
        if chunk:
            chunks.append(chunk)
        if start + max_words >= len(words):
            break
    return chunks


def load_sections(path: Path) -> list[dict]:
    rows: list[dict] = []
    skipped = 0
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                try:
                    row = json.loads(line)
                    validate_section(row)
                    rows.append(row)
                except Exception:
                    skipped += 1
    if skipped:
        print(f"Skipped {skipped} invalid extracted sections from {path}")
    return rows


def upsert(input_path: Path, db_path: Path, collection_name: str = QDRANT_COLLECTION) -> int:
    client = connect_qdrant(db_path)
    try:
        embedder = get_embedding_provider()
        sections = load_sections(input_path)

        records: list[dict] = []
        for section in sections:
            chunks = chunk_markdown(section["content_markdown"])
            for idx, chunk in enumerate(chunks):
                metadata = metadata_for_vector_store(section, idx)
                records.append(
                    {
                        "id": f"{section['citation']}::chunk-{idx}",
                        "text": chunk,
                        **metadata,
                    }
                )

        if not records:
            client.ensure_collection(collection_name, 384)
            print(
                f"No sections found in {input_path}; Qdrant collection is ready at {get_qdrant_uri(db_path)} "
                f"using embedding provider '{getattr(embedder, 'provider_name', 'unknown')}'."
            )
            return 0

        indexed = 0
        for record_batch in batched(records, 64):
            embeddings = embedder.embed_documents([record["text"] for record in record_batch])
            client.ensure_collection(collection_name, len(embeddings[0]))
            payload = []
            for record, vector in zip(record_batch, embeddings):
                payload.append({**record, "vector": vector})
            client.upsert(collection_name=collection_name, data=payload)
            indexed += len(payload)
        client.flush(collection_name)
        print(
            f"Indexed {indexed} chunks from {len(sections)} sections into Qdrant at {get_qdrant_uri(db_path)} "
            f"using embedding provider '{getattr(embedder, 'provider_name', 'unknown')}'."
        )
        return indexed
    finally:
        client.close()


def verify_count(db_path: Path, collection_name: str = QDRANT_COLLECTION) -> int:
    client = connect_qdrant(db_path)
    try:
        if not client.has_collection(collection_name):
            print(f"Qdrant collection '{collection_name}' does not exist yet")
            return 0
        count = client.get_collection_stats(collection_name).get("row_count", 0)
        print(f"Qdrant collection '{collection_name}' contains {count} records")
        return count
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Upsert CCR sections into Qdrant.")
    parser.add_argument("--input", default=str(SECTIONS_PATH))
    parser.add_argument("--db", default=str(QDRANT_LOCAL_PATH))
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        verify_count(Path(args.db))
    else:
        upsert(Path(args.input), Path(args.db))


if __name__ == "__main__":
    main()
