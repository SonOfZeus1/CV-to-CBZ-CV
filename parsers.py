import logging
import os
import re
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
from datetime import datetime
import dateparser

from ai_client import call_ai
from ai_parsers import (
    FULL_CV_EXTRACTION_SYSTEM_PROMPT,
    FULL_CV_EXTRACTION_USER_PROMPT,
    parse_cv_full_text
)
from text_processor import preprocess_markdown
from date_extractor import extract_date_anchors
from entity_extractor import extract_entity_anchors
from segmenter import segment_cv
from validator import validate_extraction
import json

# Configure logging
logger = logging.getLogger(__name__)

@dataclass
class ExperienceEntry:
    job_title: str = ""
    company: str = ""
    location: str = ""
    dates: str = "" 
    dates_raw: str = "" 
    date_start: str = "" 
    date_end: str = ""   
    is_current: bool = False
    duration: str = ""
    description: str = "" # Unified description field
    full_text: str = ""
    block_id: str = ""
    anchor_ids: List[str] = field(default_factory=list)

# ... (Previous code)


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
    skills_tech: List[str]
    experience: List[ExperienceEntry]
    education: List[EducationEntry]
    projects_and_other: List[str]
    is_cv: bool = True

    def to_dict(self):
        return {
            "meta": self.meta,
            "basics": self.basics,
            "skills_tech": self.skills_tech,
            "experience": [asdict(e) for e in self.experience],
            "education": [asdict(e) for e in self.education],
            "projects_and_other": self.projects_and_other,
            "is_cv": self.is_cv
        }


def calculate_months_between(start_str: str, end_str: str, is_current: bool) -> int:
    """
    Calculates months between two YYYY-MM dates.
    If date is missing, returns 0.
    """
    if not start_str:
        return 0
    
    try:
        start_date = datetime.strptime(start_str, "%Y-%m")
    except ValueError:
        return 0

    end_date = datetime.now()
    if not is_current and end_str:
        try:
            end_date = datetime.strptime(end_str, "%Y-%m")
        except ValueError:
            pass # Keep as now or return 0? Let's assume 0 duration if end is invalid and not current.
            # But "Present" logic matches is_current.
            
    # If not current and no valid end date, we can't calculate duration.
    if not is_current and not end_str:
        return 0
        
    # Calculate difference
    # We use relativedelta logic essentially: (year_diff * 12) + month_diff
    years = end_date.year - start_date.year
    months = end_date.month - start_date.month
    total = years * 12 + months
    
    return max(0, total)


def parse_cv_from_text(text: str, filename: str = "", metadata: Dict = None) -> Dict[str, Any]:
    """
    Main entry point for CV parsing.
    Now uses Single-Shot Strategy by default.
    """
    logger.info(f"--- Starting Single-Shot Parsing for {filename} ---")
    
    # 1. Preprocess Text
    clean_text = preprocess_markdown(text)
    
    # 2. Rich Anchor Extraction & Segmentation
    logger.info("Extracting Anchors and Segments...")
    date_anchors = extract_date_anchors(clean_text)
    entity_anchors = extract_entity_anchors(clean_text)
    blocks = segment_cv(clean_text, date_anchors, entity_anchors)
    
    # Build Anchor Map
    anchor_map = {
        "anchors": {
            "dates": [asdict(a) for a in date_anchors],
            "entities": [asdict(a) for a in entity_anchors]
        },
        "blocks": [asdict(b) for b in blocks]
    }
    
    # 3. Extract Data (Single Shot with Anchors)
    extracted_data = parse_cv_full_text(clean_text, anchor_map=anchor_map)
    
    if not extracted_data:
        logger.warning("Single-Shot failed completely. Returning empty structure.")
        return {
            "meta": {"filename": filename},
            "basics": {},
            "skills_tech": [],
            "experience": [],
            "education": [],
            "projects_and_other": [],
            "is_cv": False
        }
    
    # 3. Map to Internal Schema (CVData)
    contact = extracted_data.get("contact_info", {})
    
    # Language Fallback
    languages = contact.get("languages", [])
    if not languages and metadata and metadata.get("language"):
        lang_code = metadata.get("language").upper()
        if lang_code == "EN":
            languages = ["Anglais"]
        elif lang_code == "FR":
            languages = ["Français"]
        else:
            languages = [metadata.get("language")]

    # Experiences
    structured_experiences = []
    
    # Helper to parse dates robustly
    def clean_parse_date(raw_date: str) -> str:
        if not raw_date: return ""
        dt = dateparser.parse(raw_date, languages=['fr', 'en'], settings={'PREFER_DAY_OF_MONTH': 'first'})
        if dt:
            return dt.strftime("%Y-%m")
        return "" # Fail gracefully
        
    for item in extracted_data.get("experiences", []):
        matches = item # AI dict
        
        # Raw from AI (Copy-Paste)
        raw_start = matches.get("date_start", "") 
        raw_end = matches.get("date_end", "")
        
        # Parse Logic
        norm_start = clean_parse_date(raw_start)
        norm_end = clean_parse_date(raw_end)
        
        entry = ExperienceEntry(
            job_title=matches.get("job_title", ""),
            company=matches.get("company", ""),
            location=matches.get("location", ""),
            dates=matches.get("dates_raw", ""),
            dates_raw=matches.get("dates_raw", ""),
            date_start=norm_start, # Normalized by Python
            date_end=norm_end,     # Normalized by Python
            is_current=matches.get("is_current", False),
            duration="", 
            description=matches.get("description", ""), 
            full_text="Generated via Single-Shot",
            block_id=matches.get("block_id", ""),
            anchor_ids=matches.get("anchor_ids", [])
        )
        structured_experiences.append(entry)

    # Calculate Total Experience
    declared_exp = extracted_data.get("total_experience_declared")
    total_months = 0
    for exp in structured_experiences:
        months = calculate_months_between(exp.date_start, exp.date_end, exp.is_current)
        total_months += months
        
    calculated_exp = round(total_months / 12, 1)

    # Post-Process: specific logic for 'is_current'
    # "Only the most recent experience can be current. All others must be false."
    if structured_experiences:
        # Sort by date_start desc (String sort works for ISO YYYY-MM)
        # Handle empty dates safely by treating them as old
        structured_experiences.sort(key=lambda x: x.date_start or "", reverse=True)
        
        # The first one *might* be current. The rest are definitely not.
        for i, exp in enumerate(structured_experiences):
            if i > 0:
                exp.is_current = False
                # User says: "If date without range... IsCurrent must be false"
                # By forcing False here, we handle the "anomaly" case for old roles.


    basics = {
        "name": f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip(),
        "email": contact.get("email", ""),
        "phone": contact.get("phone", ""),
        "address": contact.get("address", ""),
        "languages": languages,
        "total_experience_declared": declared_exp if declared_exp else "N/A",
        "total_experience_calculated": calculated_exp
    }
    
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
        
    # Skills (Removed per user request)
    skills_tech = []
    
    # Projects
    projects = extracted_data.get("projects_and_other", [])

    # Assemble CVData
    cv_data = CVData(
        meta={"filename": filename},
        basics=basics,
        skills_tech=skills_tech,
        experience=structured_experiences,
        education=education_entries,
        projects_and_other=projects,
        is_cv=True
    )
    
    result_dict = cv_data.to_dict()
    
    # Quality Check
    logger.info(f"Successfully extracted {len(structured_experiences)} experiences.")
    
    # 4. Validation
    validation_issues = validate_extraction(result_dict, anchor_map)
    if validation_issues:
        logger.warning(f"Validation Issues found: {len(validation_issues)}")
        for issue in validation_issues:
            logger.warning(f"- {issue}")
            
    # Add issues to meta
    result_dict["meta"]["validation_issues"] = validation_issues
    
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
