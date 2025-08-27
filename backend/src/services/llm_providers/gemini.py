import os
import logging
import json
import google.generativeai as genai
from tenacity import retry, stop_after_attempt, wait_random_exponential
from .base import LLMProvider

logger = logging.getLogger(__name__)

class GoogleProvider(LLMProvider):
    """Provider for Google's Gemini models."""

    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY environment variable is not set.")
        genai.configure(api_key=api_key)
        self.default_model = "gemini-1.5-pro-latest"
        logger.info("Google (Gemini) provider initialized.")

    @retry(wait=wait_random_exponential(min=1, max=30), stop=stop_after_attempt(3))
    async def agenerate(self, prompt: str, system_prompt: str, use_json_mode: bool = False, model: str = None) -> str:
        model_to_use = model or self.default_model

        if use_json_mode:
            generation_config = genai.types.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.2
            )
        else:
            generation_config = genai.types.GenerationConfig(
                temperature=0.2
            )

        try:
            # We combine the system prompt into the main prompt content for Gemini's format
            full_prompt = f"{system_prompt}\n\n---\n\n{prompt}"
            
            model_instance = genai.GenerativeModel(model_to_use)
            response = await model_instance.generate_content_async(
                full_prompt,
                generation_config=generation_config
            )
            
            # Gemini may wrap JSON in ```json ... ```, so we need to clean it.
            cleaned_response = response.text.strip().removeprefix("```json").removesuffix("```").strip()
            return cleaned_response
        except Exception as e:
            logger.error(f"Google Gemini API call failed for model {model_to_use}. Retrying... Error: {e}")
            raise