import os
import logging
from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)

class BaseRewriter:
    async def rewrite(self, text: str) -> str:
        raise NotImplementedError("Rewriter must implement the rewrite method.")

class ClaudeRewriter(BaseRewriter):
    def __init__(self, api_key: str, prompt_file: str = "prompt.txt"):
        self.client = AsyncAnthropic(api_key=api_key)
        self.prompt_file = prompt_file

    def _load_prompt(self) -> str:
        if os.path.exists(self.prompt_file):
            with open(self.prompt_file, "r", encoding="utf-8") as f:
                return f.read()
        return "Перефразируй следующий текст:"

    async def rewrite(self, text: str) -> str:
        system_prompt = self._load_prompt()
        logger.info("Requesting Claude rewrite...")
        try:
            message = await self.client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=2000,
                temperature=0.7,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": text}
                ]
            )
            rewritten_text = "".join([block.text for block in message.content])
            return rewritten_text.strip()
        except Exception as e:
            logger.error(f"Claude API request failed: {e}")
            raise e

class GeminiRewriter(BaseRewriter):
    def __init__(self, api_key: str, prompt_file: str = "prompt.txt"):
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel("gemini-1.5-flash")
        self.prompt_file = prompt_file

    def _load_prompt(self) -> str:
        if os.path.exists(self.prompt_file):
            with open(self.prompt_file, "r", encoding="utf-8") as f:
                return f.read()
        return "Перефразируй следующий текст:"

    async def rewrite(self, text: str) -> str:
        system_prompt = self._load_prompt()
        full_prompt = f"{system_prompt}\n\nТекст:\n{text}"
        logger.info("Requesting Gemini rewrite...")
        try:
            # We run the synchronous call in executor if native async is not preferred
            import asyncio
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None, 
                lambda: self.model.generate_content(full_prompt)
            )
            return response.text.strip()
        except Exception as e:
            logger.error(f"Gemini API request failed: {e}")
            raise e

def get_rewriter(prompt_file: str = "prompt.txt") -> BaseRewriter:
    """Factory to get the configured rewriter."""
    provider = os.getenv("LLM_PROVIDER", "claude").lower()
    
    if provider == "gemini":
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY must be set when using Gemini provider.")
        return GeminiRewriter(api_key=api_key, prompt_file=prompt_file)
    else:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY must be set when using Claude provider.")
        return ClaudeRewriter(api_key=api_key, prompt_file=prompt_file)
