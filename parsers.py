import io
import time
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import docx
import fitz  # PyMuPDF
import pytesseract
from PIL import Image

# New AI Modules
from ai_parsers import (
    ai_parse_contact,
    ai_parse_segmentation,
    ai_parse_experience_block,
    ai_parse_education,
    ai_generate_summary
)
import dateparser
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

logger = logging.getLogger(__name__)

# --- HELPERS ---

def calculate_duration_string(start_str: str, end_str: str) -> str:
    """
    Calculates duration from start and end date strings.
    Returns a string like "2 ans 3 mois".
    Uses strict parsing: 1st of month for start, last of month for end (if day missing).
    """
    if not start_str or not end_str:
        return ""
        
    now = datetime.now()
    end_date = None
    
    # Handle "Present"
    if re.match(r'(?i)^(aujourd\'hui|présent|present|current|now|ce jour)$', end_str):
        end_date = now
    elif re.match(r'^\d{4}$', end_str):
        end_date = datetime(int(end_str), 12, 31)
    else:
        # End date: prefer last day of month
        end_date = dateparser.parse(end_str, languages=['fr', 'en'], settings={'PREFER_DAY_OF_MONTH': 'last'})
            
    # Start date
    if re.match(r'^\d{4}$', start_str):
        start_date = datetime(int(start_str), 1, 1)
    else:
        start_date = dateparser.parse(start_str, languages=['fr', 'en'], settings={'PREFER_DAY_OF_MONTH': 'first'})

    if not start_date or not end_date:
        return ""
    
    if end_date < start_date:
        return ""

    # Logic for inclusive months
    # If end date was parsed as end of month (no specific day in input), add 1 day for inclusive calc
    # Heuristic: check if input has a day digit
    has_day_end = re.search(r'\\b\\d{1,2}\\b', end_str) and not re.match(r'^\\d{4}$', end_str)
    
    if not has_day_end and end_date != now:
        end_date = end_date + timedelta(days=1)

    # Calculate difference
    diff = relativedelta(end_date, start_date)
    
    # Format output
    parts = []
    if diff.years > 0:
        parts.append(f"{diff.years} ans")
    if diff.months > 0:
        parts.append(f"{diff.months} mois")
        
    if not parts:
        return "1 mois" # Minimum
        
    return " ".join(parts)

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
        return "\\n".join([para.text for para in doc.paragraphs])
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
                logger.info(f"Low text content ({avg_chars_per_page:.1f} chars/page). Applying OCR...")
                text = ""
                ocr_applied = True
                for page_num in range(len(doc)):
                    page = doc.load_page(page_num)
                    pix = page.get_pixmap(dpi=150)
                    img_bytes = pix.tobytes("png")
                    image = Image.open(io.BytesIO(img_bytes))
                    text += pytesseract.image_to_string(image) + "\n"
            else:
                logger.info(f"Native text found ({avg_chars_per_page:.1f} chars/page). Skipping OCR.")
                
    except Exception as exc:
        logger.warning(f"PDF extraction failed/corrupted ({file_path}): {exc}")
        return "", False
    return text, ocr_applied

# --- CORE PIPELINE ---

def pre_process_text(text: str) -> str:
    """Removes repetitive headers/footers."""
    lines = [l.strip() for l in text.split("\\n") if l.strip()]
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
    
    return "\\n".join(cleaned_lines)

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
            matches = list(re.finditer(r"(^|\\n)\s*" + re.escape(kw), text_lower))
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
            # Attempt to split by date patterns
            # Pattern for dates: Month Year - Month Year (or Present)
            date_pattern = r"(?:Janvier|Février|Mars|Avril|Mai|Juin|Juillet|Août|Septembre|Octobre|Novembre|Décembre)\s+\d{4}\s*-\s*(?:Aujourd’hui|Présent|(?:Janvier|Février|Mars|Avril|Mai|Juin|Juillet|Août|Septembre|Octobre|Novembre|Décembre)\s+\d{4})"
            
            # Find all date matches
            matches = list(re.finditer(date_pattern, content, re.IGNORECASE))
            
            if not matches:
                segments[section_name] = [content]
            else:
                exp_list = []
                # We assume each job block starts some lines before the date.
                # A simple heuristic is to split at the line that is 2 lines before the date line?
                # Or simpler: The end of the previous block is the start of the current block's title.
                # But we don't know where the title starts.
                
                # Let's try to find the start of the block by looking backwards from the date.
                # Usually: Title \n Company \n Date
                # So we can try to split 2 non-empty lines before the date.
                
                # Alternative: Just split AT the date, and attach the preceding lines to the current block?
                # No, the preceding lines (Title/Company) belong to the date.
                
                # Strategy:
                # 1. Identify the line index of each date match.
                # 2. Go back 2 non-empty lines to find the "start" of this entry.
                # 3. Everything from that start until the start of the next entry is the block.
                
                lines = content.split("\\n")
                # Map character index to line index
                # This is getting complicated. Let's use a simpler split:
                # We assume the job starts with the Title.
                # If we can't find it easily, we might just split roughly around the dates.
                
                # Let's try this:
                # We will iterate through the text and find "islands" around dates.
                # Actually, looking at the CV, the structure is consistent.
                # Let's use the "2 lines before date" heuristic.
                
                block_starts = []
                for m in matches:
                    # Find the line containing this date
                    date_start_idx = m.start()
                    
                    # Count newlines before this index to find line number
                    preceding_text = content[:date_start_idx]
                    line_idx = preceding_text.count("\\n")
                    
                    # Walk back 2 non-empty lines
                    current_line = line_idx
                    lines_back = 0
                    start_line_idx = 0
                    
                    # We need to access lines list
                    # Let's just work with lines directly
                    pass

                # Re-implementation working with lines
                lines = content.split("\\n")
                date_line_indices = []
                for idx, line in enumerate(lines):
                    if re.search(date_pattern, line, re.IGNORECASE):
                        date_line_indices.append(idx)
                
                if not date_line_indices:
                     segments[section_name] = [content]
                else:
                    # Calculate start indices for each block
                    # We assume the block starts 2 non-empty lines before the date line
                    # If there aren't 2 lines, we take what we can.
                    
                    block_start_indices = []
                    for date_idx in date_line_indices:
                        # Walk back
                        found_lines = 0
                        curr = date_idx - 1
                        while curr >= 0:
                            if lines[curr].strip():
                                found_lines += 1
                            if found_lines == 2:
                                break
                            curr -= 1
                        # If we went below 0, start is 0. Otherwise curr is the start line.
                        start = max(0, curr)
                        
                        # Ensure we don't overlap with previous block's end (which is effectively this block's start)
                        # Actually, the previous block ends where this one starts.
                        # But we need to make sure we don't go back past the previous block's date line.
                        if block_start_indices:
                            prev_start = block_start_indices[-1]
                            # The previous block must have a date line.
                            # We can't really enforce "don't go past previous date" easily without more state.
                            # But generally, 2 lines back is safe enough for this format.
                            if start <= prev_start:
                                start = prev_start + 1 # Force forward progress?
                                
                        block_start_indices.append(start)
                        
                    # Now slice
                    for i in range(len(block_start_indices)):
                        start = block_start_indices[i]
                        if i < len(block_start_indices) - 1:
                            end = block_start_indices[i+1]
                        else:
                            end = len(lines)
                            
                        block_lines = lines[start:end]
                        exp_list.append("\\n".join(block_lines).strip())
                        
                    segments[section_name] = exp_list
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
    
    # Extract lines
    lines = [l.strip() for l in block_text.split("\\n") if l.strip()]
    
    # Identify Title and Company (heuristic: usually first 2 lines)
    # But sometimes date is first if we split poorly.
    # We already extracted date.
    
    title = ""
    company = ""
    
    # Filter out the date line if it's in the first few lines
    clean_lines = []
    for l in lines:
        if dates and l in dates: continue # Exact match
        if len(l) < 3: continue # Skip noise
        clean_lines.append(l)
        
    if clean_lines:
        title = clean_lines[0]
    if len(clean_lines) > 1:
        company = clean_lines[1]
        
    # If title looks like a date, swap or fix
    if re.match(date_pattern, title, re.IGNORECASE):
        title = "Poste Inconnu"

    # Extract Skills from "Environnement Technologique"
    skills = []
    tasks_raw = clean_lines[2:] if len(clean_lines) > 2 else []
    tasks_clean = []
    
    for line in tasks_raw:
        # Check for Environment line
        if "Environnement Technologique" in line or "Environnement:" in line:
            # Extract skills
            # Remove label
            content = re.sub(r".*Environnement.*[:]\s*", "", line, flags=re.IGNORECASE)
            # Split
            raw_skills = re.split(r"[,•/]", content)
            for s in raw_skills:
                s = s.strip()
                if len(s) > 1:
                    skills.append(s)
            continue # Do not add to tasks
            
        # Clean task line
        # Remove bullets
        line = re.sub(r"^[\•\-\*]\s*", "", line)
        
        # Merge with previous if starts with lowercase (continuation)
        if tasks_clean and line and line[0].islower():
            tasks_clean[-1] += " " + line
        else:
            tasks_clean.append(line)
            
    return {
        "titre_poste": title,
        "entreprise": company,
        "dates": dates,
        "taches": tasks_clean,
        "competences": skills
    }

def heuristic_parse_contact(text: str) -> Dict[str, Any]:
    """
    Extracts basic contact info using regex.
    """
    lines = [l.strip() for l in text.split("\\n") if l.strip()]
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
    # Clean lines
    lines = [l.strip() for l in text.split("\\n") if l.strip()]
    
    # Remove header if present
    if lines and "ÉDUCATION" in lines[0].upper():
        lines = lines[1:]
        
    degree = "Diplôme (Non spécifié)"
    institution = "Établissement (Non spécifié)"
    year = ""
    
    # Heuristic: Look for a year (4 digits)
    year_idx = -1
    for i, line in enumerate(lines):
        if re.match(r"^\d{4}$", line):
            year = line
            year_idx = i
            break
            
    if year_idx != -1:
        # If year found, assume line before is degree, line after is institution
        if year_idx > 0:
            degree = lines[year_idx - 1]
        if year_idx < len(lines) - 1:
            institution = " ".join(lines[year_idx + 1:]) # Join remaining lines for institution
    elif len(lines) >= 2:
        # Fallback: First line degree, second line institution
        degree = lines[0]
        institution = " ".join(lines[1:])
        
    return {
        "education": [{
            "diplome": degree,
            "etablissement": institution,
            "annee": year,
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
    logger.info("Step 3: Processing Experience Blocks (Parallel)...")
    structured_experiences = []
    
    if "experience_blocks" in segments and isinstance(segments["experience_blocks"], list):
        experience_blocks = [b for b in segments["experience_blocks"] if isinstance(b, str) and b.strip()]
        
        # Helper function for parallel execution
        def process_single_experience(exp_text, index):
            try:
                # Add delay to respect rate limits
                time.sleep(2)
                exp_data = ai_parse_experience_block(exp_text)
                return index, exp_data, exp_text
            except Exception as e:
                logger.error(f"Error parsing experience block {index}: {e}")
                return index, None, exp_text

        # Execute sequentially to avoid Rate Limits
        results = []
        for i, txt in enumerate(experience_blocks):
            # Add significant delay to respect strict rate limits
            time.sleep(20) 
            idx, exp_data, exp_text = process_single_experience(txt, i)
            results.append((idx, exp_data, exp_text))
                
        # Sort by original index to maintain order
        results.sort(key=lambda x: x[0])
        
        # Deduplication set
        seen_experiences = set()
        
        for _, exp_data, exp_text in results:
            if not exp_data:
                continue
                
            # Handle structured dates
            start_date = exp_data.get("start_date", "")
            end_date = exp_data.get("end_date", "")
            
            # Construct legacy dates string for display
            dates_str = f"{start_date} - {end_date}" if start_date and end_date else exp_data.get("dates", "")
            
            # Calculate duration using structured dates
            if not exp_data.get("duration"):
                exp_data["duration"] = calculate_duration_string(start_date, end_date)

            # Fallback parsing if AI failed to extract key fields
            if not exp_data.get("job_title") and not exp_data.get("company"):
                logger.warning("AI failed to extract experience details, trying heuristic fallback...")
                heuristic_data = heuristic_parse_experience(exp_text)
                # Merge heuristic data
                for k, v in heuristic_data.items():
                    if not exp_data.get(k):
                        exp_data[k] = v
                
                # If we fell back to heuristic, we might have a single 'dates' string
                # Try to split it for duration calculation if needed
                if not exp_data.get("duration") and exp_data.get("dates"):
                    # Heuristic returns "Start - End"
                    d_str = exp_data.get("dates")
                    # We can try to reuse our calc function by splitting loosely
                    parts = re.split(r'\s+(?:-|–|—|to|à)\s+', d_str)
                    if len(parts) == 2:
                        exp_data["duration"] = calculate_duration_string(parts[0], parts[1])

            # Map various possible keys for job title
            job_title = exp_data.get("job_title") or exp_data.get("titre_poste") or exp_data.get("titre") or "Poste inconnu"
            company = exp_data.get("company", "") or exp_data.get("entreprise", "")
            location = exp_data.get("localisation", "") or exp_data.get("location", "")
            
            # Deduplication Check
            # Create a signature based on key fields
            # We normalize strings to avoid minor differences (case, whitespace)
            def normalize_key(s): return str(s).strip().lower()
            
            sig = (
                normalize_key(job_title),
                normalize_key(company),
                normalize_key(dates_str)
            )
            
            if sig in seen_experiences:
                logger.info(f"Skipping duplicate experience: {job_title} at {company} ({dates_str})")
                continue
                
            seen_experiences.add(sig)

            entry = ExperienceEntry(
                job_title=job_title,
                company=company,
                location=location,
                dates=dates_str,
                duration=exp_data.get("duration", "") or exp_data.get("duree", ""),
                summary=exp_data.get("resume", "") or exp_data.get("summary", ""),
                tasks=exp_data.get("taches", []) or exp_data.get("tasks", []),
                skills=exp_data.get("competences", []) or exp_data.get("skills", []),
                full_text=exp_text
            )
            structured_experiences.append(entry)
    # 5b. Generate Dynamic Summary
    logger.info("Step 3b: Generating Dynamic Summary...")
    generated_summary = ""
    if structured_experiences:
        try:
            # Convert ExperienceEntry objects to dicts for the AI function
            exp_dicts = [exp.to_dict() for exp in structured_experiences]
            summary_result = ai_generate_summary(exp_dicts)
            generated_summary = summary_result.get("generated_summary", "")
        except Exception as e:
            logger.error(f"Error generating summary: {e}")

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
    if skills_block:
        # Split by comma, bullet, or newline
        raw_skills = re.split(r"[,•\n]", skills_block)
        for s in raw_skills:
            s = s.strip()
            if not s: continue
            # Filter out headers and noise
            # Check for colons at end (standard and full-width)
            if s.endswith(":") or s.endswith("："): continue 
            # Check for known headers
            if "COMPÉTENCES" in s.upper() or "TECHNIQUES" in s.upper(): continue
            if "DÉVELOPPEMENT" in s.upper() and s.endswith(":"): continue
            
            if len(s) < 2: continue # Filter single chars like "."
            
            skills_tech.append(s)

    extra_info = []
    other_block = segments.get("other_block", "")
    if other_block:
        extra_info = [other_block]

    # 8. Assemble CV Data
    cv_data = CVData(
        meta={"filename": filename, "ocr_applied": str(ocr_applied)},
        basics=basics,
        links=[basics.get("linkedin")] if basics.get("linkedin") else [],
        summary=generated_summary if generated_summary else basics.get("summary", ""), 
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
