import io
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import docx
import fitz  # PyMuPDF
import pytesseract
import spacy
from PIL import Image
from dateparser import parse as parse_date

logger = logging.getLogger(__name__)

# --- Feature flag pour activer/désactiver l'IA ---
USE_AI_EXPERIENCE = os.getenv("USE_AI_EXPERIENCE", "false").lower() in {"1", "true", "yes"}
if USE_AI_EXPERIENCE:
    try:
        from ai_parsers import ai_parse_experience_block  # type: ignore
    except Exception as exc:  # pragma: no cover
        logger.warning("Désactivation IA (import impossible) : %s", exc)
        USE_AI_EXPERIENCE = False
        ai_parse_experience_block = None  # type: ignore
else:
    ai_parse_experience_block = None  # type: ignore

# --- SCHÉMA DE DONNÉES ---


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
    date_start: str = ""
    date_end: str = ""
    full_text: str = ""


@dataclass
class CVData:
    meta: Dict[str, str] = field(default_factory=dict)
    basics: Dict[str, str] = field(
        default_factory=lambda: {"name": "", "email": "", "phone": "", "location": ""}
    )
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


# --- OUTILS GÉNÉRAUX ---

NLP = None


def load_spacy_model():
    global NLP
    if NLP is None:
        try:
            NLP = spacy.load("en_core_web_sm")
        except OSError:
            from spacy.cli import download

            download("en_core_web_sm")
            NLP = spacy.load("en_core_web_sm")
    return NLP


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
                    del pix
                    del image
    except Exception as exc:
        logger.error("PDF extraction failed (%s): %s", file_path, exc)
    return text, ocr_applied


# --- PARSING AVANCÉ ---

TECH_KEYWORDS = {
    "python",
    "java",
    "c++",
    "c#",
    ".net",
    "javascript",
    "typescript",
    "react",
    "angular",
    "vue",
    "sql",
    "mysql",
    "postgresql",
    "oracle",
    "mongodb",
    "docker",
    "kubernetes",
    "aws",
    "azure",
    "gcp",
    "jenkins",
    "gitlab",
    "git",
    "jira",
    "confluence",
    "tableau",
    "power bi",
    "sap",
    "salesforce",
    "linux",
    "windows",
    "spark",
    "hadoop",
    "terraform",
    "ansible",
    "snowflake",
    "databricks",
    "pandas",
    "numpy",
    "scikit-learn",
    "matlab",
}

DATE_RANGE_REGEX = re.compile(
    r"(?i)((?:\d{4}|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
    r"|\b(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre)\b)"
    r"[\w\séû\.']{0,15}?\d{4})\s*[-–—]\s*("
    r"(?:\d{4}|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
    r"[\w\séû\.']{0,15}?\d{4}|"
    r"(?:présent|present|aujourd'hui|aujourd’hui|maintenant|current))"
    r")"
)

PRESENT_TOKENS = {"present", "présent", "aujourd'hui", "aujourd’hui", "maintenant", "current"}


def _parse_natural_date(chunk: str) -> Optional[datetime]:
    cleaned = chunk.strip().lower()
    if not cleaned:
        return None
    if cleaned in PRESENT_TOKENS:
        return datetime.utcnow()
    parsed = parse_date(chunk, languages=["fr", "en"])
    return parsed


def compute_duration_label(dates_text: str) -> str:
    if not dates_text:
        return ""
    parts = re.split(r"\s*[-–—]\s*", dates_text)
    if len(parts) < 2:
        return ""
    start = _parse_natural_date(parts[0])
    end = _parse_natural_date(parts[1])
    if not start:
        return ""
    end = end or datetime.utcnow()
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    if months < 0:
        return ""
    years, remaining_months = divmod(months, 12)
    chunks = []
    if years > 0:
        chunks.append(f"{years} an{'s' if years > 1 else ''}")
    if remaining_months > 0:
        chunks.append(f"{remaining_months} mois")
    if not chunks and months >= 0:
        chunks.append("1 mois")
    return " ".join(chunks)


def extract_skills_from_text(text: str, limit: int = 12) -> List[str]:
    tokens = re.findall(r"[A-Za-z0-9\+#\.]+(?:\s+[A-Za-z0-9\+#\.]+)?", text.lower())
    found: List[str] = []
    for token in tokens:
        normalized = token.strip()
        if not normalized:
            continue
        if normalized in TECH_KEYWORDS and normalized.capitalize() not in found:
            found.append(normalized.capitalize())
        if len(found) >= limit:
            break
    return found


class UniversalParser:
    def __init__(self, text: str):
        self.raw_text = text
        self.nlp = load_spacy_model()
        self.doc = self.nlp(text[:100000])
        self.lines = self.pre_process_text(text)

    def pre_process_text(self, text: str) -> List[str]:
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        cleaned_lines = []
        patterns_to_remove = [
            r"^page\s*\d+\s*(/|sur|of)\s*\d+$",
            r"^curriculum\s*vitae$",
            r"^cv$",
        ]
        for line in lines:
            if any(re.match(pat, line, re.IGNORECASE) for pat in patterns_to_remove):
                continue
            cleaned_lines.append(line)
        return cleaned_lines

    def normalize_paragraph(self, text_lines: List[str]) -> str:
        full_text = " ".join(text_lines)
        return re.sub(r"\s+", " ", full_text).strip()

    def extract_basics(self) -> Dict[str, Any]:
        full_text = "\n".join(self.lines)
        email_regex = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
        phone_regex = r"(\+?\d{1,3}[-.\s]?)?(\(?\d{2,4}\)?[-.\s]?)?(\d{2,4}[-.\s]?){2,4}"
        link_regex = r"(https?://\S+|www\.\S+|linkedin\.com/in/\S+|github\.com/\S+)"

        emails = re.findall(email_regex, full_text)
        phones = [
            m.group(0).strip()
            for m in re.finditer(phone_regex, full_text)
            if len(re.sub(r"\D", "", m.group(0))) >= 9
        ]
        links = list(set(re.findall(link_regex, full_text, re.IGNORECASE)))

        name = "Inconnu"
        blacklist = {
            "curriculum",
            "vitae",
            "resume",
            "cv",
            "email",
            "phone",
            "page",
            "profil",
            "summary",
        }
        for line in self.lines[:40]:
            words = line.split()
            if 2 <= len(words) <= 4:
                if any(w.lower() in blacklist for w in words):
                    continue
                if any(c.isdigit() or c in "@+/" for c in line):
                    continue
                if line.isupper():
                    name = line.title()
                    break
                if line.istitle() and name == "Inconnu":
                    name = line
        return {
            "name": name,
            "email": emails[0] if emails else "",
            "phone": phones[0] if phones else "",
            "links": links,
        }

    def extract_skills(self) -> Dict[str, List[str]]:
        tech_keywords = TECH_KEYWORDS
        soft_keywords = {
            "management",
            "communication",
            "leadership",
            "agile",
            "scrum",
            "anglais",
            "français",
            "espagnol",
            "analyste",
            "stratégique",
            "coordination",
        }
        tech, soft = set(), set()
        for token in [t.text.lower() for t in self.doc if not t.is_stop]:
            if token in tech_keywords:
                tech.add(token.capitalize())
            if token in soft_keywords:
                soft.add(token.capitalize())
        return {"tech": list(tech), "soft": list(soft)}

    def segment_sections(self) -> Dict[str, List[str]]:
        sections = {
            "experience": [],
            "education": [],
            "summary": [],
            "skills": [],
            "languages": [],
            "achievements": [],
            "extra": [],
            "unmapped": [],
        }
        map_keys = {
            "experience": ["expérience", "experience", "mandats", "parcours"],
            "education": ["formation", "education", "diplômes"],
            "skills": ["compétences", "skills", "expertises", "technologies", "technique"],
            "summary": ["résumé", "summary", "profil", "objectif"],
            "languages": ["langues"],
            "achievements": ["réalisations", "projets"],
            "extra": ["intérêts", "hobbies", "certifications"],
        }
        current = "unmapped"
        # Header detection improvement:
        # We track if we are in a header section (e.g. name/contact info)
        # Usually, the first few lines are basics.
        
        for i, line in enumerate(self.lines):
            line_lower = line.lower()
            
            # Heuristique simple : les 10 premières lignes sont souvent des basics si non mappées
            if i < 10 and current == "unmapped":
                 # On ne change rien, ça ira dans unmapped qui servira au basics extraction
                 pass

            if len(line) < 60:
                for key, aliases in map_keys.items():
                    # Check if line is a section header
                    if any(val == line_lower or val in line_lower for val in aliases) and (
                        line.isupper() or len(line.split()) < 5 or line.endswith(":")
                    ):
                        current = key
                        break
            
            # Check for recurring headers/footers to ignore (simple duplicate check could be added here)
            sections[current].append(line)
            
        return sections

    def extract_experience_blocks(self, raw_lines: List[str]) -> List[Dict[str, Any]]:
        if not raw_lines:
            return []
        header_indices = []
        for idx, line in enumerate(raw_lines):
            if len(line) < 140 and DATE_RANGE_REGEX.search(line):
                if not header_indices or idx - header_indices[-1] > 1:
                    header_indices.append(idx)
        if not header_indices:
            header_indices = [0]

        blocks: List[Dict[str, Any]] = []
        for i, start in enumerate(header_indices):
            end = header_indices[i + 1] if i + 1 < len(header_indices) else len(raw_lines)
            block_lines = raw_lines[start:end]
            block_text = "\n".join(block_lines).strip()
            if not block_text:
                continue
            date_text = ""
            for header_line in block_lines[:3]:
                match = DATE_RANGE_REGEX.search(header_line)
                if match:
                    date_text = match.group(0).strip()
                    break
            location_hint = self._extract_location_hint(block_lines)
            blocks.append(
                {
                    "text": block_text,
                    "lines": block_lines,
                    "date_text": date_text,
                    "location_hint": location_hint,
                }
            )
        return blocks

    @staticmethod
    def _extract_location_hint(block_lines: List[str]) -> str:
        for line in block_lines[:3]:
            if "," in line:
                candidate = line.split(",")[-1].strip()
                if 2 <= len(candidate) <= 40:
                    return candidate
        return ""


def _build_entry_from_ai(block: Dict[str, Any], ai_payload: Dict[str, Any]) -> ExperienceEntry:
    dates = ai_payload.get("dates") or block.get("date_text", "")
    duration = ai_payload.get("duree") or compute_duration_label(dates)
    location = ai_payload.get("localisation") or block.get("location_hint", "")
    tasks = [task.strip() for task in ai_payload.get("taches", []) if task.strip()]
    skills = [skill.strip() for skill in ai_payload.get("competences", []) if skill.strip()]

    return ExperienceEntry(
        job_title=ai_payload.get("titre_poste", ""),
        company=ai_payload.get("entreprise", ""),
        location=location,
        dates=dates,
        duration=duration,
        summary=ai_payload.get("resume", ""),
        tasks=tasks,
        skills=skills,
        full_text=block.get("text", ""),
    )


def _rule_based_entry(block: Dict[str, Any]) -> ExperienceEntry:
    lines = [line.strip() for line in block.get("lines", []) if line.strip()]
    header = lines[0] if lines else ""
    job_title = header
    company = ""
    location = block.get("location_hint", "")

    if " - " in header:
        parts = header.split(" - ", 1)
        job_title = parts[0].strip()
        company = parts[1].strip()
    elif "|" in header:
        parts = header.split("|", 1)
        job_title = parts[0].strip()
        company = parts[1].strip()

    if "," in company and not location:
        company_parts = company.split(",")
        location = company_parts[-1].strip()
        company = ",".join(company_parts[:-1]).strip()

    tasks: List[str] = []
    for raw in lines[1:]:
        # Nettoyage des caractères invisibles
        clean_line = raw.strip()
        # Regex élargie pour détecter les puces : tiret, astérisque, bullet ronde, bullet carrée, flèche
        if re.match(r"^[\-\*•▪‣➢\+]+", clean_line):
             tasks.append(re.sub(r"^[\-\*•▪‣➢\+\s]+", "", clean_line).strip())
        # Heuristique : si la ligne commence par un verbe d'action (liste non exhaustive) et pas de puce
        elif re.match(r"^(Développer|Concevoir|Gérer|Analyser|Participer|Mettre en place|Assurer|Réaliser|Créer|Optimiser|Maintenir)\b", clean_line, re.IGNORECASE):
             tasks.append(clean_line)
             
    if not tasks:
        # Fallback ultime : on prend toutes les lignes de longueur > 15 chars qui ne sont pas des headers/dates
        # On exclut les lignes trop courtes (titres, lieux)
        tasks = [line for line in lines[1:] if len(line) > 20 and not DATE_RANGE_REGEX.search(line)]

    dates = block.get("date_text", "")
    duration = compute_duration_label(dates)
    skills = extract_skills_from_text(block.get("text", ""))

    return ExperienceEntry(
        job_title=job_title,
        company=company,
        location=location,
        dates=dates,
        duration=duration,
        summary="",
        tasks=tasks,
        skills=skills,
        full_text=block.get("text", ""),
    )


def parse_cv(file_path: str) -> Optional[dict]:
    filename = os.path.basename(file_path)
    _, extension = os.path.splitext(filename)
    text, ocr_applied = "", False
    if extension.lower() == ".pdf":
        text, ocr_applied = extract_text_from_pdf(file_path)
    elif extension.lower() == ".docx":
        text = extract_text_from_docx(file_path)
    if not text.strip():
        return None

    try:
        parser = UniversalParser(text)
        basics = parser.extract_basics()
        skills = parser.extract_skills()
        sections = parser.segment_sections()

        experience_blocks = parser.extract_experience_blocks(sections["experience"])
        structured_experiences: List[ExperienceEntry] = []
        for block in experience_blocks:
            entry: Optional[ExperienceEntry] = None

            if USE_AI_EXPERIENCE and ai_parse_experience_block:
                logger.info(f"Parsing AI expérience (bloc de {len(block['text'])} chars)...")
                try:
                    ai_payload = ai_parse_experience_block(block["text"])
                except Exception as exc:
                    logger.warning("AI parsing failure, fallback to rule-based: %s", exc)
                    ai_payload = {}
                if ai_payload:
                    try:
                        entry = _build_entry_from_ai(block, ai_payload)
                    except Exception as exc:  # pragma: no cover
                        logger.warning("AI payload invalide, fallback rule-based: %s", exc)
                        entry = None
            else:
                if USE_AI_EXPERIENCE:
                    logger.warning("IA activée mais module non disponible. Fallback rule-based.")
            
            if entry is None:
                logger.info("Utilisation parser Rule-based pour ce bloc.")
                entry = _rule_based_entry(block)

            structured_experiences.append(entry)

        clean_summary = parser.normalize_paragraph(sections["summary"])

        education_entries = [
            EducationEntry(degree=line, full_text=line)
            for line in sections["education"]
            if len(line) > 3
        ]

        cv_data = CVData(
            meta={"filename": filename, "ocr_applied": str(ocr_applied)},
            basics=basics,
            links=basics["links"],
            summary=clean_summary,
            skills_tech=skills["tech"],
            skills_soft=skills["soft"],
            experience=structured_experiences,
            education=education_entries,
            languages=[l for l in sections["languages"] if len(l) > 2],
            achievements_global=sections["achievements"],
            extra_info=sections["extra"],
            unmapped=sections["unmapped"],
            raw_text=text,
        )
        return cv_data.to_dict()
    except Exception as exc:
        logger.error("Parsing failed for %s: %s", filename, exc, exc_info=True)
        return CVData(raw_text=text, meta={"error": str(exc)}).to_dict()
