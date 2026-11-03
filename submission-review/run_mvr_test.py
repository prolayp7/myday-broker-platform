"""Reads sample MVRs into staging and reports what passed.

  python run_mvr_test.py <folder> [limit] [--no-save]

Picks the files with the most entries first, so a small batch still covers the
hard cases. Azure's text is cached in REVIEW_TEXT_CACHE, so a re-run after a
prompt change costs Claude only.
"""

import glob
import hashlib
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import azure_text
import mvr
import store

_CACHE = os.environ.get("REVIEW_TEXT_CACHE", ".text-cache")


def _files(folder: str) -> list[str]:
    found = glob.glob(os.path.join(folder, "**", "*.pdf"), recursive=True)
    found = [f for f in found if re.search("mvr", os.path.basename(f), re.I)]

    def entries(path: str) -> int:
        from pypdf import PdfReader

        text = "\n".join(p.extract_text() or "" for p in PdfReader(path).pages)
        match = re.search(r"Number of Convictions:\s*(\d+)", text)
        return int(match.group(1)) if match else 99

    return sorted(found, key=entries, reverse=True)


def _text(pdf: bytes, sha256: str) -> str:
    os.makedirs(_CACHE, exist_ok=True)
    path = os.path.join(_CACHE, f"{sha256}.txt")
    if os.path.exists(path):
        with open(path, encoding="utf8") as f:
            return f.read()
    text = azure_text.read_pdf(pdf)
    with open(path, "w", encoding="utf8") as f:
        f.write(text)
    return text


def run_one(path: str, save: bool) -> tuple[str, str, list[str]]:
    with open(path, "rb") as f:
        pdf = f.read()
    sha256 = store.fingerprint(pdf)
    # Hashed, so a report can be shared without naming the customer.
    label = hashlib.sha256(os.path.basename(path).encode()).hexdigest()[:8]
    if save and store.already_read(sha256):
        return label, "skipped", []
    parsed = None
    try:
        text = _text(pdf, sha256)
        parsed = mvr.read_mvr(text)
        problems = mvr.self_check(parsed, text)
    except Exception as err:
        problems = [f"{type(err).__name__}: {err}"[:200]]
    if save:
        licence = mvr._licence(parsed["licence_no"]) if parsed else None
        doc = store.save_document(
            os.path.basename(path), sha256, "mvr", parsed, problems, licence or None
        )
        if not problems:
            store.save_mvr(doc, *mvr.to_rows(parsed))
    return label, "failed" if problems else "ok", problems


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    save = "--no-save" not in sys.argv
    files = _files(args[0])
    # The sample folders hold copies of the same PDF; one reading of each.
    unique: dict[str, str] = {}
    for path in files:
        with open(path, "rb") as f:
            unique.setdefault(store.fingerprint(f.read()), path)
    files = list(unique.values())
    if len(args) > 1:
        files = files[: int(args[1])]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda f: run_one(f, save), files))
    for label, status, problems in results:
        if status == "failed":
            print(label, problems)
    counts = {s: sum(1 for r in results if r[1] == s) for s in ("ok", "failed", "skipped")}
    print(counts, "of", len(results))


if __name__ == "__main__":
    main()
