from typing import List, Dict, Any

class LLMClient:
    """
    Обёртка над LLM-провайдером. Пока что метод _call_llm — заглушка.
    Когда решишь, каким SDK пользоваться, просто реализуй _call_llm.
    """
    def __init__(self, provider: str, model: str, api_key: str):
        self.provider = provider
        self.model = model
        self.api_key = api_key

    def analyze_document(self, chunks: List[str], prompt: str) -> Dict[str, Any]:
        # Базовая агрегация: первый ненулевой ответ побеждает, списки объединяются.
        result: Dict[str, Any] = {
            "lot_title": None,
            "start_price": None,
            "currency": None,
            "customer": None,
            "deadline": None,
            "region": None,
            "contact": {"name": None, "phone": None, "email": None},
            "links": []
        }
        for ch in chunks:
            part = self._call_llm(prompt, ch)
            result = self._merge(result, part)
        return result

    def _call_llm(self, prompt: str, content: str) -> Dict[str, Any]:
        # TODO: заменить на реальную интеграцию (SDK выбранного провайдера).
        # Возвращаем пустой ответ, чтобы не ломать текущие вызовы.
        return {
            "lot_title": None,
            "start_price": None,
            "currency": None,
            "customer": None,
            "deadline": None,
            "region": None,
            "contact": {"name": None, "phone": None, "email": None},
            "links": []
        }

    def _merge(self, acc: Dict[str, Any], part: Dict[str, Any]) -> Dict[str, Any]:
        if not part:
            return acc
        def take(a, b):
            return a if a not in (None, "", []) else b
        merged = dict(acc)
        merged["lot_title"]   = take(acc.get("lot_title"),   part.get("lot_title"))
        merged["start_price"] = take(acc.get("start_price"), part.get("start_price"))
        merged["currency"]    = take(acc.get("currency"),    part.get("currency"))
        merged["customer"]    = take(acc.get("customer"),    part.get("customer"))
        merged["deadline"]    = take(acc.get("deadline"),    part.get("deadline"))
        merged["region"]      = take(acc.get("region"),      part.get("region"))
        contact_acc = acc.get("contact") or {}
        contact_part = part.get("contact") or {}
        merged["contact"] = {
            "name":  take(contact_acc.get("name"),  contact_part.get("name")),
            "phone": take(contact_acc.get("phone"), contact_part.get("phone")),
            "email": take(contact_acc.get("email"), contact_part.get("email")),
        }
        merged["links"] = list({*(acc.get("links") or []), * (part.get("links") or [])})
        return merged
