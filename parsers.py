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

# --- SCHÉMA DE DONNÉES (V4 - Riche) ---

@dataclass
class ExperienceEntry:
    title: str = ""
    company: str = ""
    role: str = ""
    location: str = ""
    date_start: str = ""
    date_end: str = ""
    duration: str = ""
    
    # Segmentation du corps de l'expérience
    context: str = ""
    responsibilities: List[str] = field(default_factory=list)
    achievements: List[str] = field(default_factory=list)
    description: List[str] = field(default_factory=list) # Fallback legacy

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
        self.doc = self.nlp(text[:100000])
        
    def extract_regex_fields(self) -> Dict[str, Any]:
        email_regex = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        phone_regex = r'(\+?\d{1,3}[-.\s]?)?(\(?\d{2,4}\)?[-.\s]?)?(\d{2,4}[-.\s]?){2,4}'
        link_regex = r'(https?://\S+|www\.\S+|linkedin\.com/in/\S+|github\.com/\S+)'
        
        emails = list(set(re.findall(email_regex, self.text)))
        phones_iter = re.finditer(phone_regex, self.text)
        phones = [m.group(0).strip() for m in phones_iter if len(re.sub(r'\D', '', m.group(0))) >= 9]
        links = list(set(re.findall(link_regex, self.text, re.IGNORECASE)))
        
        return {"email": emails[0] if emails else "", "phone": phones[0] if phones else "", "links": links}

    def extract_name(self) -> str:
        """Détection robuste du nom (priorité aux lignes d'en-tête en majuscules)."""
        lines = self.text.split('\n')[:30] # On regarde les 30 premières lignes
        
        # Mots à exclure
        blacklist = {"curriculum", "vitae", "resume", "cv", "email", "téléphone", "phone", "adresse", "page", "profil", "summary"}
        
        potential_names = []
        
        for line in lines:
            line_clean = line.strip()
            if not line_clean: continue
            
            words = line_clean.split()
            # Un nom fait généralement 2 ou 3 mots
            if 2 <= len(words) <= 3:
                # Filtrage mots clés
                if any(w.lower() in blacklist for w in words):
                    continue
                # Filtrage caractères bizarres (dates, chiffres)
                if any(c.isdigit() or c in "@+/" for c in line_clean):
                    continue
                
                # Priorité 1 : TOUT EN MAJUSCULES (ex: RICHARD BOURBEAU)
                if line_clean.isupper():
                    return line_clean.title() # On retourne formaté
                
                # Priorité 2 : Title Case (ex: Richard Bourbeau)
                if line_clean.istitle():
                    potential_names.append(line_clean)

        # Si on n'a pas trouvé de MAJUSCULES, on prend le premier Title Case
        if potential_names:
            return potential_names[0]
            
        # Fallback Spacy (moins fiable)
        for ent in self.doc.ents[:10]:
            if ent.label_ == "PERSON" and len(ent.text.split()) >= 2 and "\n" not in ent.text:
                return ent.text.strip()
                
        return "Inconnu"

    def extract_skills(self) -> Dict[str, List[str]]:
        tech_keywords = {"python", "java", "c++", "sql", "javascript", "react", "docker", "aws", "linux", "git", "html", "css", "kubernetes", "azure", "vba", "oracle", "visio", "jira", "confluence", "power bi", "tableau", "sap"}
        soft_keywords = {"management", "communication", "leadership", "agile", "scrum", "anglais", "français", "espagnol", "analyste", "stratégique", "coordination"}
        
        tech_found = set()
        soft_found = set()
        
        # Analyse simple sur tokens
        tokens = [t.text.lower() for t in self.doc if not t.is_stop]
        for t in tokens:
            if t in tech_keywords:
                tech_found.add(t.capitalize())
            if t in soft_keywords:
                soft_found.add(t.capitalize())
        
        return {"tech": list(tech_found), "soft": list(soft_found)}

    def parse_mandat_style_experience(self, text_block: List[str]) -> List[ExperienceEntry]:
        """
        Parser spécialisé pour les CV structurés par 'MANDAT X'.
        Découpe le texte en blocs Mandat et extrait finement les infos.
        """
        full_text = "\n".join(text_block)
        # Regex pour splitter sur "MANDAT X" (case insensitive)
        mandat_split = re.split(r'(?i)\n\s*(MANDAT\s*\d+.*)\n', full_text)
        
        entries = []
        
        # Le split renvoie [intro, titre_mandat1, corps_mandat1, titre_mandat2, corps_mandat2...]
        # On ignore l'intro souvent vide ou titre de section
        for i in range(1, len(mandat_split), 2):
            mandat_title_line = mandat_split[i].strip()
            mandat_body = mandat_split[i+1] if i+1 < len(mandat_split) else ""
            
            entry = ExperienceEntry()
            entry.title = mandat_title_line # Par défaut le titre est "MANDAT X..."
            
            lines = mandat_body.split('\n')
            lines = [l.strip() for l in lines if l.strip()]
            
            # Analyse des premières lignes pour extraire Client / Rôle / Dates
            # On s'attend à ce format :
            # 1. Titre/Sous-titre (optionnel)
            # 2. Client (ex: SQI)
            # 3. Rôle
            # 4. Dates
            
            header_lines_count = 0
            max_header_lines = 6
            
            for j, line in enumerate(lines[:max_header_lines]):
                # Détection Dates (Format robuste)
                # Ex: "Avril 2023 à aujourd’hui (2 ans et 2 mois) – 1 000 jp"
                # Regex améliorée pour capturer start, end, duration, jp
                date_match = re.search(
                    r'([A-Za-zûé]+\s\d{4}|\d{2}/\d{4})\s*[à-]\s*([A-Za-zûé]+\s\d{4}|aujourd’hui|présent|maintenant)(?:\s*\(([^)]+)\))?(?:.*[–-]\s*(.*))?',
                    line, re.IGNORECASE
                )
                
                if date_match:
                    entry.date_start = date_match.group(1).strip()
                    entry.date_end = date_match.group(2).strip()
                    if date_match.group(3):
                        entry.duration = date_match.group(3).strip()
                    
                    # Si on a trouvé la date, on suppose que les lignes d'avant sont Client/Rôle
                    # Heuristique simple : Ligne juste avant = Rôle, Ligne d'avant = Client
                    if j > 0:
                        entry.role = lines[j-1]
                    if j > 1:
                        entry.company = lines[j-2]
                    
                    header_lines_count = j + 1
                    break
            
            # Si on n'a pas trouvé de date, on essaie une heuristique de position pure
            if not entry.date_start and len(lines) >= 3:
                entry.company = lines[0]
                entry.role = lines[1]
                # On laisse date vide ou on cherche plus loin
            
            # Segmentation du corps (Contexte vs Responsabilités vs Valeur Ajoutée)
            body_lines = lines[header_lines_count:]
            current_sub = "context"
            buffer_context = []
            
            for line in body_lines:
                # Détection de puces pour responsabilités
                if re.match(r'^[-•o*]\s', line):
                    clean_line = re.sub(r'^[-•o*]\s?', '', line).strip()
                    entry.responsibilities.append(clean_line)
                    current_sub = "responsibilities"
                # Détection de sous-titres (Valeur ajoutée, Résultats)
                elif "valeur ajoutée" in line.lower() or "résultats" in line.lower() or "bénéfices" in line.lower():
                     current_sub = "achievements"
                elif current_sub == "achievements":
                     clean_line = re.sub(r'^[-•o*]\s?', '', line).strip()
                     entry.achievements.append(clean_line)
                elif current_sub == "context":
                    buffer_context.append(line)
            
            entry.context = " ".join(buffer_context)
            entries.append(entry)
            
        return entries

    def parse_standard_experience(self, text_block: List[str]) -> List[ExperienceEntry]:
        """Fallback : Parsing standard ligne par ligne."""
        entries = []
        current_entry = None
        
        date_pattern = r'((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s?\d{4}|\d{2}/\d{4}|\d{4})\s*[-–toà]\s*((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s?\d{4}|\d{2}/\d{4}|\d{4}|present|aujourd\'hui|now)'
        
        for line in text_block:
            line = line.strip()
            if not line: continue
            
            date_match = re.search(date_pattern, line, re.IGNORECASE)
            
            if date_match:
                if current_entry:
                    entries.append(current_entry)
                
                current_entry = ExperienceEntry(
                    date_start=date_match.group(1),
                    date_end=date_match.group(2),
                    description=[]
                )
                
                clean_line = re.sub(date_pattern, '', line, flags=re.IGNORECASE).strip()
                if len(clean_line) > 3:
                    current_entry.title = clean_line
            
            elif current_entry:
                clean_desc = re.sub(r'^[-•*]\s?', '', line).strip()
                current_entry.description.append(clean_desc)
                current_entry.responsibilities.append(clean_desc) # Dual populating
        
        if current_entry:
            entries.append(current_entry)
            
        return entries

    def segment_and_parse(self) -> Dict[str, Any]:
        lines = self.text.split('\n')
        sections = {"experience": [], "education": [], "summary": [], "languages": []}
        current_section = None
        
        keywords = {
            "experience": ["experience", "employment", "work history", "expérience", "parcours", "mandats"],
            "education": ["education", "formation", "diplômes", "academic"],
            "summary": ["summary", "profile", "profil", "objectif", "about me"],
            "languages": ["languages", "langues"]
        }
        
        buffer = [] 
        
        for line in lines:
            line_clean = line.strip().lower()
            
            is_header = False
            new_section = None
            if len(line_clean) < 50:
                for key, words in keywords.items():
                    if any(w in line_clean for w in words) and not "mandat" in line_clean: # Eviter de trigger header sur "Mandat 1"
                        new_section = key
                        is_header = True
                        break
            
            if is_header:
                if current_section == "experience":
                    # Choix de la stratégie : Mandat vs Standard
                    full_buffer = "\n".join(buffer)
                    if "MANDAT" in full_buffer.upper():
                        sections["experience"] = self.parse_mandat_style_experience(buffer)
                    else:
                        sections["experience"] = self.parse_standard_experience(buffer)
                        
                elif current_section == "education":
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
            full_buffer = "\n".join(buffer)
            if "MANDAT" in full_buffer.upper():
                sections["experience"] = self.parse_mandat_style_experience(buffer)
            else:
                sections["experience"] = self.parse_standard_experience(buffer)
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
