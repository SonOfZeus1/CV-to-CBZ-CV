import re
import os
import io
import fitz  # PyMuPDF
import docx
import pytesseract
from PIL import Image
import spacy
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any

# --- SCHÉMA DE DONNÉES (V7 - Advanced/Inverted) ---

@dataclass
class ExperienceEntry:
    title: str = ""
    company: str = ""
    location: str = ""
    date_start: str = ""
    date_end: str = ""
    duration: str = ""
    
    # Segmentation
    context: str = ""
    responsibilities: List[str] = field(default_factory=list)
    tech_stack: List[str] = field(default_factory=list) # Nouveau champ isolé
    
    # Filet de sécurité
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
    meta: Dict[str, str] = field(default_factory=dict)
    basics: Dict[str, str] = field(default_factory=lambda: {
        "name": "", "email": "", "phone": "", "location": ""
    })
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

    def to_dict(self):
        return asdict(self)

# --- OUTILS ---

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
    except Exception:
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
                    del pix; del image
    except Exception:
        pass
    return text, ocr_applied

# --- PARSER UNIVERSEL ---

class UniversalParser:
    def __init__(self, text: str):
        self.raw_text = text
        self.nlp = load_spacy_model()
        self.doc = self.nlp(text[:100000])
        self.lines = self.pre_process_text(text)

    def pre_process_text(self, text: str) -> List[str]:
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        cleaned_lines = []
        patterns_to_remove = [
            r'^page\s*\d+\s*(/|sur|of)\s*\d+$',
            r'^curriculum\s*vitae$',
            r'^cv$'
        ]
        for line in lines:
            keep = True
            for pat in patterns_to_remove:
                if re.match(pat, line, re.IGNORECASE):
                    keep = False
                    break
            if keep:
                cleaned_lines.append(line)
        return cleaned_lines

    def normalize_paragraph(self, text_lines: List[str]) -> str:
        full_text = " ".join(text_lines)
        return re.sub(r'\s+', ' ', full_text).strip()

    def extract_basics(self) -> Dict[str, Any]:
        full_text = "\n".join(self.lines)
        email_regex = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        phone_regex = r'(\+?\d{1,3}[-.\s]?)?(\(?\d{2,4}\)?[-.\s]?)?(\d{2,4}[-.\s]?){2,4}'
        link_regex = r'(https?://\S+|www\.\S+|linkedin\.com/in/\S+|github\.com/\S+)'
        
        emails = re.findall(email_regex, full_text)
        phones = [m.group(0).strip() for m in re.finditer(phone_regex, full_text) if len(re.sub(r'\D', '', m.group(0))) >= 9]
        links = list(set(re.findall(link_regex, full_text, re.IGNORECASE)))

        name = "Inconnu"
        blacklist = {"curriculum", "vitae", "resume", "cv", "email", "phone", "page", "profil", "summary"}
        for line in self.lines[:40]:
            words = line.split()
            if 2 <= len(words) <= 4:
                if any(w.lower() in blacklist for w in words): continue
                if any(c.isdigit() or c in "@+/" for c in line): continue
                if line.isupper(): 
                    name = line.title(); break
                if line.istitle() and name == "Inconnu": 
                    name = line
        return {"name": name, "email": emails[0] if emails else "", "phone": phones[0] if phones else "", "links": links}

    def extract_skills(self) -> Dict[str, List[str]]:
        tech_keywords = {"python", "java", "c++", "sql", "javascript", "react", "docker", "aws", "linux", "git", "html", "css", "kubernetes", "azure", "vba", "oracle", "visio", "jira", "confluence", "power bi", "tableau", "sap", ".net", "c#", "spring", "angular", "jenkins", "selenium", "cucumber", "postman", "xray", "github"}
        soft_keywords = {"management", "communication", "leadership", "agile", "scrum", "anglais", "français", "espagnol", "analyste", "stratégique", "coordination"}
        tech, soft = set(), set()
        for token in [t.text.lower() for t in self.doc if not t.is_stop]:
            if token in tech_keywords: tech.add(token.capitalize())
            if token in soft_keywords: soft.add(token.capitalize())
        return {"tech": list(tech), "soft": list(soft)}

    def parse_experience_granular(self, raw_lines: List[str]) -> List[ExperienceEntry]:
        entries = []
        if not raw_lines: return entries

        # 1. SEGMENTATION (Regex Dates)
        # Ex: "Septembre 2021-Aujourd’hui", "Juin 2020 - Septembre 2021"
        date_regex = r'(?i)((?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre|[a-z]{3})\s*\d{4})\s*[-–]\s*((?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre|[a-z]{3})\s*\d{4}|aujourd’hui|présent|maintenant)'
        
        bounds = []
        for i, line in enumerate(raw_lines):
            # On considère qu'une ligne < 80 chars qui matche une date est un header
            if len(line) < 80 and re.search(date_regex, line):
                if not bounds or (i - bounds[-1] > 2):
                    bounds.append(i)
        
        if not bounds: bounds = [0]

        # 2. PARSING BLOCS
        for idx, start_index in enumerate(bounds):
            end_index = bounds[idx+1] if idx+1 < len(bounds) else len(raw_lines)
            block_lines = raw_lines[start_index:end_index]
            entry = ExperienceEntry()
            entry.full_text = "\n".join(block_lines)

            # Extraction Dates (Ligne 1 ou 2)
            for line in block_lines[:3]:
                dm = re.search(date_regex, line)
                if dm:
                    entry.date_start = dm.group(1)
                    entry.date_end = dm.group(2)
                    break
            
            # Séparation Body / Footer (Env Tech)
            body_lines = []
            footer_lines = []
            found_tech = False
            
            tech_marker = "environnement technologique"
            
            for line in block_lines:
                if tech_marker in line.lower():
                    found_tech = True
                    parts = re.split(r'(?i)environnement technologique\s*[:\.]?', line)
                    if len(parts) > 1:
                        if parts[0].strip(): body_lines.append(parts[0].strip())
                        footer_lines.append(parts[1].strip())
                    else:
                        footer_lines.append(line)
                elif found_tech:
                    footer_lines.append(line)
                else:
                    if not (entry.date_start and entry.date_start in line):
                        body_lines.append(line)

            # A. Parsing Body (Tâches)
            full_body = " ".join(body_lines)
            
            # On utilise les puces "•" si présentes, sinon les verbes d'action
            if "•" in full_body:
                 tasks = full_body.split("•")
                 for task in tasks:
                     clean_task = task.strip()
                     if len(clean_task) > 3:
                         entry.responsibilities.append(clean_task)
            else:
                # Fallback Verbes d'action
                action_verbs = r'(Conception|Développement|Implémentation|Mise en œuvre|Processus|Gestion|Analyse|Rédaction|Planification|Coordination|Support|Maintenance)'
                split_body = re.sub(r'(?<!^)\s+(?=' + action_verbs + r'\b)', '\n', full_body)
                for task in split_body.split('\n'):
                    clean_task = task.strip()
                    if len(clean_task) > 3:
                        entry.responsibilities.append(clean_task)

            # B. Parsing Footer (Tech Stack + Identity)
            full_footer = " ".join(footer_lines)
            footer_parts = [p.strip() for p in full_footer.split(',')]
            
            if footer_parts:
                last_part = footer_parts[-1]
                
                # Extraction identité finale
                title_regex = r'\b(Développeur|Analyste|Architecte|Consultant|Ingénieur|Tech Lead|Product Owner)\b'
                
                # Recherche du titre dans tout le footer pour couper avant
                match_title_full = re.search(title_regex, full_footer)
                
                if match_title_full:
                    # Stack = tout avant le titre
                    stack_str = full_footer[:match_title_full.start()]
                    # Nettoyage stack
                    entry.tech_stack = [t.strip() for t in re.split(r'[,/]', stack_str) if len(t.strip()) > 1]
                    
                    # Identité = tout après
                    identity_str = full_footer[match_title_full.start():]
                    # Ex: "Développeur Hilo Énergie, Montréal, CANADA"
                    
                    # On splitte pour séparer Titre/Co du Lieu
                    # Heuristique: Lieu est souvent séparé par une virgule à la fin
                    id_parts = identity_str.rsplit(',', 2) # On essaie de chopper Ville, Pays
                    
                    if len(id_parts) >= 2 and "CANADA" in id_parts[-1].upper():
                         entry.location = ", ".join(id_parts[-2:]).strip()
                         title_co_part = ", ".join(id_parts[:-2]).strip()
                    elif len(id_parts) >= 2:
                         entry.location = id_parts[-1].strip()
                         title_co_part = ", ".join(id_parts[:-1]).strip()
                    else:
                         title_co_part = identity_str
                    
                    # Séparation Titre - Entreprise
                    # On cherche le premier mot Majuscule qui n'est pas dans le titre
                    # "Développeur Hilo" -> Titre=Développeur, Co=Hilo
                    
                    # On prend le premier mot du titre détecté
                    title_start = re.match(title_regex, title_co_part)
                    if title_start:
                         # On coupe arbitrairement après le titre + 1 mot (ex: Développeur Java) ou on cherche une majuscule ?
                         # Simple : Titre = tout jusqu'à la première majuscule qui n'est pas un mot clé ? Trop dur.
                         # On va prendre : Titre = le match regex + mot suivant
                         # Puis Entreprise = le reste
                         words = title_co_part.split()
                         if len(words) > 2:
                             entry.title = " ".join(words[:2]) # Développeur Java
                             entry.company = " ".join(words[2:]) # Hilo Énergie
                         else:
                             entry.title = title_co_part
                    else:
                         entry.title = title_co_part

                else:
                    # Pas de titre trouvé -> tout est stack
                    entry.tech_stack = footer_parts

            entries.append(entry)
        return entries

    def classify_and_parse(self) -> Dict[str, Any]:
        sections = {"experience": [], "education": [], "summary": [], "skills": [], "languages": [], "achievements": [], "extra": [], "unmapped": []}
        map_keys = {
            "experience": ["expérience", "experience", "mandats", "parcours"],
            "education": ["formation", "education", "diplômes"],
            "skills": ["compétences", "skills", "expertises"],
            "summary": ["résumé", "summary", "profil", "objectif"],
            "languages": ["langues"],
            "achievements": ["réalisations", "projets"],
            "extra": ["intérêts", "hobbies", "certifications"]
        }
        current = "unmapped"
        for line in self.lines:
            line_lower = line.lower()
            if len(line) < 60:
                for k, v in map_keys.items():
                    if any(val in line_lower for val in v) and (line.isupper() or len(line.split()) < 5):
                        current = k; break
            sections[current].append(line)

        parsed_exp = self.parse_experience_granular(sections["experience"])
        clean_sum = self.normalize_paragraph(sections["summary"])
        
        return {"experience": parsed_exp, "summary": clean_sum, "raw_sections": sections}

def parse_cv(file_path: str) -> Optional[dict]:
    filename = os.path.basename(file_path)
    _, extension = os.path.splitext(filename)
    text, ocr_applied = "", False
    if extension.lower() == ".pdf": text, ocr_applied = extract_text_from_pdf(file_path)
    elif extension.lower() == ".docx": text = extract_text_from_docx(file_path)
    if not text.strip(): return None

    try:
        parser = UniversalParser(text)
        basics = parser.extract_basics()
        skills = parser.extract_skills()
        struct = parser.classify_and_parse()
        raw = struct["raw_sections"]
        
        cv_data = CVData(
            meta={"filename": filename, "ocr_applied": str(ocr_applied)},
            basics=basics, links=basics["links"],
            summary=struct["summary"],
            skills_tech=skills["tech"], skills_soft=skills["soft"],
            experience=struct["experience"],
            education=[EducationEntry(degree=l, full_text=l) for l in raw["education"] if len(l) > 3],
            languages=[l for l in raw["languages"] if len(l) > 3],
            achievements_global=raw["achievements"], extra_info=raw["extra"], unmapped=raw["unmapped"],
            raw_text=text
        )
        return cv_data.to_dict()
    except Exception as e:
        print(f"Erreur: {e}")
        return CVData(raw_text=text, meta={"error": str(e)}).to_dict()
