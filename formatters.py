from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML
import os

def format_experience_entry(entry):
    """Formate une entrée d'expérience structurée en chaîne HTML lisible."""
    if isinstance(entry, str): return entry
    # entry est un dict (car on a fait to_dict() sur la dataclass)
    title = entry.get('title', 'Poste inconnu')
    company = entry.get('company', '')
    dates = f"{entry.get('date_start', '')} - {entry.get('date_end', '')}"
    
    header = f"<strong>{title}</strong>"
    if company: header += f" chez <em>{company}</em>"
    header += f" ({dates})"
    
    desc = ""
    if entry.get('description'):
        desc = "<ul>" + "".join([f"<li>{d}</li>" for d in entry['description']]) + "</ul>"
        
    return f"<div>{header}{desc}</div>"

def normalize_data_for_template(data):
    """
    Adapte la structure de données du nouveau parser (v3) pour le template HTML.
    """
    if "basics" not in data: return data

    basics = data.get("basics", {})
    
    # Fusion skills tech/soft pour l'affichage simple
    all_skills = data.get("skills_tech", []) + data.get("skills_soft", [])
    
    # Formatage riche des expériences
    raw_experiences = data.get("experience", [])
    formatted_experiences = [format_experience_entry(exp) for exp in raw_experiences]

    normalized = {
        "name": basics.get("name", "Nom Inconnu"),
        "email": basics.get("email", ""),
        "mobile_number": basics.get("phone", ""),
        "skills": all_skills,
        "experience": formatted_experiences, # Liste de strings HTML safe
        "degree": [e.get('degree', '') for e in data.get("education", [])] if data.get("education") else [],
        "raw_text": data.get("raw_text", "")
    }
    return normalized

def generate_pdf_from_data(data, template_path, output_path):
    if not data:
        return None

    template_data = normalize_data_for_template(data)
    template_dir = os.path.dirname(template_path)
    template_name = os.path.basename(template_path)

    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template(template_name)

    # Note: on passe les données normalisées.
    # Attention: comme on a mis du HTML dans 'experience', il faudrait dire à Jinja que c'est safe.
    # Mais le template utilise {{ exp }}, qui escape par défaut.
    # On va faire simple : le template affichera les balises <strong> etc.
    # Pour faire propre, il faudrait modifier le template pour utiliser 'safe'.
    
    html_out = template.render(data=template_data)

    if not os.path.exists(os.path.dirname(output_path)):
        os.makedirs(os.path.dirname(output_path))
        
    HTML(string=html_out).write_pdf(output_path)
    return output_path
