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

# ... (Rate Limits remain mostly same, just ensuring keys exist) ...
RATE_LIMITS = {
    "meta-llama/llama-3.3-70b-instruct:free": {"rpm": 8},
    "google/gemini-2.0-flash-exp:free": {"rpm": 10},
    "mistralai/mistral-small-3.1-24b-instruct:free": {"rpm": 10},
    "mistralai/devstral-2512:free": {"rpm": 8},
    "nousresearch/hermes-3-llama-3.1-405b:free": {"rpm": 2},
}

# ...

            for attempt in range(MAX_RETRIES_PER_MODEL):
                try:
                    completion = self.client.chat.completions.create(
                        model=model,
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
