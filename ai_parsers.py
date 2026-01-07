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
   - DO NOT normalize dates. Copy the exact text strings (e.g. "sept 2018") into "date_start" and "date_end".
   - If "Present" or "Aujourd'hui", set "is_current": true.
   - CRITICAL: Extract specific technologies and skills used in EACH experience.
4. Extract Education entries.
5. Extract Projects (if any specific projects are listed outside of experiences).

JSON SCHEMA:
{
  "is_cv": boolean,
  "total_experience_declared": "string or null (e.g. '10 ans', '5+ years' FOUND explicitly in intro)",
  "contact_info": {
    "first_name": "...",
    "last_name": "...",
    "email": "...",
    "phone": "...",
    "address": "...",
    "languages": ["French", "English"]
  },
  "experiences": [
    {
      "job_title": "...",
      "company": "...",
      "location": "...",
      "dates_raw": "...",
      "date_start": "Exact text found (e.g. 'Sept 2018', '01/20')",
      "date_end": "Exact text found (e.g. 'Jan 2020', 'Present')",
      "is_current": boolean,
      "description": "..."
    }
  ],
  "projects_and_other": [
    "Project 1 or Block 1 details...",
    "Project 2 or Block 2 details..."
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
You are provided with TWO complementary text sources.
YOUR GOAL: Map ALL text from the MARKDOWN source into the correct JSON fields, using the PDF source as a structural guide.

SOURCE 1: MARKDOWN TEXT (CONTENT)
- Contains the exact text strings you must use in the JSON.
- Every text block here must be preserved in the output.

SOURCE 2: PDF TEXT (STRUCTURE GUIDE)
- Use this to correctly categorize the Markdown text.
- Example: If the Markdown has a text block "Java, Python", look at the PDF layout to decide if this belongs to "Skills" or a specific "Experience".

*** STRICT RULES ***
1. CONTENT SOURCE: All "description" and "dates" in the JSON must be COPIED exactly from Source 1 (Markdown).
2. STRUCTURE SOURCE: Use Source 2 (PDF) only to determine which JSON list (Experience, Education, Projects) a block belongs to.
3. COMPLETENESS: Ensure ALL text present in the Markdown file is found somewhere in the JSON.
4. If a block's category is ambiguous in Markdown, defer to the PDF's visual layout to classify it.

*** ANCHOR MAP (Derived from Source 1) ***
1. "anchors": Validated Dates/Entities.
2. "blocks": Pre-segmented Markdown blocks.

ANCHOR MAP:
---
{anchor_map}
---

CV DUAL-SOURCE TEXT:
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

def parse_cv_full_text(text: str, anchor_map: Dict = None) -> Dict[str, Any]:
    """
    Parses the full text of a CV into structured JSON using the Single-Shot prompt.
    """
    if not text:
        return {}
        
    # Format anchor map for prompt
    anchor_text = "No pre-computed anchors available."
    if anchor_map:
        import json
        anchor_text = json.dumps(anchor_map, indent=2, ensure_ascii=False)
    
    prompt = FULL_CV_EXTRACTION_USER_PROMPT.format(
        anchor_map=anchor_text,
        text=text
    )
    
    return call_ai(prompt, FULL_CV_EXTRACTION_SYSTEM_PROMPT, expect_json=True)

# --- DIRECT METRICS EXTRACTION (TEXT-BASED / MISTRAL) ---
DIRECT_METRICS_SYSTEM_PROMPT = """
You are an expert HR Analyst. Your goal is to determine the TOTAL years of professional experience from a CV.
Output STRICT JSON.

METRIC TO EXTRACT:
"years_experience": The TOTAL number of years of professional experience. 
   - Step 1: Check again for EXPLICIT statements (e.g., "10 years exp") just in case.
   - Step 2: If no explicit statement, CAREFULLY CALCULATE the duration by summing the time ranges of all relevant professional roles found in the text.
   - Ignore overlapping dates (count them once).
   - Ignore education, volunteer work, or non-relevant gaps.
   - Return a FLOAT (e.g., 5.5).

JSON SCHEMA:
{
  "years_experience": float
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
