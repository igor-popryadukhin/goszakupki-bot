from typing import List, Dict, Any
import re
from bs4 import BeautifulSoup

class EnhancedLLMClient:
    """Улучшенный LLM клиент с анализом релевантности"""

    def __init__(self, provider: str, model: str, api_key: str):
        self.provider = provider
        self.model = model
        self.api_key = api_key

    async def analyze_relevance(self, html_content: str, keywords: List[str]) -> Dict[str, Any]:
        """Анализ релевантности документа через LLM"""
        cleaned_content = self._clean_html_content(html_content)

        # Формируем промпт
        prompt = self._create_relevance_prompt(cleaned_content, keywords)

        try:
            # Используем существующий LLMClient
            from .client import LLMClient
            llm_client = LLMClient(self.provider, self.model, self.api_key)
            result = llm_client._call_llm(prompt, cleaned_content[:2000])
            return self._parse_relevance_result(result)
        except Exception as e:
            return self._keyword_fallback(cleaned_content, keywords, str(e))

    def _clean_html_content(self, html_content: str, max_length: int = 4000) -> str:
        """Очистка HTML контента"""
        if not html_content:
            return ""

        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            for element in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
                element.decompose()

            text = soup.get_text(separator='\n', strip=True)
            text = re.sub(r'\n{3,}', '\n\n', text)
            text = re.sub(r' {2,}', ' ', text)
            return text.strip()[:max_length]

        except Exception:
            cleaned = re.sub(r'<script.*?</script>', '', html_content, flags=re.DOTALL)
            cleaned = re.sub(r'<style.*?</style>', '', cleaned, flags=re.DOTALL)
            cleaned = re.sub(r'<[^>]+>', ' ', cleaned)
            cleaned = re.sub(r'\s+', ' ', cleaned)
            return cleaned.strip()[:max_length]

    def _create_relevance_prompt(self, content: str, keywords: List[str]) -> str:
        """Создание промпта для анализа"""
        keywords_str = "\n".join(f"- {kw}" for kw in keywords)

        return f"""Проанализируй документ о государственных закупках и определи релевантность ключевым темам:

Ключевые темы:
{keywords_str}

Верни ответ ТОЛЬКО в формате JSON:

{{
    "relevant": true/false,
    "confidence": число от 0 до 1,
    "matched_keywords": ["список", "совпавших", "слов"],
    "summary": "краткое описание",
    "reasoning": "обоснование"
}}

Документ:
{content[:3000]}"""

    def _parse_relevance_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Парсинг результата LLM анализа"""
        if result.get("lot_title") or result.get("customer"):
            return {
                "relevant": True,
                "confidence": 0.8,
                "matched_keywords": ["auto_detected"],
                "summary": result.get("lot_title", ""),
                "reasoning": "Документ содержит данные о закупке"
            }

        return {
            "relevant": False,
            "confidence": 0.0,
            "matched_keywords": [],
            "summary": "Анализ не выполнен",
            "reasoning": "Не удалось проанализировать документ"
        }

    def _keyword_fallback(self, content: str, keywords: List[str], error: str) -> Dict[str, Any]:
        """Fallback анализ по ключевым словам"""
        content_lower = content.lower()
        found_keywords = [kw for kw in keywords if kw.lower() in content_lower]

        return {
            "relevant": len(found_keywords) >= 2,
            "confidence": min(1.0, len(found_keywords) * 0.3),
            "matched_keywords": found_keywords,
            "summary": f"Ключевой анализ (найдено: {len(found_keywords)})",
            "reasoning": f"Fallback: {error}" if error else f"Найдено слов: {len(found_keywords)}"
        }