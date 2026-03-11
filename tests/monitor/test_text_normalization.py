from __future__ import annotations

from src.monitor.text_normalization import normalize_procurement_text


def test_normalize_procurement_text_cleans_html_and_case() -> None:
    text = normalize_procurement_text(
        "Поставка <b>Ноутбуков</b>",
        "№ 42 &nbsp; для ИТ-отдела",
    )

    assert "поставка ноутбуков" in text
    assert "номер 42 для ит-отдела" in text
