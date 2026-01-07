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
0. CLASSIFICATION: Determine if the document is a CV. 
   - If it is a CV, set "is_cv": true.
   - If it is NOT a CV (e.g., cover letter, invoice, code, empty file), set "is_cv": false. You may leave other fields empty or minimal.
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
  "is_cv": boolean,
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

*** STRICT ANCHORING INSTRUCTIONS ***
You are provided with an "ANCHOR MAP" below. This map contains:
1. "anchors": Validated Dates, Roles, and Companies found in the text.
2. "blocks": Pre-segmented text blocks (especially for Experience).

RULES:
1. For each "experience" entry, you MUST reference the `block_id` it comes from.
2. You MUST use the `date_anchor_id` if the date matches an anchor.
3. DO NOT invent dates. If a date is not in the anchors, be very careful.
4. The `skills` list for an experience must be derived ONLY from the text in that block.

ANCHOR MAP:
---
{anchor_map}
---

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

def parse_cv_full_text(text: str) -> Dict[str, Any]:
    """
    Parses the full text of a CV into structured JSON using the Single-Shot prompt.
    """
    if not text:
        return {}
        
    # We don't have an anchor map in this simple flow, so we pass an empty one or modify the prompt.
    # The prompt expects {anchor_map}, so we must provide it.
    # For now, we'll pass a placeholder saying "No anchors provided".
    
    prompt = FULL_CV_EXTRACTION_USER_PROMPT.format(
        anchor_map="No pre-computed anchors available.",
        text=text
    )
    
    return call_ai(prompt, FULL_CV_EXTRACTION_SYSTEM_PROMPT, expect_json=True)

# --- DIRECT METRICS EXTRACTION (TEXT-BASED / MISTRAL) ---
DIRECT_METRICS_SYSTEM_PROMPT = """
You are an expert HR Analyst. Your goal is to extract TWO specific metrics from the CV text.
Output STRICT JSON.

METRICS TO EXTRACT:
1. "years_experience": The TOTAL number of years of professional experience. 
   - PRIORITY 1: Look for EXPLICIT statements in the Introduction, Summary, or Profile (e.g., "Over 10 years of experience", "5+ ans d'expérience"). 
   - IF FOUND: Use this number immediately. Do not calculate dates.
   - IF NOT FOUND: Calculate the duration from the start date of the first relevant professional role to Today.
   - Return a FLOAT (e.g., 5.5).
2. "latest_job_title": The Most Recent or Current Job Title.
   - Look for the role with "Present", "Current", or the latest end date.

JSON SCHEMA:
{
  "years_experience": float,
  "latest_job_title": "string"
}
"""

DIRECT_METRICS_USER_PROMPT = """
Analyze this CV text and extract the years of experience and latest job title.

CV TEXT:
\"\"\"{text}\"\"\"
"""

def parse_cv_direct_metrics(text: str, model: str = None) -> Dict[str, Any]:
    """
    Parses specific metrics directly from text, optionally specifying a model.
    """
    if not text:
        return {}
    
    prompt = DIRECT_METRICS_USER_PROMPT.format(text=text)
    # Pass model to call_ai if supported, otherwise rely on default or modify call_ai to accept it?
    # ai_client.call_ai needs to be updated or we check if it accepts kwargs/model.
    # checking ai_client.py... it does NOT accept model in signature shown previously.
    # I will need to update ai_client.py first or pass it if I missed it.
    # Assuming I will update ai_client.py next.
    return call_ai(prompt, DIRECT_METRICS_SYSTEM_PROMPT, expect_json=True, model=model)

def parse_cv_metrics_multi_model(text: str) -> Dict[str, str]:
    """
    Extracts metrics using 3 different models and returns a formatted string for each metric.
    Format:
    1. [Value] - [Model]
    2. [Value] - [Model]
    3. [Value] - [Model]
    """
    if not text:
        return {"years_experience": "", "latest_job_title": ""}

    # Models to use
    models = [
        "google/gemini-2.0-flash-exp:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "mistralai/mistral-7b-instruct:free"
    ]

    results = []
    
    # Use ThreadPoolExecutor for parallel execution to save time
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        future_to_model = {
            executor.submit(call_ai, DIRECT_METRICS_USER_PROMPT.format(text=text[:50000]), DIRECT_METRICS_SYSTEM_PROMPT, True, model): model 
            for model in models
        }
        
        for future in concurrent.futures.as_completed(future_to_model):
            model_name = future_to_model[future]
            short_name = model_name.split("/")[1].split("-")[0].capitalize() # e.g. Gemini, Llama, Mistral
            try:
                data = future.result()
                if isinstance(data, dict):
                    results.append((short_name, data))
                else:
                    results.append((short_name, {}))
            except Exception as e:
                logger.error(f"Multi-model failed for {model_name}: {e}")
                results.append((short_name, {}))

    # Sort results to maintain consistent order (optional, but good for readability)
    # Actually results come in random order of completion. Let's rely on list append order or sort by name?
    # Sorting by name makes it stable.
    results.sort(key=lambda x: x[0])

    # Format Output
    exp_lines = []
    title_lines = []
    
    for i, (model, data) in enumerate(results):
        # Experience
        exp_val = data.get("years_experience", "N/A")
        exp_lines.append(f"{i+1}. {exp_val} - {model}")
        
        # Title
        title_val = data.get("latest_job_title", "N/A")
        title_lines.append(f"{i+1}. {title_val} - {model}")

    return {
        "years_experience": "\n".join(exp_lines),
        "latest_job_title": "\n".join(title_lines)
    }
