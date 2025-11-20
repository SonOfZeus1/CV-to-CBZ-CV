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

# --- SCHÉMA DE DONNÉES (V3 - Structuré) ---

@dataclass
class ExperienceEntry:
    title: str = ""
    company: str = ""
    date_start: str = ""
    date_end: str = ""
    description: List[str] = field(default_factory=list)

@dataclass
class EducationEntry:
    degree: str = ""
    institution: str = ""
    date_start: str = ""
    date_end: str = ""

@dataclass
class CVData:
    """Structure standardisée enrichie pour les données extraites d'un CV."""
    meta: Dict[str, str] = field(default_factory=dict)
    basics: Dict[str, str] = field(default_factory=lambda: {
        "name": "", "email": "", "phone": "", "location": ""
    })
    summary: str = ""
    skills_tech: List[str] = field(default_factory=list)
    skills_soft: List[str] = field(default_factory=list)
    languages: List[str] = field(default_factory=list)
    experience: List[ExperienceEntry] = field(default_factory=list)
    education: List[EducationEntry] = field(default_factory=list)
    links: List[str] = field(default_factory=list)
    raw_text: str = ""

    def to_dict(self):
        return asdict(self)

# --- CHARGEMENT MODÈLE NLP ---

NLP = None

def load_spacy_model():
    global NLP
    if NLP is None:
        try:
            NLP = spacy.load("en_core_web_sm")
        except OSError:
            print("Modèle Spacy non trouvé. Téléchargement en cours...")
            from spacy.cli import download
            download("en_core_web_sm")
            NLP = spacy.load("en_core_web_sm")
    return NLP

# --- OUTILS D'EXTRACTION BAS NIVEAU ---

def extract_text_from_docx(file_path: str) -> str:
    try:
        doc = docx.Document(file_path)
        return "\n".join([para.text for para in doc.paragraphs])
    except Exception as e:
        print(f"Erreur lecture DOCX {file_path}: {e}")
        return ""

def extract_text_from_pdf(file_path: str) -> tuple[str, bool]:
    text = ""
    ocr_applied = False
    try:
        with fitz.open(file_path) as doc:
            for page in doc:
                text += page.get_text()
            
            # Heuristique OCR améliorée : si beaucoup de caractères inconnus ou trop peu de texte
            avg_chars_per_page = len(text.strip()) / len(doc) if len(doc) > 0 else 0
            garbage_ratio = len(re.findall(r'[^\w\s]', text)) / len(text) if len(text) > 0 else 0

            if avg_chars_per_page < 50 or garbage_ratio > 0.4:
                print(f"OCR requis pour {os.path.basename(file_path)} (densité: {avg_chars_per_page:.1f}, garbage: {garbage_ratio:.2f})")
                text = "" 
                ocr_applied = True
                for page_num in range(len(doc)):
                    page = doc.load_page(page_num)
                    pix = page.get_pixmap(dpi=150)
                    img_bytes = pix.tobytes("png")
                    image = Image.open(io.BytesIO(img_bytes))
                    text += pytesseract.image_to_string(image) + "\n"
                    # Nettoyage temp
                    del pix
                    del image
    except Exception as e:
        print(f"Erreur extraction PDF {file_path}: {e}")
    
    return text, ocr_applied

# --- PARSER AVANCÉ ---

class AdvancedResumeParser:
    def __init__(self, text: str):
        self.text = text
        self.nlp = load_spacy_model()
        # Limite taille texte pour Spacy pour éviter OOM sur très gros CV
        self.doc = self.nlp(text[:100000])
        
    def extract_regex_fields(self) -> Dict[str, Any]:
        email_regex = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        phone_regex = r'(\+?\d{1,3}[-.\s]?)?(\(?\d{2,4}\)?[-.\s]?)?(\d{2,4}[-.\s]?){2,4}'
        link_regex = r'(https?://\S+|www\.\S+|linkedin\.com/in/\S+|github\.com/\S+)'
        
        emails = list(set(re.findall(email_regex, self.text)))
        # Filtre téléphones : au moins 10 chars pour éviter faux positifs comme dates "2019-2020"
        phones_iter = re.finditer(phone_regex, self.text)
        phones = [m.group(0).strip() for m in phones_iter if len(re.sub(r'\D', '', m.group(0))) >= 9]
        links = list(set(re.findall(link_regex, self.text, re.IGNORECASE)))
        
        return {"email": emails[0] if emails else "", "phone": phones[0] if phones else "", "links": links}

    def extract_name(self) -> str:
        # On cherche une entité PERSON au tout début du document
        for ent in self.doc.ents[:20]:
            if ent.label_ == "PERSON" and len(ent.text.split()) >= 2 and "\n" not in ent.text:
                return ent.text.strip()
        return ""

    def extract_skills(self) -> Dict[str, List[str]]:
        # Liste extensible
        tech_keywords = {"python", "java", "c++", "sql", "javascript", "react", "docker", "aws", "linux", "git", "html", "css", "kubernetes", "azure"}
        soft_keywords = {"management", "communication", "leadership", "agile", "scrum", "anglais", "français", "spanish"}
        
        tech_found = set()
        soft_found = set()
        
        tokens = [t.text.lower() for t in self.doc if not t.is_stop]
        for t in tokens:
            if t in tech_keywords:
                tech_found.add(t.capitalize())
            if t in soft_keywords:
                soft_found.add(t.capitalize())
                
        return {"tech": list(tech_found), "soft": list(soft_found)}

    def parse_experience_block(self, text_block: List[str]) -> List[ExperienceEntry]:
        """Tente de structurer un bloc de texte d'expérience."""
        entries = []
        current_entry = None
        
        # Regex date : "Jan 2020 - Present", "2019-2021", "01/2020"
        date_pattern = r'((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s?\d{4}|\d{2}/\d{4}|\d{4})\s*[-–toà]\s*((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s?\d{4}|\d{2}/\d{4}|\d{4}|present|aujourd\'hui|now)'
        
        for line in text_block:
            line = line.strip()
            if not line: continue
            
            # Detection ligne d'en-tête (Date + potentiellement Titre/Company)
            date_match = re.search(date_pattern, line, re.IGNORECASE)
            
            if date_match:
                # Sauvegarde entrée précédente
                if current_entry:
                    entries.append(current_entry)
                
                # Nouvelle entrée
                current_entry = ExperienceEntry(
                    date_start=date_match.group(1),
                    date_end=date_match.group(2),
                    description=[]
                )
                
                # On essaie de deviner le titre/compagnie sur la même ligne ou autour
                clean_line = re.sub(date_pattern, '', line, flags=re.IGNORECASE).strip()
                # Heuristique : si la ligne restante est courte, c'est titre/entreprise
                if len(clean_line) > 3:
                     # Analyse NER rapide sur cette ligne pour trouver ORG
                    line_doc = self.nlp(clean_line)
                    orgs = [ent.text for ent in line_doc.ents if ent.label_ == "ORG"]
                    current_entry.company = orgs[0] if orgs else ""
                    current_entry.title = clean_line # Par défaut on met tout
            
            elif current_entry:
                # C'est une ligne de description
                # On nettoie les puces
                clean_desc = re.sub(r'^[-•*]\s?', '', line).strip()
                current_entry.description.append(clean_desc)
        
        if current_entry:
            entries.append(current_entry)
            
        return entries

    def segment_and_parse(self) -> Dict[str, Any]:
        lines = self.text.split('\n')
        sections = {"experience": [], "education": [], "summary": [], "languages": []}
        current_section = None
        
        keywords = {
            "experience": ["experience", "employment", "work history", "expérience", "parcours"],
            "education": ["education", "formation", "diplômes", "academic"],
            "summary": ["summary", "profile", "profil", "objectif", "about me"],
            "languages": ["languages", "langues"]
        }
        
        buffer = [] # Lignes accumulées pour la section courante
        
        for line in lines:
            line_clean = line.strip().lower()
            
            # Détection Header
            is_header = False
            new_section = None
            if len(line_clean) < 50:
                for key, words in keywords.items():
                    if any(w in line_clean for w in words):
                        new_section = key
                        is_header = True
                        break
            
            if is_header:
                # On traite le buffer de la section précédente
                if current_section == "experience":
                    sections["experience"] = self.parse_experience_block(buffer)
                elif current_section == "education":
                    # Simplification pour education : on garde les lignes brutes pour l'instant
                    # ou on applique une logique similaire (TODO)
                    sections["education"] = [EducationEntry(degree=l) for l in buffer if l.strip()]
                elif current_section == "summary":
                    sections["summary"] = " ".join(buffer)
                elif current_section == "languages":
                    sections["languages"] = [l for l in buffer if l.strip()]
                
                current_section = new_section
                buffer = []
            else:
                if current_section:
                    buffer.append(line)
        
        # Traiter le dernier buffer
        if current_section == "experience":
            sections["experience"] = self.parse_experience_block(buffer)
        elif current_section == "summary":
             sections["summary"] = " ".join(buffer)
            
        return sections

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
        parser = AdvancedResumeParser(text)
        basics = parser.extract_regex_fields()
        name = parser.extract_name()
        skills = parser.extract_skills()
        sections = parser.segment_and_parse()
        
        cv_data = CVData(
            raw_text=text,
            meta={"filename": filename, "ocr_applied": str(ocr_applied)},
            basics={
                "name": name if name else "Inconnu", 
                "email": basics["email"], 
                "phone": basics["phone"],
                "location": ""
            },
            links=basics["links"],
            summary=sections["summary"] if isinstance(sections["summary"], str) else "",
            skills_tech=skills["tech"],
            skills_soft=skills["soft"],
            experience=sections["experience"],
            education=sections["education"],
            languages=sections["languages"]
        )
        return cv_data.to_dict()

    except Exception as e:
        print(f"Erreur critique {filename}: {e}")
        return CVData(raw_text=text, meta={"error": str(e)}).to_dict()
