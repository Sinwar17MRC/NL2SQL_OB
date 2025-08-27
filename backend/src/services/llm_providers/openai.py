import os
import logging
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_random_exponential
from .base import LLMProvider

logger = logging.getLogger(__name__)

class OpenAIProvider(LLMProvider):
    """Provider for OpenAI models like GPT-4."""
    
    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is not set.")
        self.client = AsyncOpenAI(api_key=api_key, max_retries=0)
        self.default_model = "gpt-4-turbo-preview"
        logger.info("OpenAI provider initialized.")

    @retry(wait=wait_random_exponential(min=1, max=30), stop=stop_after_attempt(3))
    async def agenerate(self, prompt: str, system_prompt: str, use_json_mode: bool = False, model: str = None) -> str:
        model_to_use = model or self.default_model
        response_format = {"type": "json_object"} if use_json_mode else {"type": "text"}
        
        try:
            response = await self.client.chat.completions.create(
                model=model_to_use,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                response_format=response_format,
                temperature=0.1 # Lower temperature for more deterministic SQL
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"OpenAI API call failed for model {model_to_use}. Retrying... Error: {e}")
            raise