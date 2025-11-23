import os
import json
import logging
import time
from typing import Any, Dict, Optional, Union

from groq import Groq

# Configure logging
logger = logging.getLogger(__name__)

# Constants
DEFAULT_MODEL = "llama-3.1-8b-instant"
MAX_RETRIES = 3

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
        Generic function to call the AI.
        
        Args:
            prompt: The user prompt.
            system_prompt: The system prompt.
            expect_json: If True, tries to parse the response as JSON.
            
        Returns:
            String response or Dictionary if expect_json is True.
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

        for attempt in range(MAX_RETRIES):
            try:
                completion = self.client.chat.completions.create(
                    model=DEFAULT_MODEL,
                    messages=messages,
                    temperature=0.1 if expect_json else 0.3,
                    response_format={"type": "json_object"} if expect_json else None
                )
                
                content = completion.choices[0].message.content
                duration = time.time() - start_time
                logger.info(f"AI Response received in {duration:.2f}s. Length: {len(content)}")

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
                        logger.error(f"Failed to parse JSON from AI response: {content[:100]}...")
                        if attempt < MAX_RETRIES - 1:
                            logger.info("Retrying...")
                            continue
                        return {}
                
                return content

            except Exception as e:
                logger.error(f"AI Call failed (Attempt {attempt+1}/{MAX_RETRIES}): {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(1)
                else:
                    return {} if expect_json else ""
        
        return {} if expect_json else ""

# Global helper function
def call_ai(prompt: str, system_prompt: str = "", expect_json: bool = False) -> Union[str, Dict[str, Any]]:
    client = AIClient.get_instance()
    return client.call_ai(prompt, system_prompt, expect_json)
