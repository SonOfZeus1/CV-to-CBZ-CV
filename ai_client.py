import os
import json
import logging
import time
from typing import Any, Dict, Optional, Union

from openai import OpenAI

# Configure logging
logger = logging.getLogger(__name__)

# Constants
# Priority list of models (Quality -> Speed/Quota)
MODELS = [
    # 1️⃣ 🏆 Modèle principal recommandé (équilibre parfait)
    "meta-llama/llama-3.3-70b-instruct:free",

    # 2️⃣ ⚡️ Ultra-rapide et intelligent (Google)
    "google/gemini-2.0-flash-exp:free",

    # 3️⃣ Très bon pour extraction structurée
    "mistralai/mistral-small-3.1-24b-instruct:free",

    # 4️⃣ Très bon modèle orienté structure / parsing
    "mistralai/devstral-2512:free",

    # 5️⃣ Fallback Puissant (mais lent/instable)
    "nousresearch/hermes-3-llama-3.1-405b:free",
]

MAX_RETRIES_PER_MODEL = 2

# Rate Limiting Configuration (OpenRouter usually handles this, but we keep a safety buffer)
# ============================================================
# Rate Limiting Configuration
# Conservative, production-safe for OpenRouter (free tier)
# Focus: long prompts, structured JSON extraction
# ============================================================

RATE_LIMITS = {
    "meta-llama/llama-3.3-70b-instruct:free": {"rpm": 8},
    "google/gemini-2.0-flash-exp:free": {"rpm": 10},
    "mistralai/mistral-small-3.1-24b-instruct:free": {"rpm": 10},
    "mistralai/devstral-2512:free": {"rpm": 8},
    "nousresearch/hermes-3-llama-3.1-405b:free": {"rpm": 2},
}


class RateLimiter:
    def __init__(self):
        self.last_request_time = {}
        
    def wait_for_token(self, model_name):
        limits = RATE_LIMITS.get(model_name, {"rpm": 30})
        rpm = limits["rpm"]
        interval = 60.0 / rpm
        
        now = time.time()
        last = self.last_request_time.get(model_name, 0)
        
        elapsed = now - last
        if elapsed < interval:
            sleep_time = interval - elapsed
            logger.info(f"Rate Limit: Sleeping {sleep_time:.2f}s for {model_name}")
            time.sleep(sleep_time)
            
        self.last_request_time[model_name] = time.time()

rate_limiter = RateLimiter()

class AIClient:
    _instance = None

    def __init__(self):
        # OpenRouter uses OpenAI client structure
        api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("GROQ_API_KEY") # Fallback to GROQ key if user hasn't updated env yet (though they should)
        
        if not api_key:
            logger.warning("OPENROUTER_API_KEY not found. AI features will be disabled.")
            self.client = None
        else:
            self.client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=api_key,
            )
            logger.info("OpenRouter Client initialized successfully.")

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def call_ai(self, prompt: str, system_prompt: str = "You are a helpful assistant.", expect_json: bool = False, model: str = None) -> Union[str, Dict[str, Any]]:
        """
        Generic function to call the AI with Multi-Model Fallback via OpenRouter.
        Supports optional 'model' override.
        """
        if not self.client:
            logger.error("AI call attempted but client is not initialized (missing key).")
            return {} if expect_json else ""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]

        if expect_json:
             messages.append({"role": "system", "content": "IMPORTANT: Output ONLY valid JSON. No markdown, no explanations."})

        logger.info(f"Calling AI (JSON={expect_json}, Model={model or 'Default'}). Prompt length: {len(prompt)}")
        
        start_time = time.time()

        # Use provided model or iterate through defaults (Priority Order)
        models_to_try = [model] if model else MODELS

        # Iterate through models in priority order
        for model_name in models_to_try:
            # Rate Limit Check
            rate_limiter.wait_for_token(model_name)
            
            logger.info(f"Trying model: {model_name}")
            
            for attempt in range(MAX_RETRIES_PER_MODEL):
                try:
                    completion = self.client.chat.completions.create(
                        model=model_name,
                        messages=messages,
                        temperature=0.1 if expect_json else 0.3,
                        timeout=180, # 3 minutes timeout (Fail fast)
                        extra_headers={
                            "HTTP-Referer": "https://github.com/SonOfZeus1/CV-to-CBZ-CV", 
                            "X-Title": "CV Extraction Pipeline", 
                        },
                    )
                    
                    content = completion.choices[0].message.content
                    duration = time.time() - start_time
                    logger.info(f"AI Response received from {model_name} in {duration:.2f}s. Length: {len(content)}")

                    if expect_json:
                        try:
                            # Clean up markdown code blocks if present
                            cleaned_content = content.strip()
                            if cleaned_content.startswith("```"):
                                # Remove first line (```json or just ```)
                                cleaned_content = "\n".join(cleaned_content.split("\n")[1:])
                                # Remove last line if it is ```
                                if cleaned_content.strip().endswith("```"):
                                    cleaned_content = cleaned_content.strip()[:-3]
                            
                            return json.loads(cleaned_content)
                        except json.JSONDecodeError:
                            logger.error(f"Failed to parse JSON from AI response ({model_name}): {content[:100]}...")
                            if attempt < MAX_RETRIES_PER_MODEL - 1:
                                logger.info("Retrying same model...")
                                continue
                            else:
                                # If JSON parsing fails repeatedly on this model, try next model
                                logger.warning(f"JSON parsing failed for {model_name}, switching to next model...")
                                break 
                    
                    return content

                except Exception as e:
                    error_str = str(e).lower()
                    # Check for Rate Limit (429)
                    if "429" in error_str or "rate limit" in error_str or "too many requests" in error_str:
                        logger.warning(f"Rate Limit hit for {model_name}. Switching to next model immediately...")
                        break # Break inner loop -> Try next model in outer loop
                    
                    logger.error(f"AI Call failed for {model_name} (Attempt {attempt+1}/{MAX_RETRIES_PER_MODEL}): {e}")
                    
                    if attempt < MAX_RETRIES_PER_MODEL - 1:
                        # Exponential backoff for non-rate-limit errors
                        sleep_time = 2 ** (attempt + 1)
                        logger.info(f"Retrying {model_name} in {sleep_time} seconds...")
                        time.sleep(sleep_time)
                    else:
                        # If we exhausted retries for this model (e.g. 500 error), try next model
                        logger.warning(f"Model {model_name} failed repeatedly. Switching to next model...")
                        break
        
        logger.error("All models failed.")
        return {} if expect_json else ""

# Global helper function
def call_ai(prompt: str, system_prompt: str = "", expect_json: bool = False, model: str = None) -> Union[str, Dict[str, Any]]:
    client = AIClient.get_instance()
    return client.call_ai(prompt, system_prompt, expect_json, model)
