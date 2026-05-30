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
        self.prompt_file = prompt_file

    def _load_prompt(self) -> str:
        if os.path.exists(self.prompt_file):
            with open(self.prompt_file, "r", encoding="utf-8") as f:
                return f.read()
        return "Перефразируй следующий текст:"

    async def rewrite(self, text: str) -> str:
        system_prompt = self._load_prompt()
        logger.info("Requesting Gemini rewrite...")
        try:
            import google.generativeai as genai
            model = genai.GenerativeModel(
                model_name="gemini-1.5-flash",
                system_instruction=system_prompt
            )
            response = await model.generate_content_async(text)
            return response.text.strip()
        except Exception as e:
            logger.error(f"Gemini API request failed: {e}")
            raise e

class GigaChatRewriter(BaseRewriter):
    def __init__(self, auth_key: str, scope: str = "GIGACHAT_API_PERS", model: str = "GigaChat", verify_ssl: bool = False, prompt_file: str = "prompt.txt"):
        self.auth_key = auth_key
        self.scope = scope
        self.model = model
        self.verify_ssl = verify_ssl
        self.prompt_file = prompt_file

    def _load_prompt(self) -> str:
        if os.path.exists(self.prompt_file):
            with open(self.prompt_file, "r", encoding="utf-8") as f:
                return f.read()
        return "Перефразируй следующий текст:"

    async def rewrite(self, text: str) -> str:
        from gigachat import GigaChat
        system_prompt = self._load_prompt()
        logger.info("Requesting GigaChat rewrite...")
        try:
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text}
                ]
            }
            async with GigaChat(
                credentials=self.auth_key,
                scope=self.scope,
                model=self.model,
                verify_ssl_certs=self.verify_ssl
            ) as client:
                response = await client.achat(payload)
                return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"GigaChat API request failed: {e}")
            raise e

class DeepSeekRewriter(BaseRewriter):
    def __init__(self, api_key: str, base_url: str = "https://integrate.api.nvidia.com/v1", model: str = "deepseek-ai/deepseek-v4-pro", prompt_file: str = "prompt.txt"):
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.prompt_file = prompt_file

    def _load_prompt(self) -> str:
        if os.path.exists(self.prompt_file):
            with open(self.prompt_file, "r", encoding="utf-8") as f:
                return f.read()
        return "Перефразируй следующий текст:"

    async def rewrite(self, text: str) -> str:
        system_prompt = self._load_prompt()
        logger.info(f"Requesting DeepSeek rewrite using model {self.model}...")
        try:
            completion = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text}
                ],
                temperature=1,
                top_p=0.95,
                max_tokens=2048,
                extra_body={"chat_template_kwargs": {"thinking": False}},
                stream=False,
                timeout=30.0
            )
            return completion.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"DeepSeek API request failed: {e}")
            raise e

def get_rewriter(prompt_file: str = "prompt.txt") -> BaseRewriter:
    """Factory to get the configured rewriter."""
    provider = os.getenv("LLM_PROVIDER", "claude").lower()
    
    if provider == "gemini":
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY must be set when using Gemini provider.")
        return GeminiRewriter(api_key=api_key, prompt_file=prompt_file)
    elif provider == "gigachat":
        auth_key = os.getenv("GIGACHAT_AUTH_KEY")
        if not auth_key:
            raise ValueError("GIGACHAT_AUTH_KEY must be set when using GigaChat provider.")
        scope = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
        model = os.getenv("GIGACHAT_MODEL", "GigaChat")
        verify_ssl_str = os.getenv("GIGACHAT_VERIFY_SSL", "true").lower()
        verify_ssl = verify_ssl_str not in ("false", "0")
        
        return GigaChatRewriter(
            auth_key=auth_key,
            scope=scope,
            model=model,
            verify_ssl=verify_ssl,
            prompt_file=prompt_file
        )
    elif provider == "deepseek":
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY must be set when using DeepSeek provider.")
        base_url = os.getenv("DEEPSEEK_BASE_URL", "https://integrate.api.nvidia.com/v1")
        model = os.getenv("DEEPSEEK_MODEL", "deepseek-ai/deepseek-v4-pro")
        return DeepSeekRewriter(api_key=api_key, base_url=base_url, model=model, prompt_file=prompt_file)
    else:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY must be set when using Claude provider.")
        return ClaudeRewriter(api_key=api_key, prompt_file=prompt_file)
