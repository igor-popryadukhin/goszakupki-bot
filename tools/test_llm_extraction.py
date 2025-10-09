import sys
from pathlib import Path
from src.config_llm import USE_LLM, LLM_PROVIDER, LLM_MODEL, LLM_API_KEY, LLM_MAX_TOKENS_PER_CHUNK
from src.llm.client import LLMClient
from src.util.clean_html import extract_main_text, collapse_whitespace, chunk_for_llm

def main():
    if len(sys.argv) < 2:
        print("Usage: python -m tools.test_llm_extraction path/to/file.html")
        sys.exit(1)

    html_path = Path(sys.argv[1])
    html = html_path.read_text(encoding="utf-8", errors="ignore")

    text = extract_main_text(html)
    text = collapse_whitespace(text)
    chunks = chunk_for_llm(text, LLM_MAX_TOKENS_PER_CHUNK)
    prompt = Path("prompts/extract.md").read_text(encoding="utf-8")

    client = LLMClient(LLM_PROVIDER, LLM_MODEL, LLM_API_KEY)
    result = client.analyze_document(chunks, prompt)
    print(result)

if __name__ == "__main__":
    main()
