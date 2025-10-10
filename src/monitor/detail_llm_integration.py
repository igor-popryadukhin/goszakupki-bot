from typing import List
from ..llm.enhanced_client import EnhancedLLMClient
from ..config_llm import USE_LLM, LLM_PROVIDER, LLM_MODEL, LLM_API_KEY

class DetailLLMIntegrationMixin:
    """Mixin для добавления LLM анализа в DetailMonitorService"""
    
    def _init_llm_client(self):
        """Инициализация LLM клиента"""
        self.llm_client = None
        if USE_LLM and LLM_API_KEY:
            try:
                self.llm_client = EnhancedLLMClient(LLM_PROVIDER, LLM_MODEL, LLM_API_KEY)
                print("✅ LLM клиент инициализирован для детального анализа")
            except Exception as e:
                print(f"⚠️ Ошибка инициализации LLM: {e}")
    
    async def _llm_relevance_check(self, html_content: str, text_content: str, keywords: List[str]) -> bool:
        """Проверка релевантности через LLM для детального анализа"""
        if not self.llm_client:
            return True
        
        try:
            # Используем HTML контент для анализа
            analysis = await self.llm_client.analyze_relevance(html_content, keywords)
            print(f"🔍 Детальный LLM анализ: relevant={analysis['relevant']}, confidence={analysis['confidence']:.2f}")
            return analysis['relevant'] and analysis['confidence'] > 0.6
        except Exception as e:
            print(f"⚠️ Ошибка детального LLM анализа: {e}")
            return True
