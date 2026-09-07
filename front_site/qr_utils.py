import io
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageOps


def decode_qr_from_bytes(image_bytes: bytes) -> Optional[str]:
    """
    Распознавание QR-кода с фотографий любого разрешения и ориентации:
    1. Автоповорот по EXIF (метаданные ориентации смартфонов iOS/Android).
    2. Двойной детектор: QRCodeDetectorAruco (OpenCV 4.7+) + стандартный QRCodeDetector.
    3. Мультимасштабирование (пирамида разрешений для четкого захвата как мелких, так и крупных наклеек).
    4. Повороты на 0°, 90°, 180°, 270°.
    5. Адаптивные фильтры контрастности (CLAHE, Otsu, Gaussian Adaptive Threshold).
    """
    if not image_bytes:
        return None

    # 1. Загрузка и нормализация ориентации EXIF через PIL
    try:
        pil_img = Image.open(io.BytesIO(image_bytes))
        pil_img = ImageOps.exif_transpose(pil_img)
        if pil_img.mode != "RGB":
            pil_img = pil_img.convert("RGB")
        img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    except Exception:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    if img is None:
        return None

    # Инициализация детекторов
    detectors = []
    if hasattr(cv2, "QRCodeDetectorAruco"):
        try:
            detectors.append(cv2.QRCodeDetectorAruco())
        except Exception:
            pass
    detectors.append(cv2.QRCodeDetector())

    def _try_detect(frame) -> Optional[str]:
        for det in detectors:
            try:
                data, _, _ = det.detectAndDecode(frame)
                if data and data.strip():
                    return data.strip()
            except Exception:
                continue
        return None

    # Быстрая проверка на оригинале
    quick_res = _try_detect(img)
    if quick_res:
        return quick_res

    h, w = img.shape[:2]
    max_dim = max(h, w)

    # Пирамида масштабов
    scales = [img]
    for target_dim in (2400, 1600, 1000, 700):
        if max_dim > target_dim:
            s = target_dim / max_dim
            resized = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
            scales.append(resized)

    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))

    for base in scales:
        gray = cv2.cvtColor(base, cv2.COLOR_BGR2GRAY) if len(base.shape) == 3 else base
        gray_eq = clahe.apply(gray)
        _, otsu = cv2.threshold(gray_eq, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        adapt = cv2.adaptiveThreshold(
            gray_eq, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 51, 5
        )

        variants = [base, gray, gray_eq, otsu, adapt]
        for v in variants:
            for rot in (0, 1, 2, 3):
                test_frame = np.rot90(v, rot) if rot > 0 else v
                res = _try_detect(test_frame)
                if res:
                    return res

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

