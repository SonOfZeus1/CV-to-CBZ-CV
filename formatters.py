from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML
import os

def normalize_data_for_template(data):
    """
    Adapte la structure de données du nouveau parser pour qu'elle soit compatible
    avec le template HTML existant (ou légèrement modifié).
    """
    # Si c'est déjà l'ancien format (peu probable maintenant), on laisse tel quel
    if "basics" not in data:
        return data

    # Nouveau format -> Format compatible template
    basics = data.get("basics", {})
    
    normalized = {
        "name": basics.get("name", "Nom Inconnu"),
        "email": basics.get("email", ""),
        "mobile_number": basics.get("phone", ""), # Mapping phone -> mobile_number
        "skills": data.get("skills", []),
        "experience": data.get("experience", []),
        "degree": data.get("education", []), # Mapping education -> degree
        "raw_text": data.get("raw_text", "")
    }
    return normalized

def generate_pdf_from_data(data, template_path, output_path):
    """
    Génère un PDF à partir des données extraites d'un CV en utilisant un template HTML.
    """
    if not data:
        print("Les données sont vides, impossible de générer le PDF.")
        return None

    # Préparation des données pour le template
    template_data = normalize_data_for_template(data)

    template_dir = os.path.dirname(template_path)
    template_name = os.path.basename(template_path)

    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template(template_name)

    # Rendu du template HTML avec les données
    html_out = template.render(data=template_data)

    # Génération du PDF
    if not os.path.exists(os.path.dirname(output_path)):
        os.makedirs(os.path.dirname(output_path))
        
    HTML(string=html_out).write_pdf(output_path)
    
    print(f"PDF généré avec succès : {output_path}")
    return output_path
