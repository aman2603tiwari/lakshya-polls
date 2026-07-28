"""
================================================================
  ONE-TIME SETUP SCRIPT — Run this locally on your PC once
  Extracts text from your 3 PYQ PDFs → saves as .txt files
  Then commit the .txt files to your GitHub repo
  
  Usage:
    pip install pymupdf
    python extract_pdfs.py
================================================================
"""

import fitz  # PyMuPDF
import os

# ── Put your PDF filenames here ──────────────────────────────
PDF_MAP = {
    "pdfs/physics_pyq.pdf":   "pdfs/physics_pyq.txt",
    "pdfs/chemistry_pyq.pdf": "pdfs/chemistry_pyq.txt",
    "pdfs/maths_pyq.pdf":     "pdfs/maths_pyq.txt",
}

os.makedirs("pdfs", exist_ok=True)

for pdf_path, txt_path in PDF_MAP.items():
    if not os.path.exists(pdf_path):
        print(f"⚠️  Not found: {pdf_path} — skipping")
        continue

    print(f"📄 Extracting: {pdf_path}")
    doc = fitz.open(pdf_path)
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text)

    size_kb = os.path.getsize(txt_path) // 1024
    print(f"   ✅ Saved → {txt_path} ({size_kb} KB, {len(text):,} chars)")

print("\n✅ Done! Now commit the pdfs/*.txt files to your GitHub repo.")
