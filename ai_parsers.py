import logging
from typing import Any, Dict, List
from ai_client import call_ai

logger = logging.getLogger(__name__)

# --- PROMPTS ---

# --- SINGLE-SHOT FULL CV EXTRACTION (OPENROUTER) ---
FULL_CV_EXTRACTION_SYSTEM_PROMPT = """
You are an expert CV Parser. Your goal is to extract ALL structured data from a CV in a SINGLE pass.
Output STRICT JSON matching the schema below.

CRITICAL RULES:
1. Extract Contact Info (Email, Phone, Name, Languages).
   - DO NOT extract LinkedIn or Social Links.
2. Extract Professional Summary (or generate one if missing).
3. Extract ALL Experience entries.
   - Use "dates_raw" for the exact text found in the CV.
   - Try to normalize "date_start" and "date_end" to YYYY-MM or YYYY.
   - If "Present" or "Aujourd'hui", set "is_current": true.
   - CRITICAL: Extract specific technologies and skills used in EACH experience.
4. Extract Education entries.
5. Extract Projects (if any specific projects are listed outside of experiences).

JSON SCHEMA:
{
  "contact_info": {
    "first_name": "...",
    "last_name": "...",
    "email": "...",
    "phone": "...",
    "address": "...",
    "languages": ["French", "English"]
  },
  "summary": "...",
  "experiences": [
    {
      "job_title": "...",
      "company": "...",
      "location": "...",
      "dates_raw": "...",
      "date_start": "YYYY-MM",
      "date_end": "YYYY-MM",
      "is_current": boolean,
      "summary": "...",
      "tasks": ["Task 1", "Task 2"],
      "skills": ["Java", "Python", "Project Management"]
    }
  ],
  "projects": [
    {
      "name": "...",
      "description": "...",
      "technologies": ["Tech 1", "Tech 2"],
      "dates": "..."
    }
  ],
  "education": [
    {
      "degree": "...",
      "school": "...",
      "year": "..."
    }
  ]
}
"""

FULL_CV_EXTRACTION_USER_PROMPT = """
Here is the full text of a CV. Parse it completely into the requested JSON format.

CV TEXT:
\"\"\"{text}\"\"\"
"""

SUMMARY_SYSTEM_PROMPT = """
You are an expert career consultant. Your goal is to write a single, powerful summary sentence for a CV.
"""

SUMMARY_USER_PROMPT = """
Based on the following experience list, generate a single summary sentence following this EXACT template:

"[Role] [seniority] comptant plus de [X] années d’expérience en [Main Tech/Field], ayant travaillé pour des organisations d’envergure telles que [Company1], [Company2] et [Company3]."

Rules:
1. [Role]: Extract the most common or current role (e.g., Ingénieur logiciel, Développeur Java).
2. [seniority]: Add "senior" if > 5 years, "intermédiaire" if > 2 years, else remove.
3. [X]: Calculate total years of experience from the dates provided.
4. [Main Tech/Field]: The primary technology or field (e.g., développement Java, architecture Cloud).
5. [CompanyList]: List 3-5 most significant/recognizable companies from the list.
6. Output MUST be a JSON object with a single key "generated_summary".

Experiences:
{experiences_text}
"""

def ai_generate_summary(experiences: List[Dict[str, Any]]) -> Dict[str, str]:
    """Generates a dynamic summary based on extracted experiences."""
    if not experiences:
        return {"generated_summary": ""}
        
    # Format experiences for the prompt
    exp_text = ""
    for exp in experiences:
        exp_text += f"- {exp.get('job_title')} at {exp.get('company')} ({exp.get('dates')})\n"
        
    prompt = SUMMARY_USER_PROMPT.format(experiences_text=exp_text)
    return call_ai(prompt, SUMMARY_SYSTEM_PROMPT, expect_json=True)
