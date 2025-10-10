import os

USE_LLM = os.getenv("USE_LLM", "false").lower() in ("1", "true", "yes", "on")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4.1-mini")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MAX_TOKENS_PER_CHUNK = int(os.getenv("LLM_MAX_TOKENS_PER_CHUNK", "1200"))
