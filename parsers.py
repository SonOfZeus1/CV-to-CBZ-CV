import docx
import fitz  # PyMuPDF
import pytesseract
from PIL import Image
import io
import os
import spacy
from pyresparser import ResumeParser

# Charger le modèle spacy. En cas d'échec, le télécharger.
# Le téléchargement des données NLTK est maintenant géré dans le workflow GitHub Actions.
try:
    spacy.load('en_core_web_sm')
except OSError:
    print('Downloading language model for the spaCy POS tagger\n'
        "(don't worry, this will only happen once)")
    from spacy.cli import download
    download('en_core_web_sm')


def extract_text_from_docx(file_path):
    """Extrait le texte d'un fichier .docx."""
    doc = docx.Document(file_path)
    return "\n".join([para.text for para in doc.paragraphs])

def extract_text_from_pdf(file_path):
    """Extrait le texte d'un fichier .pdf, en utilisant l'OCR si nécessaire."""
    text = ""
    try:
        with fitz.open(file_path) as doc:
            for page in doc:
                text += page.get_text()
        
        # Si le texte est très court, il est probable que le PDF soit une image
        if len(text.strip()) < 100:
            print(f"Le PDF {os.path.basename(file_path)} semble être une image. Tentative d'OCR.")
            text = ""
            with fitz.open(file_path) as doc:
                for page_num in range(len(doc)):
                    page = doc.load_page(page_num)
                    pix = page.get_pixmap()
                    img_bytes = pix.tobytes("png")
                    image = Image.open(io.BytesIO(img_bytes))
                    text += pytesseract.image_to_string(image)
    except Exception as e:
        print(f"Erreur lors de l'extraction du texte du PDF {file_path}: {e}")
    return text

def parse_cv(file_path):
    """
    Analyse un fichier de CV (PDF ou DOCX), extrait le texte et les informations structurées.
    """
    _, extension = os.path.splitext(file_path)
    text = ""

    if extension.lower() == ".pdf":
        text = extract_text_from_pdf(file_path)
    elif extension.lower() == ".docx":
        text = extract_text_from_docx(file_path)
    else:
        print(f"Format de fichier non supporté : {extension}")
        return None

    if not text.strip():
        print(f"Aucun texte n'a pu être extrait de {os.path.basename(file_path)}")
        return None
    
    # Création d'un fichier temporaire pour le texte car ResumeParser
    # peut mieux fonctionner avec des fichiers.
    temp_text_file = "temp_resume_text.txt"
    with open(temp_text_file, "w", encoding="utf-8") as f:
        f.write(text)

    try:
        # Utilisation de PyResparser pour analyser le texte
        parser = ResumeParser(temp_text_file)
        data = parser.get_extracted_data()
    except Exception as e:
        print(f"Erreur lors de l'analyse du CV {os.path.basename(file_path)} avec PyResparser: {e}")
        data = {}
    finally:
        if os.path.exists(temp_text_file):
            os.remove(temp_text_file)

    # PyResparser ne renvoie pas toujours toutes les sections.
    # On peut ajouter des regex ici en fallback si nécessaire.
    # Pour l'instant, nous nous en tenons aux données de PyResparser.
    
    # On ajoute le texte brut extrait aux données
    data['raw_text'] = text

    return data
