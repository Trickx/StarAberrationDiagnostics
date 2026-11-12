"""Saving rendered images and preparing them for the AI review."""

from __future__ import annotations

import os

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage


def scaled_to_max_side(img: QImage, max_side: int) -> QImage:
    k = min(1.0, max_side / max(img.width(), img.height()))
    if k >= 1:
        return img
    return img.scaled(max(1, round(img.width() * k)), max(1, round(img.height() * k)),
                      Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)


def png_bytes(img: QImage, max_side: int | None = None) -> bytes:
    """The image as PNG, reduced to max_side pixels on its longer side."""
    if max_side:
        img = scaled_to_max_side(img, max_side)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def save_png(img: QImage, path: str) -> None:
    """Saves as PNG, replacing a file of an earlier run."""
    if os.path.exists(path):
        os.remove(path)
    if not img.save(path, "PNG"):
        raise OSError(f"Cannot write {path}")
