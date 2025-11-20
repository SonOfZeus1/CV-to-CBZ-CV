from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML
import os

def format_experience_entry(entry):
    """Formate une entrée d'expérience structurée en chaîne HTML lisible."""
    if isinstance(entry, str): return entry
    
    title = entry.get('title', 'Poste inconnu')
    company = entry.get('company', '')
    role = entry.get('role', '')
    dates = f"{entry.get('date_start', '')} - {entry.get('date_end', '')}"
    if entry.get('duration'):
        dates += f" ({entry.get('duration')})"
    
    # Header
    header = f"<div style='margin-bottom: 5px;'><strong>{title}</strong>"
    if company: 
        header += f" | <span style='color:#2c3e50; font-weight:600;'>{company}</span>"
    header += "</div>"
    
    sub_header = f"<div style='font-size: 0.9em; color: #666; margin-bottom: 8px;'>"
    if role: sub_header += f"<em>{role}</em> &bull; "
    sub_header += f"{dates}</div>"
    
    # Body construction
    body = ""
    has_content = False
    
    if entry.get('context'):
        body += f"<div style='font-style: italic; margin-bottom: 5px; color: #444;'>{entry['context']}</div>"
        has_content = True
    
    if entry.get('responsibilities'):
        body += "<div><strong>Responsabilités :</strong><ul>"
        body += "".join([f"<li>{d}</li>" for d in entry['responsibilities']])
        body += "</ul></div>"
        has_content = True

    if entry.get('achievements'):
        body += "<div style='margin-top:5px;'><strong>Réalisations clés :</strong><ul>"
        body += "".join([f"<li>{d}</li>" for d in entry['achievements']])
        body += "</ul></div>"
        has_content = True
        
    # Fallback CRITIQUE : Si le parsing granulaire a échoué mais qu'on a du texte brut
    if not has_content and entry.get('full_text'):
         # On affiche le texte brut en respectant les sauts de ligne
         clean_text = entry['full_text'].replace('\n', '<br>')
         body = f"<div style='color: #555; font-size: 0.95em;'>{clean_text}</div>"

    return f"<div style='margin-bottom: 20px; border-left: 3px solid #eee; padding-left: 15px;'>{header}{sub_header}{body}</div>"

def normalize_data_for_template(data):
    """
    Adapte la structure de données du nouveau parser (v5 - Zero Loss) pour le template HTML.
    """
    if "basics" not in data: return data

    basics = data.get("basics", {})
    
    # Fusion skills
    all_skills = data.get("skills_tech", []) + data.get("skills_soft", [])
    
    # Experiences
    formatted_experiences = [format_experience_entry(exp) for exp in data.get("experience", [])]

    # Education (simple liste de strings ou objets)
    formatted_education = []
    for edu in data.get("education", []):
        if isinstance(edu, str): formatted_education.append(edu)
        else: formatted_education.append(edu.get('full_text', edu.get('degree', '')))

    normalized = {
        "name": basics.get("name", "Nom Inconnu"),
        "email": basics.get("email", ""),
        "mobile_number": basics.get("phone", ""),
        "skills": all_skills,
        "experience": formatted_experiences,
        "education": formatted_education,
        "summary": data.get("summary", ""),
        
        # Nouvelles sections
        "achievements_global": data.get("achievements_global", []),
        "extra_info": data.get("extra_info", []),
        "unmapped": data.get("unmapped", []), # Pour l'annexe
        
        "raw_text": data.get("raw_text", "")
    }
    return normalized

def generate_pdf_from_data(data, template_path, output_path):
    if not data: return None

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
