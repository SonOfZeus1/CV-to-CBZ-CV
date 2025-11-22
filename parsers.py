import io
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import docx
import fitz  # PyMuPDF
import pytesseract
from PIL import Image

# New AI Modules
from ai_parsers import (
    ai_parse_contact,
    ai_parse_segmentation,
    ai_parse_experience_block,
    ai_parse_education
)

logger = logging.getLogger(__name__)

# --- DATA SCHEMA ---

@dataclass
class ExperienceEntry:
    job_title: str = ""
    company: str = ""
    location: str = ""
    dates: str = ""
    duration: str = ""
    summary: str = ""
    tasks: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    full_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class EducationEntry:
    degree: str = ""
    institution: str = ""
    date_start: str = "" # Often just "Year"
    date_end: str = ""
    full_text: str = ""

@dataclass
class CVData:
    meta: Dict[str, str] = field(default_factory=dict)
    basics: Dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    skills_tech: List[str] = field(default_factory=list)
    skills_soft: List[str] = field(default_factory=list)
    experience: List[ExperienceEntry] = field(default_factory=list)
    education: List[EducationEntry] = field(default_factory=list)
    languages: List[str] = field(default_factory=list)
    links: List[str] = field(default_factory=list)
    achievements_global: List[str] = field(default_factory=list)
    extra_info: List[str] = field(default_factory=list)
    unmapped: List[str] = field(default_factory=list)
    raw_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["experience"] = [exp.to_dict() for exp in self.experience]
        payload["education"] = [asdict(edu) for edu in self.education]
        return payload

# --- TEXT EXTRACTION ---

def extract_text_from_docx(file_path: str) -> str:
    try:
        doc = docx.Document(file_path)
        return "\n".join([para.text for para in doc.paragraphs])
    except Exception as exc:
        logger.warning("DOCX extraction failed (%s): %s", file_path, exc)
        return ""

def extract_text_from_pdf(file_path: str) -> tuple[str, bool]:
    text = ""
    ocr_applied = False
    try:
        with fitz.open(file_path) as doc:
            for page in doc:
                text += page.get_text()
            avg_chars_per_page = len(text.strip()) / len(doc) if len(doc) > 0 else 0
            if avg_chars_per_page < 50:
                text = ""
                ocr_applied = True
                for page_num in range(len(doc)):
                    page = doc.load_page(page_num)
                    pix = page.get_pixmap(dpi=150)
                    img_bytes = pix.tobytes("png")
                    image = Image.open(io.BytesIO(img_bytes))
                    text += pytesseract.image_to_string(image) + "\n"
    except Exception as exc:
        logger.error("PDF extraction failed (%s): %s", file_path, exc)
    return text, ocr_applied

# --- CORE PIPELINE ---

def pre_process_text(text: str) -> str:
    """Removes repetitive headers/footers."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    cleaned_lines = []
    patterns_to_remove = [
        r"^page\s*\d+\s*(/|sur|of)\s*\d+$",
        r"^document\s+généré\s+automatiquement",
        r"^curriculum\s*vitae$",
        r"^cv$",
    ]
    
    for line in lines:
        if any(re.match(pat, line, re.IGNORECASE) for pat in patterns_to_remove):
            continue
        cleaned_lines.append(line)
    
    return "\n".join(cleaned_lines)

def verify_content_coverage(cv_data: Dict[str, Any], raw_text: str):
    """Checks if generated content covers the raw text."""
    captured_text = ""
    
    # Basics
    basics = cv_data.get("basics", {})
    captured_text += f"{basics.get('name', '')} {basics.get('email', '')} {basics.get('phone', '')} "
    
    # Experience
    for exp in cv_data.get("experience", []):
        captured_text += f"{exp.get('job_title', '')} {exp.get('company', '')} {exp.get('dates', '')} "
        captured_text += " ".join(exp.get("tasks", [])) + " "
        captured_text += " ".join(exp.get("skills", [])) + " "
    
    # Education
    for edu in cv_data.get("education", []):
        captured_text += f"{edu.get('degree', '')} {edu.get('institution', '')} "

    # Skills
    captured_text += " ".join(cv_data.get("skills_tech", [])) + " "
    
    # Extra
    captured_text += " ".join(cv_data.get("extra_info", [])) + " "

    def normalize(s):
        return re.sub(r"\s+", "", s.lower())

    raw_norm = normalize(raw_text)
    captured_norm = normalize(captured_text)

    if len(raw_norm) > 0:
        ratio = len(captured_norm) / len(raw_norm)
        logger.info(f"Content Coverage Ratio: {ratio:.2f}")
        if ratio < 0.6:
            logger.warning("ALERTE: Coverage < 60%. Some content might be missing.")

def parse_cv(file_path: str) -> Optional[dict]:
    filename = os.path.basename(file_path)
    _, extension = os.path.splitext(filename)
    
    # 1. Extract Text
    text, ocr_applied = "", False
    if extension.lower() == ".pdf":
        text, ocr_applied = extract_text_from_pdf(file_path)
    elif extension.lower() == ".docx":
        text = extract_text_from_docx(file_path)
    
    if not text.strip():
        logger.error("Empty text extracted.")
        return None

    # 2. Clean Text
    clean_text = pre_process_text(text)
    
    # 3. AI Contact Extraction
    logger.info("Step 1: Extracting Contact Info...")
    basics = ai_parse_contact(clean_text)
    if not basics:
        logger.warning("Contact extraction failed.")
        basics = {}

    # 4. AI Segmentation
    logger.info("Step 2: Segmenting CV...")
    segments = ai_parse_segmentation(clean_text)
    if not segments:
        logger.warning("Segmentation failed. Using full text as 'other'.")
        segments = {"other_block": clean_text}

    # 5. Process Experience
    logger.info("Step 3: Processing Experience Blocks...")
    structured_experiences = []
    exp_blocks = segments.get("experience_blocks", [])
    if isinstance(exp_blocks, str): # Handle case where AI returns string instead of list
        exp_blocks = [exp_blocks]
        
    for block in exp_blocks:
        if len(block) < 20: continue
        exp_data = ai_parse_experience_block(block)
        if exp_data:
            entry = ExperienceEntry(
                job_title=exp_data.get("titre_poste", ""),
                company=exp_data.get("entreprise", ""),
                location=exp_data.get("localisation", ""),
                dates=exp_data.get("dates", ""),
                duration=exp_data.get("duree", ""),
                summary=exp_data.get("resume", ""),
                tasks=exp_data.get("taches", []),
                skills=exp_data.get("competences", []),
                full_text=block
            )
            structured_experiences.append(entry)

    # 6. Process Education
    logger.info("Step 4: Processing Education...")
    education_entries = []
    edu_block = segments.get("education_block", "")
    if edu_block:
        edu_data = ai_parse_education(edu_block)
        for item in edu_data.get("education", []):
            education_entries.append(EducationEntry(
                degree=item.get("diplome", ""),
                institution=item.get("etablissement", ""),
                date_start=item.get("annee", ""),
                full_text=str(item)
            ))

    # 7. Process Skills & Extra
    skills_tech = []
    skills_block = segments.get("skills_block", "")
    # Simple split for now, or we could use AI to listify. 
    # Let's just keep it as a list of lines/words for now or use a simple heuristic.
    # Actually, let's just split by comma/newline for the list.
    if skills_block:
        skills_tech = [s.strip() for s in re.split(r"[,•\n]", skills_block) if s.strip()]

    extra_info = []
    other_block = segments.get("other_block", "")
    if other_block:
        extra_info = [other_block]

    # 8. Assemble CV Data
    cv_data = CVData(
        meta={"filename": filename, "ocr_applied": str(ocr_applied)},
        basics=basics,
        links=[basics.get("linkedin")] if basics.get("linkedin") else [],
        summary=basics.get("summary", ""), # Sometimes summary is in basics or separate
        skills_tech=skills_tech,
        experience=structured_experiences,
        education=education_entries,
        languages=basics.get("languages", []),
        extra_info=extra_info,
        raw_text=text
    )

    result_dict = cv_data.to_dict()
    
    # 9. Verify Coverage
    verify_content_coverage(result_dict, clean_text)
    
    return result_dict
