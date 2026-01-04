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
    # 1️⃣ 🏆 MEILLEURE QUALITÉ ABSOLUE (si quota OK)
    # "openai/gpt-oss-120b:free", # REMOVED due to 404 errors
    "nousresearch/hermes-3-llama-3.1-405b:free",

    # 3️⃣ Modèle principal recommandé (équilibre parfait)
    "meta-llama/llama-3.3-70b-instruct:free",

    # 4️⃣ Très bon raisonnement chronologique
    "deepseek/deepseek-r1-0528:free",

    # 5️⃣ Très bon modèle orienté structure / parsing
    "mistralai/devstral-2512:free",

    # 7️⃣ Très bon pour extraction structurée
    "mistralai/mistral-small-3.1-24b-instruct:free",

    # 🔟 Bon fallback seulement
    "google/gemini-2.0-flash-exp:free",
]

MAX_RETRIES_PER_MODEL = 2

# Rate Limiting Configuration (OpenRouter usually handles this, but we keep a safety buffer)
# ============================================================
# Rate Limiting Configuration
# Conservative, production-safe for OpenRouter (free tier)
# Focus: long prompts, structured JSON extraction
# ============================================================

RATE_LIMITS = {
    # 🏆 Élite – très coûteux / quotas stricts
    "openai/gpt-oss-120b:free": {"rpm": 1},  # 1 req/min max (tokens très limités)

    "nousresearch/hermes-3-llama-3.1-405b:free": {"rpm": 2},  # Très lourd, free tier fragile

    # ⭐ Modèle principal – bon équilibre mais lourd
    "meta-llama/llama-3.3-70b-instruct:free": {"rpm": 8},  # Stable jusqu’à ~1 req / 7–8s

    # 🧠 Raisonnement fort, mais parfois throttlé
    "deepseek/deepseek-r1-0528:free": {"rpm": 6},

    # 🧩 Parsing très structuré, bon compromis
    "mistralai/devstral-2512:free": {"rpm": 8},

    # 🧼 JSON propre, assez stable
    "z-ai/glm-4.5-air:free": {"rpm": 8},

    # 🟢 Modèle moyen-haut, bonne stabilité
    "mistralai/mistral-small-3.1-24b-instruct:free": {"rpm": 10},

    # 🟡 Solide mais moins sollicité
    "nvidia/nemotron-3-nano-30b-a3b:free": {"rpm": 10},

    # 🟡 Raisonnement correct, parfois lent
    "allenai/olmo-3.1-32b-think:free": {"rpm": 5},

    # 🔟 Fallback seulement – free tier très limité en burst
    "google/gemini-2.0-flash-exp:free": {"rpm": 5},  # 1 req / ~12s recommandé
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

    def call_ai(self, prompt: str, system_prompt: str = "You are a helpful assistant.", expect_json: bool = False) -> Union[str, Dict[str, Any]]:
        """
        Generic function to call the AI with Multi-Model Fallback via OpenRouter.
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

        logger.info(f"Calling AI (JSON={expect_json}). Prompt length: {len(prompt)}")
        
        start_time = time.time()

        # Iterate through models in priority order
        for model in MODELS:
            # Rate Limit Check
            rate_limiter.wait_for_token(model)
            
            logger.info(f"Trying model: {model}")
            
            for attempt in range(MAX_RETRIES_PER_MODEL):
                try:
                    completion = self.client.chat.completions.create(
                        model=model,
                        messages=messages,
                        temperature=0.1 if expect_json else 0.3,
                        # response_format={"type": "json_object"} if expect_json else None, # REMOVED: Causes 400 errors on free providers
                        extra_headers={
                            "HTTP-Referer": "https://github.com/SonOfZeus1/CV-to-CBZ-CV", 
                            "X-Title": "CV Extraction Pipeline", 
                        },
                    )
                    
                    content = completion.choices[0].message.content
                    duration = time.time() - start_time
                    logger.info(f"AI Response received from {model} in {duration:.2f}s. Length: {len(content)}")

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
                            logger.error(f"Failed to parse JSON from AI response ({model}): {content[:100]}...")
                            if attempt < MAX_RETRIES_PER_MODEL - 1:
                                logger.info("Retrying same model...")
                                continue
                            else:
                                # If JSON parsing fails repeatedly on this model, try next model
                                logger.warning(f"JSON parsing failed for {model}, switching to next model...")
                                break 
                    
                    return content

                except Exception as e:
                    error_str = str(e).lower()
                    # Check for Rate Limit (429)
                    if "429" in error_str or "rate limit" in error_str or "too many requests" in error_str:
                        logger.warning(f"Rate Limit hit for {model}. Switching to next model immediately...")
                        break # Break inner loop -> Try next model in outer loop
                    
                    logger.error(f"AI Call failed for {model} (Attempt {attempt+1}/{MAX_RETRIES_PER_MODEL}): {e}")
                    
                    if attempt < MAX_RETRIES_PER_MODEL - 1:
                        # Exponential backoff for non-rate-limit errors
                        sleep_time = 2 ** (attempt + 1)
                        logger.info(f"Retrying {model} in {sleep_time} seconds...")
                        time.sleep(sleep_time)
                    else:
                        # If we exhausted retries for this model (e.g. 500 error), try next model
                        logger.warning(f"Model {model} failed repeatedly. Switching to next model...")
                        break
        
        logger.error("All models failed.")
        return {} if expect_json else ""

# Global helper function
def call_ai(prompt: str, system_prompt: str = "", expect_json: bool = False) -> Union[str, Dict[str, Any]]:
    client = AIClient.get_instance()
    return client.call_ai(prompt, system_prompt, expect_json)
