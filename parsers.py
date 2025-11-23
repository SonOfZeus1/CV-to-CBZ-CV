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

def heuristic_segmentation(text: str) -> Dict[str, Any]:
    """
    Fallback segmentation using regex keywords if AI fails.
    """
    logger.info("Running Heuristic Segmentation...")
    
    # Normalize text for easier matching
    # We keep original text for extraction but use lower case for finding indices
    text_lower = text.lower()
    
    # Define keywords for sections
    # Note: Order matters less here as we find all indices first
    section_map = {
        "skills_block": ["compétences techniques", "technical skills", "skills", "compétences"],
        "experience_blocks": ["expérience", "experience", "emploi", "employment", "work history"],
        "education_block": ["éducation", "education", "formation", "diplômes", "academic background"],
        "languages_block": ["langues", "languages"]
    }
    
    # Find start indices for each section
    indices = []
    for section, keywords in section_map.items():
        for kw in keywords:
            # We look for the keyword at the start of a line or preceded by newline
            # to avoid matching inside a sentence
            matches = list(re.finditer(r"(^|\n)\s*" + re.escape(kw), text_lower))
            if matches:
                # Take the first match for this section type
                start_idx = matches[0].start()
                indices.append((start_idx, section))
                break
    
    # Sort indices by position
    indices.sort(key=lambda x: x[0])
    
    # If no sections found, return everything as other
    if not indices:
        return {"other_block": text}
        
    segments = {}
    
    # The text before the first section is usually Contact/Header
    if indices[0][0] > 0:
        segments["contact_block"] = text[:indices[0][0]].strip()
        
    # Slice text between indices
    for i in range(len(indices)):
        start_idx, section_name = indices[i]
        
        if i < len(indices) - 1:
            end_idx = indices[i+1][0]
            content = text[start_idx:end_idx].strip()
        else:
            # Last section goes to end of text
            content = text[start_idx:].strip()
            
        # Special handling for experience to make it a list
        if section_name == "experience_blocks":
            # Heuristic split by dates or common job titles is hard without AI.
            # We will just return it as a single block list for now, 
            # and let the per-block AI try to parse it (or fail gracefully).
            # Ideally we would split by date patterns here.
            segments[section_name] = [content]
        else:
            segments[section_name] = content
            
    return segments

def heuristic_parse_experience(block_text: str) -> Dict[str, Any]:
    """
    Attempts to extract experience details using regex if AI fails.
    """
    # Try to find dates
    date_pattern = r"((?:Janvier|Février|Mars|Avril|Mai|Juin|Juillet|Août|Septembre|Octobre|Novembre|Décembre)\s+\d{4}\s*-\s*(?:Aujourd’hui|Présent|(?:Janvier|Février|Mars|Avril|Mai|Juin|Juillet|Août|Septembre|Octobre|Novembre|Décembre)\s+\d{4}))"
    dates_match = re.search(date_pattern, block_text, re.IGNORECASE)
    dates = dates_match.group(1) if dates_match else ""
    
    # Try to find job title (usually first line)
    lines = [l.strip() for l in block_text.split("\n") if l.strip()]
    title = lines[0] if lines else ""
    company = lines[1] if len(lines) > 1 else ""
    
    # If title looks like a date, swap or fix
    if re.match(date_pattern, title, re.IGNORECASE):
        title = "Poste Inconnu"
        
    return {
        "titre_poste": title,
        "entreprise": company,
        "dates": dates,
        "taches": lines[2:] if len(lines) > 2 else [],
        "competences": [] # Hard to extract without AI or known list
    }

def heuristic_parse_contact(text: str) -> Dict[str, Any]:
    """
    Extracts basic contact info using regex.
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    name = lines[0] if lines else ""
    
    # Try to find email
    email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", text)
    email = email_match.group(0) if email_match else ""
    
    # Try to find phone
    phone_match = re.search(r"(\+\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", text)
    phone = phone_match.group(0) if phone_match else ""
    
    # Try to find languages (often labeled)
    languages = []
    lang_match = re.search(r"(?:Langues|Languages)\s*[:\-\n]\s*(.*)", text, re.IGNORECASE)
    if lang_match:
        languages = [l.strip() for l in re.split(r"[,/]", lang_match.group(1)) if l.strip()]
        
    return {
        "name": name,
        "email": email,
        "phone": phone,
        "languages": languages,
        "title": lines[1] if len(lines) > 1 else "" # Assumption: Title is often 2nd line
    }

def heuristic_parse_education(text: str) -> Dict[str, Any]:
    """
    Extracts education info using regex.
    """
    # Very basic extraction: just take the whole block as one entry if we can't parse
    return {
        "education": [{
            "diplome": "Diplôme (Non spécifié)",
            "etablissement": "Établissement (Non spécifié)",
            "annee": "",
            "full_text": text
        }]
    }

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
        logger.warning("Contact extraction failed. Attempting Heuristic Fallback.")
        # Use the contact block from segmentation if available, otherwise use first few lines
        contact_text = clean_text[:1000]
        basics = heuristic_parse_contact(contact_text)
        
    # 4. AI Segmentation
    logger.info("Step 2: Segmenting CV...")
    segments = ai_parse_segmentation(clean_text)
    
    # Check if AI segmentation failed (empty or just other_block)
    is_ai_failed = not segments or (list(segments.keys()) == ["other_block"] and len(segments) == 1)
    
    if is_ai_failed:
        logger.warning("AI Segmentation failed or returned only 'other_block'. Attempting Heuristic Fallback.")
        segments = heuristic_segmentation(clean_text)
        
    if not segments:
        logger.warning("Heuristic segmentation also failed. Using full text as 'other'.")
        segments = {"other_block": clean_text}

    # 5. Process Experience
    logger.info("Step 3: Processing Experience Blocks...")
    structured_experiences = []
    exp_blocks = segments.get("experience_blocks", [])
    if isinstance(exp_blocks, str): # Handle case where AI returns string instead of list
        exp_blocks = [exp_blocks]
        
    for block in exp_blocks:
        if len(block) < 20: continue
        
        # Try AI first
        exp_data = ai_parse_experience_block(block)
        
        # Fallback if AI fails
        if not exp_data:
            logger.warning("AI Experience Parsing failed. Using Heuristic Fallback.")
            exp_data = heuristic_parse_experience(block)
            
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
        
        # Fallback
        if not edu_data or not edu_data.get("education"):
             logger.warning("AI Education Parsing failed. Using Heuristic Fallback.")
             edu_data = heuristic_parse_education(edu_block)
             
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
    
    # 10. Critical Validation
    if not result_dict.get("experience") and "EXPÉRIENCE" in clean_text.upper():
        logger.error("CRITICAL: Experience section missing in JSON but present in text!")
    if not result_dict.get("education") and "ÉDUCATION" in clean_text.upper():
        logger.error("CRITICAL: Education section missing in JSON but present in text!")
    if not result_dict.get("basics", {}).get("name"):
        logger.error("CRITICAL: Name missing in basics!")
        
    return result_dict
