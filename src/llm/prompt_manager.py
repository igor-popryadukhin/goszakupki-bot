import json
import os
from typing import Dict

class PromptManager:
    """Менеджер промптов с возможностью редактирования"""

    def __init__(self, prompts_dir: str = "prompts"):
        self.prompts_dir = prompts_dir
        self.prompts_file = os.path.join(prompts_dir, "analysis_prompts.json")
        self._ensure_prompts_dir()
        self.prompts = self._load_prompts()

    def _ensure_prompts_dir(self):
        """Создает директорию для промптов если не существует"""
        os.makedirs(self.prompts_dir, exist_ok=True)

    def _load_prompts(self) -> Dict[str, str]:
        """Загружает промпты из файла или создает default"""
        default_prompts = {
            "relevance_analysis": """Проанализируй документ о государственных закупках и определи релевантность ключевым темам:

Ключевые темы:
{keywords}

Инструкция:
1. Внимательно изучи документ
2. Определи основные темы и содержание
3. Оцени релевантность ключевым темам
4. Верни ответ в формате JSON:

{{
    "relevant": true/false,
    "confidence": число от 0 до 1,
    "matched_keywords": ["список", "совпавших", "слов"],
    "summary": "краткое описание документа",
    "reasoning": "обоснование решения"
}}

Документ для анализа:
{content}""",

            "content_extraction": """Извлеки основной текстовый контент из HTML документа о закупках, удалив:
- Скрипты и стили
- Навигационные элементы
- Рекламные блоки
- Повторяющиеся элементы
- Футеры и хедеры

Оставь только основной контент документа связанный с закупкой.

HTML:
{html_content}"""
        }

        if os.path.exists(self.prompts_file):
            try:
                with open(self.prompts_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"⚠️ Ошибка загрузки промптов: {e}")

        # Создаем файл с default промптами
        self._save_prompts(default_prompts)
        return default_prompts

    def _save_prompts(self, prompts: Dict[str, str]):
        """Сохраняет промпты в файл"""
        try:
            with open(self.prompts_file, 'w', encoding='utf-8') as f:
                json.dump(prompts, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ Ошибка сохранения промптов: {e}")

    def get_prompt(self, name: str, **kwargs) -> str:
        """Возвращает промпт с подстановкой параметров"""
        if name not in self.prompts:
            raise ValueError(f"Промпт '{name}' не найден")

        return self.prompts[name].format(**kwargs)

    def update_prompt(self, name: str, content: str):
        """Обновляет промпт"""
        self.prompts[name] = content
        self._save_prompts(self.prompts)
        print(f"✅ Промпт '{name}' обновлен")

    def list_prompts(self) -> list:
        """Возвращает список доступных промптов"""
        return list(self.prompts.keys())