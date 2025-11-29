import os
import json
import logging
import time
from typing import Any, Dict, Optional, Union

from groq import Groq

# Configure logging
logger = logging.getLogger(__name__)

# Constants
# Constants
# Priority list of models (Quality -> Speed/Quota)
MODELS = [
    "llama-3.3-70b-versatile",  # Best Quality (100k TPD)
    "llama-3.1-70b-versatile",  # High Quality (100k TPD)
    "llama3-70b-8192",          # Legacy High Quality (100k TPD)
    "mixtral-8x7b-32768",       # Good Balance (500k TPD)
    "gemma2-9b-it",             # Google Model (500k TPD)
    "llama-3.1-8b-instant",     # Fast/High Quota (500k TPD)
    "llama3-8b-8192"            # Legacy Fast (500k TPD)
]
MAX_RETRIES_PER_MODEL = 2

class AIClient:
    _instance = None

    def __init__(self):
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            logger.warning("GROQ_API_KEY not found. AI features will be disabled.")
            self.client = None
        else:
            self.client = Groq(api_key=api_key)
            logger.info("Groq Client initialized successfully.")

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def call_ai(self, prompt: str, system_prompt: str = "You are a helpful assistant.", expect_json: bool = False) -> Union[str, Dict[str, Any]]:
        """
        Generic function to call the AI with Multi-Model Fallback.
        Iterates through MODELS list if Rate Limits are hit.
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
            logger.info(f"Trying model: {model}")
            
            for attempt in range(MAX_RETRIES_PER_MODEL):
                try:
                    completion = self.client.chat.completions.create(
                        model=model,
                        messages=messages,
                        temperature=0.1 if expect_json else 0.3,
                        response_format={"type": "json_object"} if expect_json else None
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
                                # If JSON parsing fails repeatedly on this model, maybe try next model?
                                # Or just fail. Let's try next model.
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
