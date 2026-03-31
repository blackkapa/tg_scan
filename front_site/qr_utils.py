from typing import Optional

import cv2
import numpy as np


def decode_qr_from_bytes(image_bytes: bytes) -> Optional[str]:
    """Пытаемся вытащить текст из QR-кода на картинке."""
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return None
    detector = cv2.QRCodeDetector()

    variants = []

    # Оригинал и градации серого
    variants.append(img)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    variants.append(gray)

    h, w = img.shape[:2]

    # Масштабирование вниз для очень больших фото, чтобы уменьшить шум
    if max(h, w) > 1600:
        scale = 1600 / max(h, w)
        small = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        variants.append(small)
        variants.append(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))

    # Лёгкое увеличение контраста и бинаризация — часто помогает для фото с телефона
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray_eq = clahe.apply(gray)
    _, thresh = cv2.threshold(gray_eq, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append(gray_eq)
    variants.append(thresh)

    for im in variants:
        try:
            data, _, _ = detector.detectAndDecode(im)
        except cv2.error:
            continue
        if data and data.strip():
            return data.strip()
    return None


def extract_asset_id_from_qr_text(qr_text: str) -> Optional[int]:
    """Ищем AssetId в тексте QR (формат ...?ID=123 или ...&ID=123)."""
    if not qr_text:
        return None
    marker = "ID="
    lower = qr_text.upper()
    idx = lower.find(marker)
    if idx == -1:
        return None
    part = qr_text[idx + len(marker) :]
    part = part.split("&", 1)[0]
    try:
        return int(part)
    except ValueError:
        return None

