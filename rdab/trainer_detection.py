from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import Image
import pytesseract


def extract_trainer_name(image_path: str | Path) -> Optional[str]:
    """
    Run a lightweight OCR pass over a trainer profile screenshot and return
    the first detected text line. Mirrors the logic previously embedded in
    app.py so both the RDAB signup flow and the geocache quest can share it.
    """
    try:
        img = Image.open(image_path)
        w, h = img.size
        top, bottom = int(h * 0.15), int(h * 0.25)
        left, right = int(w * 0.05), int(w * 0.90)
        cropped = img.crop((left, top, right, bottom))
        text = pytesseract.image_to_string(cropped)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return lines[0] if lines else None
    except Exception as exc:  # pragma: no cover - defensive guard
        print("❌ OCR failed:", exc)
        return None

