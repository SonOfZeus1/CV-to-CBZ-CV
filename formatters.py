from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML
import os

def format_experience_entry(entry):
    """Formate une entrée d'expérience structurée en chaîne HTML lisible."""
    if isinstance(entry, str): return entry
    # entry est un dict (via to_dict())
    
    title = entry.get('title', 'Poste inconnu')
    company = entry.get('company', '')
    role = entry.get('role', '')
    dates = f"{entry.get('date_start', '')} - {entry.get('date_end', '')}"
    if entry.get('duration'):
        dates += f" ({entry.get('duration')})"
    
    # En-tête : Titre + Entreprise
    header = f"<div style='margin-bottom: 5px;'><strong>{title}</strong>"
    if company: 
        header += f" | <span style='color:#2c3e50; font-weight:600;'>{company}</span>"
    header += "</div>"
    
    # Sous-en-tête : Rôle + Dates
    sub_header = f"<div style='font-size: 0.9em; color: #666; margin-bottom: 8px;'>"
    if role:
        sub_header += f"<em>{role}</em> &bull; "
    sub_header += f"{dates}</div>"
    
    # Corps
    body = ""
    
    # 1. Contexte
    if entry.get('context'):
        body += f"<div style='font-style: italic; margin-bottom: 5px; color: #444;'>{entry['context']}</div>"
    
    # 2. Responsabilités
    if entry.get('responsibilities'):
        body += "<div><strong>Responsabilités :</strong><ul>"
        body += "".join([f"<li>{d}</li>" for d in entry['responsibilities']])
        body += "</ul></div>"

    # 3. Réalisations / Valeur ajoutée
    if entry.get('achievements'):
        body += "<div style='margin-top:5px;'><strong>Réalisations clés :</strong><ul>"
        body += "".join([f"<li>{d}</li>" for d in entry['achievements']])
        body += "</ul></div>"

    # Fallback (description simple legacy)
    if not body and entry.get('description'):
         body = "<ul>" + "".join([f"<li>{d}</li>" for d in entry['description']]) + "</ul>"

    return f"<div style='margin-bottom: 20px; border-left: 3px solid #eee; padding-left: 15px;'>{header}{sub_header}{body}</div>"

def normalize_data_for_template(data):
    """
    Adapte la structure de données du nouveau parser (v4) pour le template HTML.
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
    
    html_out = template.render(data=template_data)

    if not os.path.exists(os.path.dirname(output_path)):
        os.makedirs(os.path.dirname(output_path))
        
    HTML(string=html_out).write_pdf(output_path)
    return output_path
