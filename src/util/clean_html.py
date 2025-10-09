from bs4 import BeautifulSoup
import re
from typing import List

NOISE_CLASSES = re.compile(r"(nav|menu|footer|header|breadcrumb|sidebar|social|pagination|advert|banner)", re.I)

def strip_html_boilerplate(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript", "template"]):
        t.decompose()
    # иногда встраивают json-ld с мегатекстом
    for t in soup.find_all("script", attrs={"type": "application/ld+json"}):
        t.decompose()
    return str(soup)

def drop_layout_noise(html_text: str) -> str:
    soup = BeautifulSoup(html_text, "lxml")
    # явные контейнеры-шум по классам
    for tag in soup.find_all(attrs={"class": NOISE_CLASSES}):
        tag.decompose()
    # структурные шумы
    for tag in soup.find_all(["nav", "footer", "header", "aside"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)

def extract_main_text(html: str) -> str:
    text = drop_layout_noise(strip_html_boilerplate(html))
    # простая нормализация: выброс очень коротких строк
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if len(ln) > 3]
    return "\n".join(lines)

def collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()

def chunk_for_llm(text: str, target_tokens: int = 1200) -> List[str]:
    # Грубая оценка: 1k–1.2k токенов ~ 800–900 слов
    max_words = 850
    words = text.split()
    return [" ".join(words[i:i+max_words]) for i in range(0, len(words), max_words)]
