import logging
import os
import re
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional

from ai_client import call_ai
from ai_parsers import (
    FULL_CV_EXTRACTION_SYSTEM_PROMPT,
    FULL_CV_EXTRACTION_USER_PROMPT
)
from text_processor import preprocess_markdown

# Configure logging
logger = logging.getLogger(__name__)

@dataclass
class ExperienceEntry:
    job_title: str = ""
    company: str = ""
    location: str = ""
    dates: str = "" # Original text
    dates_raw: str = "" # Exact raw string from date extraction
    date_start: str = "" # ISO YYYY-MM or YYYY
    date_end: str = ""   # ISO YYYY-MM or YYYY or None
    date_precision: str = "unknown" # "month", "year", "unknown"
    is_current: bool = False
    duration: str = ""
    summary: str = ""
    tasks: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    full_text: str = ""

@dataclass
class EducationEntry:
    degree: str = ""
    institution: str = ""
    date_start: str = ""
    date_end: str = ""
    full_text: str = ""

@dataclass
class CVData:
    meta: Dict[str, Any]
    basics: Dict[str, Any]
    links: List[str]
    summary: str
    skills_tech: List[str]
    experience: List[ExperienceEntry]
    education: List[EducationEntry]
    languages: List[str]
    extra_info: List[str]
    unmapped: List[str]

    def to_dict(self):
        return {
            "meta": self.meta,
            "basics": self.basics,
            "links": self.links,
            "summary": self.summary,
            "skills_tech": self.skills_tech,
            "experience": [asdict(e) for e in self.experience],
            "education": [asdict(e) for e in self.education],
            "languages": self.languages,
            "extra_info": self.extra_info,
            "unmapped": self.unmapped
        }

def parse_cv_full_text(text: str) -> Dict[str, Any]:
    """
    Parses the entire CV in a single pass using OpenRouter (MiMo/GPT-OSS).
    """
    logger.info("Step 2: Single-Shot Full CV Extraction...")
    
    # Call AI with the full text
    prompt = FULL_CV_EXTRACTION_USER_PROMPT.format(text=text[:100000]) # Huge context limit
    raw_data = call_ai(prompt, FULL_CV_EXTRACTION_SYSTEM_PROMPT, expect_json=True)
    
    # Validate and Normalize Data
    if not raw_data:
        logger.error("Single-Shot Extraction failed (Empty response).")
        return {}

    # Ensure 'experiences' are robust
    experiences = raw_data.get("experiences", [])
    for exp in experiences:
        # Ensure dates_raw is present
        if "dates_raw" not in exp:
            exp["dates_raw"] = f"{exp.get('date_start', '')} - {exp.get('date_end', '')}"
            
    return raw_data

def parse_cv_from_text(text: str, filename: str = "", metadata: Dict = None) -> Dict[str, Any]:
    """
    Main entry point for CV parsing.
    Now uses Single-Shot Strategy by default.
    """
    logger.info(f"--- Starting Single-Shot Parsing for {filename} ---")
    
    # 1. Preprocess Text
    clean_text = preprocess_markdown(text)
    
    # 2. Extract Data (Single Shot)
    extracted_data = parse_cv_full_text(clean_text)
    
    if not extracted_data:
        logger.warning("Single-Shot failed completely. Returning empty structure.")
        return {
            "meta": {"filename": filename},
            "basics": {},
            "links": [],
            "summary": "",
            "skills_tech": [],
            "experience": [],
            "education": [],
            "languages": [],
            "extra_info": [],
            "unmapped": []
        }
    
    # 3. Map to Internal Schema (CVData)
    # Contact Info -> Basics
    contact = extracted_data.get("contact_info", {})
    basics = {
        "name": f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip(),
        "email": contact.get("email", ""),
        "phone": contact.get("phone", ""),
        "linkedin": contact.get("linkedin", ""),
        "address": contact.get("address", ""),
        "summary": extracted_data.get("summary", "")
    }
    
    # Experiences
    structured_experiences = []
    for item in extracted_data.get("experiences", []):
        entry = ExperienceEntry(
            job_title=item.get("job_title", ""),
            company=item.get("company", ""),
            location=item.get("location", ""),
            dates=item.get("dates_raw", ""),
            dates_raw=item.get("dates_raw", ""),
            date_start=item.get("date_start", ""),
            date_end=item.get("date_end", ""),
            date_precision="unknown", # AI inferred
            is_current=item.get("is_current", False),
            duration="", # Can calculate if needed
            summary=item.get("summary", ""),
            tasks=item.get("tasks", []),
            skills=[],
            full_text="Generated via Single-Shot"
        )
        structured_experiences.append(entry)
        
    # Education
    education_entries = []
    for item in extracted_data.get("education", []):
        education_entries.append(EducationEntry(
            degree=item.get("degree", ""),
            institution=item.get("school", ""),
            date_start=item.get("year", ""), # Often just year
            date_end="",
            full_text=str(item)
        ))
        
    # Skills
    skills_tech = extracted_data.get("skills", [])
    
    # Assemble CVData
    cv_data = CVData(
        meta={"filename": filename, "ocr_applied": str(metadata.get("ocr_applied", "False") if metadata else "False")},
        basics=basics,
        links=[basics.get("linkedin")] if basics.get("linkedin") else [],
        summary=basics.get("summary", ""),
        skills_tech=skills_tech,
        experience=structured_experiences,
        education=education_entries,
        languages=[], # Could extract from skills if labeled
        extra_info=[],
        unmapped=[]
    )
    
    result_dict = cv_data.to_dict()
    
    # Quality Check
    logger.info(f"Successfully extracted {len(structured_experiences)} experiences.")
    
    return result_dict

def parse_cv(file_path: str) -> Optional[dict]:
    filename = os.path.basename(file_path)
    _, extension = os.path.splitext(filename)
    
    # 1. Extract Text
    text = ""
    ocr_applied = False
    
    # Simple text reading for .md (assuming extraction happens elsewhere or file is .md)
    if extension.lower() == ".md":
         try:
             with open(file_path, "r", encoding="utf-8") as f:
                 text = f.read()
         except Exception as e:
             logger.error(f"Failed to read file {file_path}: {e}")
             return None
    
    if not text.strip():
        logger.error("Empty text extracted or unsupported file type.")
        return None

    # 2. Delegate to parse_cv_from_text
    return parse_cv_from_text(text, filename, metadata={"ocr_applied": ocr_applied})
