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
from datetime import datetime

# --- SCHÉMA DE DONNÉES (V5 - Universel / Zero Data Loss) ---

@dataclass
class ExperienceEntry:
    title: str = ""
    company: str = ""
    role: str = ""
    location: str = ""
    date_start: str = ""
    date_end: str = ""
    duration: str = ""
    
    # Segmentation fine
    context: str = ""
    responsibilities: List[str] = field(default_factory=list)
    achievements: List[str] = field(default_factory=list)
    
    # Filet de sécurité local
    full_text: str = "" # Contient TOUTES les lignes de ce mandat concaténées

@dataclass
class EducationEntry:
    degree: str = ""
    institution: str = ""
    date_start: str = ""
    date_end: str = ""
    full_text: str = ""

@dataclass
class CVData:
    """Structure universelle garantissant qu'aucune ligne n'est perdue."""
    meta: Dict[str, str] = field(default_factory=dict)
    basics: Dict[str, str] = field(default_factory=lambda: {
        "name": "", "email": "", "phone": "", "location": ""
    })
    
    # Sections Standard
    summary: str = ""
    skills_tech: List[str] = field(default_factory=list)
    skills_soft: List[str] = field(default_factory=list)
    experience: List[ExperienceEntry] = field(default_factory=list)
    education: List[EducationEntry] = field(default_factory=list)
    languages: List[str] = field(default_factory=list)
    links: List[str] = field(default_factory=list)
    
    # Sections "Fourre-tout" mais structurées
    achievements_global: List[str] = field(default_factory=list) # Pour ce qui est hors experience
    extra_info: List[str] = field(default_factory=list)
    
    # Filet de sécurité absolu
    unmapped: List[str] = field(default_factory=list) # Lignes orphelines
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
                    del pix
                    del image
    except Exception as e:
        print(f"Erreur extraction PDF {file_path}: {e}")
    
    return text, ocr_applied

# --- PARSER UNIVERSEL (CLASSIFICATION + SEGMENTATION) ---

class UniversalParser:
    def __init__(self, text: str):
        self.text = text
        self.lines = [line.strip() for line in text.split('\n') if line.strip()] # On garde tout sauf vide
        self.nlp = load_spacy_model()
        self.doc = self.nlp(text[:100000])
        
    def extract_regex_fields(self) -> Dict[str, Any]:
        # Similaire à avant mais encapsulé
        email_regex = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        phone_regex = r'(\+?\d{1,3}[-.\s]?)?(\(?\d{2,4}\)?[-.\s]?)?(\d{2,4}[-.\s]?){2,4}'
        link_regex = r'(https?://\S+|www\.\S+|linkedin\.com/in/\S+|github\.com/\S+)'
        
        emails = list(set(re.findall(email_regex, self.text)))
        phones_iter = re.finditer(phone_regex, self.text)
        phones = [m.group(0).strip() for m in phones_iter if len(re.sub(r'\D', '', m.group(0))) >= 9]
        links = list(set(re.findall(link_regex, self.text, re.IGNORECASE)))
        
        return {"email": emails[0] if emails else "", "phone": phones[0] if phones else "", "links": links}

    def extract_name(self) -> str:
        # Logique robuste (majuscules)
        lines = self.text.split('\n')[:40]
        blacklist = {"curriculum", "vitae", "resume", "cv", "email", "phone", "adresse", "page", "profil", "summary", "compétences", "skills"}
        potential_names = []
        
        for line in lines:
            line_clean = line.strip()
            if not line_clean: continue
            words = line_clean.split()
            if 2 <= len(words) <= 4:
                if any(w.lower() in blacklist for w in words): continue
                if any(c.isdigit() or c in "@+/" for c in line_clean): continue
                
                if line_clean.isupper(): return line_clean.title()
                if line_clean.istitle(): potential_names.append(line_clean)

        return potential_names[0] if potential_names else "Inconnu"

    def extract_skills(self) -> Dict[str, List[str]]:
        tech_keywords = {"python", "java", "c++", "sql", "javascript", "react", "docker", "aws", "linux", "git", "html", "css", "kubernetes", "azure", "vba", "oracle", "visio", "jira", "confluence", "power bi", "tableau", "sap", ".net", "c#"}
        soft_keywords = {"management", "communication", "leadership", "agile", "scrum", "anglais", "français", "espagnol", "analyste", "stratégique", "coordination", "gestion de projet"}
        
        tech_found = set()
        soft_found = set()
        tokens = [t.text.lower() for t in self.doc if not t.is_stop]
        for t in tokens:
            if t in tech_keywords: tech_found.add(t.capitalize())
            if t in soft_keywords: soft_found.add(t.capitalize())
        return {"tech": list(tech_found), "soft": list(soft_found)}

    def parse_experience_granular(self, raw_lines: List[str]) -> List[ExperienceEntry]:
        """
        Découpe un bloc de lignes 'Expérience' en mandats distincts sans rien perdre.
        """
        full_text_block = "\n".join(raw_lines)
        entries = []
        
        # 1. Découpage par "MANDAT" ou Dates
        # On construit une liste de "sous-blocs"
        sub_blocks = []
        current_sub_block = []
        
        # Regex pour détecter le début d'un nouveau mandat/job
        # Cas 1: "MANDAT X"
        # Cas 2: Une ligne qui est une date isolée ou date range claire en début de bloc
        mandat_header_regex = r'(?i)^\s*(MANDAT\s*\d+|EXPÉRIENCE\s*\d+|POSTE\s*\d+)'
        
        for line in raw_lines:
            is_new_block = False
            if re.match(mandat_header_regex, line):
                is_new_block = True
            
            if is_new_block and current_sub_block:
                sub_blocks.append(current_sub_block)
                current_sub_block = []
            
            current_sub_block.append(line)
            
        if current_sub_block:
            sub_blocks.append(current_sub_block)
            
        # Si un seul bloc mais très gros, on essaie de splitter par dates si pas de "MANDAT"
        if len(sub_blocks) <= 1 and not re.search(mandat_header_regex, full_text_block):
             # TODO: Logique de split par dates pour CV standards (non implémentée ici pour focus Mandat)
             pass

        # 2. Parsing de chaque sous-bloc
        for block in sub_blocks:
            entry = ExperienceEntry()
            entry.full_text = "\n".join(block) # Sauvegarde intégrale
            
            # Extraction header (5 premières lignes)
            header_lines = block[:6]
            
            # Titre (souvent la ligne 1 si Mandat)
            if re.match(mandat_header_regex, header_lines[0]):
                entry.title = header_lines[0]
                # Parfois le titre continue ligne 2
                if len(header_lines) > 1 and len(header_lines[1]) < 50:
                    entry.title += " - " + header_lines[1]
            else:
                entry.title = header_lines[0]
            
            # Dates & Company
            for line in header_lines:
                # Regex Dates
                date_match = re.search(
                    r'([A-Za-zûé]+\s\d{4}|\d{2}/\d{4})\s*[à-]\s*([A-Za-zûé]+\s\d{4}|aujourd’hui|présent|maintenant)(?:\s*\(([^)]+)\))?',
                    line, re.IGNORECASE
                )
                if date_match:
                    entry.date_start = date_match.group(1)
                    entry.date_end = date_match.group(2)
                    entry.duration = date_match.group(3) if date_match.group(3) else ""
                
                # Heuristique Company (si ligne contient Inc, SA, ou détecté ORG)
                if not entry.company and len(line) < 50 and not date_match and "MANDAT" not in line.upper():
                    # Check simple
                    if any(x in line.lower() for x in ["inc.", "ltd", "s.a.", "groupe", "société", "banque", "ministere"]):
                        entry.company = line
            
            # Corps (tout sauf ce qui ressemble à du header)
            context_buffer = []
            for line in block:
                if line in entry.title or (entry.date_start and entry.date_start in line):
                    continue # Skip header lines identified
                
                # Classification ligne par ligne
                clean_line = re.sub(r'^[-•o*]\s?', '', line).strip()
                
                if re.match(r'^[-•o*]\s', line):
                    entry.responsibilities.append(clean_line)
                elif "valeur ajoutée" in line.lower() or "réalisations" in line.lower():
                    # Les lignes suivantes seront des achievements
                    pass 
                elif len(entry.responsibilities) > 0 and len(line) < 80:
                    # Probablement un achievement après les responsabilités
                    entry.achievements.append(clean_line)
                else:
                    context_buffer.append(line)
            
            entry.context = " ".join(context_buffer[:3]) # On prend les 3 premières lignes de contexte
            entries.append(entry)
            
        return entries

    def classify_and_parse(self) -> Dict[str, Any]:
        """
        Machine à états qui classe chaque ligne du CV dans une section.
        Garantit que rien n'est perdu.
        """
        sections_raw = {
            "experience": [],
            "education": [],
            "summary": [],
            "skills": [],
            "languages": [],
            "achievements_global": [],
            "extra_info": [],
            "unmapped": [] # Poubelle par défaut (sera vide si tout va bien)
        }
        
        # Mapping keywords -> section
        keywords_map = {
            "experience": ["expérience", "experience", "mandats", "parcours professionnel", "work history"],
            "education": ["formation", "education", "diplômes", "études"],
            "skills": ["compétences", "skills", "expertises", "connaissances techniques"],
            "summary": ["résumé", "summary", "profil", "objectif", "about"],
            "languages": ["langues", "languages"],
            "achievements_global": ["réalisations", "projets", "projects"],
            "extra_info": ["intérêts", "hobbies", "bénévolat", "certifications"]
        }
        
        current_section = "unmapped" # Au début, c'est souvent le Header (nom/contact) -> on mettra dans unmapped ou summary
        
        # 1. SEGMENTATION & CLASSIFICATION
        for i, line in enumerate(self.lines):
            line_lower = line.lower()
            
            # Détection Header de Section
            is_header = False
            if len(line) < 60: # Un titre est rarement long
                for section, keys in keywords_map.items():
                    # Titre exact ou très proche (ex: "--- EXPÉRIENCE ---")
                    if any(k in line_lower for k in keys):
                        # Évite faux positifs comme "J'ai une expérience en Java"
                        if len(line.split()) < 5 or line.isupper() or line.endswith(':'):
                            current_section = section
                            is_header = True
                            break
            
            # Si c'est un header, on ne l'ajoute pas forcément au contenu, ou si ? 
            # Pour le Zero Loss, on l'ajoute !
            sections_raw[current_section].append(line)

        # 2. PARSING GRANULAIRE
        # Maintenant qu'on a des blocs bruts, on les structure
        
        # Experience -> ExperienceEntries
        structured_experience = self.parse_experience_granular(sections_raw["experience"])
        
        # Education -> EducationEntries (simple)
        structured_education = []
        for line in sections_raw["education"]:
             # Simple wrap pour l'instant
             structured_education.append(EducationEntry(degree=line, full_text=line))
             
        return {
            "raw_sections": sections_raw,
            "structured_experience": structured_experience,
            "structured_education": structured_education
        }

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
        
        # 1. Extraction Basics
        basics = parser.extract_regex_fields()
        name = parser.extract_name()
        skills = parser.extract_skills() # Détection par mots clés dans tout le texte
        
        # 2. Classification Universelle
        parse_result = parser.classify_and_parse()
        raw_sections = parse_result["raw_sections"]
        
        # 3. Construction de l'objet Final
        # Tout ce qui est dans 'unmapped' et qui n'est pas le nom/contact va dans extra ou summary
        # Pour être sûr, on garde 'unmapped' tel quel pour l'Annexe.
        
        cv_data = CVData(
            raw_text=text,
            meta={"filename": filename, "ocr_applied": str(ocr_applied)},
            basics={
                "name": name, 
                "email": basics["email"], 
                "phone": basics["phone"],
                "location": ""
            },
            links=basics["links"],
            
            # Sections structurées
            experience=parse_result["structured_experience"],
            education=parse_result["structured_education"],
            
            # Sections Texte Simple (join)
            summary="\n".join(raw_sections["summary"]),
            languages=raw_sections["languages"],
            skills_tech=skills["tech"], # On garde l'extraction mots-clés auto
            skills_soft=skills["soft"],
            
            achievements_global=raw_sections["achievements_global"],
            extra_info=raw_sections["extra_info"],
            
            # LE FILET DE SÉCURITÉ
            unmapped=raw_sections["unmapped"]
        )
        
        return cv_data.to_dict()

    except Exception as e:
        print(f"Erreur critique {filename}: {e}")
        return CVData(raw_text=text, meta={"error": str(e)}).to_dict()
