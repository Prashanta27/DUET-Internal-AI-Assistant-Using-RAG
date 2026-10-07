from pathlib import Path
from ingestion.loaders.pdf import load_pdf
from ingestion.chunker import chunk_pages

pages, cls = load_pdf(Path("m0_reality_check\samples\DUET Syllabus for Admission Test 2021-2022.pdf"))
chunks = chunk_pages(pages, target_tokens=450, overlap_tokens=80)
print(cls, "->", len(chunks), "chunks")
if not chunks:
    raise SystemExit("no text layer -- pick a DIGITAL pdf (see scripts/classify_folder.py)")
print(chunks[0].text[:500])