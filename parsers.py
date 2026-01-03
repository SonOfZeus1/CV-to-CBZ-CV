import logging
import os
import re
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
from datetime import datetime

from ai_client import call_ai
from ai_parsers import (
    FULL_CV_EXTRACTION_SYSTEM_PROMPT,
    FULL_CV_EXTRACTION_USER_PROMPT
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
    block_id: str = ""
    anchor_ids: List[str] = field(default_factory=list)

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
    summary: str
    skills_tech: List[str]
    experience: List[ExperienceEntry]
    education: List[EducationEntry]
    projects: List[Dict[str, Any]]
    extra_info: List[str]
    unmapped: List[str]

    def to_dict(self):
        return {
            "meta": self.meta,
            "basics": self.basics,
            "summary": self.summary,
            "skills_tech": self.skills_tech,
            "experience": [asdict(e) for e in self.experience],
            "education": [asdict(e) for e in self.education],
            "projects": self.projects,
            "extra_info": self.extra_info,
            "unmapped": self.unmapped
        }

def calculate_months_between(start_str: str, end_str: str, is_current: bool) -> int:
    """Calculates months between two dates (YYYY-MM or YYYY)."""
    if not start_str:
        return 0
        
    try:
        # Normalize start
        if len(start_str) == 4:
            start_date = datetime.strptime(start_str, "%Y")
        else:
            start_date = datetime.strptime(start_str, "%Y-%m")
            
        # Determine end
        if is_current:
            end_date = datetime.now()
        elif end_str:
            if len(end_str) == 4:
                end_date = datetime.strptime(end_str, "%Y")
            else:
                end_date = datetime.strptime(end_str, "%Y-%m")
        else:
            return 0 # No end date and not current -> cannot calculate
            
        # Calculate difference
        months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)
        return max(0, months)
        
    except ValueError:
        return 0

def parse_cv_full_text(text: str, anchor_map: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Parses the entire CV in a single pass using OpenRouter (MiMo/GPT-OSS).
    """
    logger.info("Step 2: Single-Shot Full CV Extraction...")
    
    # Format Anchor Map
    anchor_map_str = json.dumps(anchor_map, indent=2, ensure_ascii=False) if anchor_map else "{}"
    
    # Call AI with the full text and anchor map
    prompt = FULL_CV_EXTRACTION_USER_PROMPT.format(text=text[:100000], anchor_map=anchor_map_str) # Huge context limit
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
        "address": contact.get("address", ""),
        "languages": contact.get("languages", []),
        "summary": extracted_data.get("summary", "")
    }
    
    # Calculate Total Experience
    total_months = 0
    for exp in structured_experiences:
        months = calculate_months_between(exp.date_start, exp.date_end, exp.is_current)
        total_months += months
        
    basics["total_experience"] = round(total_months / 12, 1)
    
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
            skills=item.get("skills", []),
            full_text="Generated via Single-Shot",
            block_id=item.get("block_id", ""),
            anchor_ids=item.get("anchor_ids", [])
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
        
    # Skills (Aggregated from Experiences)
    # The user wants skills_tech to be ONLY based on skills from experiences.
    # So we iterate through structured_experiences and collect all skills.
    all_skills = set()
    for exp in structured_experiences:
        for skill in exp.skills:
            all_skills.add(skill)
            
    skills_tech = sorted(list(all_skills))
    
    # Projects
    projects = extracted_data.get("projects", [])

    # Assemble CVData
    cv_data = CVData(
        meta={"filename": filename},
        basics=basics,
        summary=basics.get("summary", ""),
        skills_tech=skills_tech,
        experience=structured_experiences,
        education=education_entries,
        projects=projects,
        extra_info=[],
        unmapped=[]
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
