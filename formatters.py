from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML
import os
import re


def format_experience_entry(entry):
    """Formate une expérience au format imposé (ligne, dates, résumé, tâches, skills)."""
    if isinstance(entry, str):
        return entry

    job_title = entry.get("job_title", "Poste inconnu").strip() or "Poste inconnu"
    company = entry.get("company", "").strip()
    location = entry.get("location", "").strip()
    dates = entry.get("dates", "").strip()
    duration = entry.get("duration", "").strip()
    summary = entry.get("summary", "").strip()
    tasks = entry.get("tasks", []) or []
    skills = entry.get("skills", []) or []
    full_text = entry.get("full_text", "")

    # Clean location: remove "Canada" (case insensitive)
    if location:
        # Remove ", Canada" or "Canada"
        location = re.sub(r",?\s*canada", "", location, flags=re.IGNORECASE).strip()
        # Remove trailing comma if any
        location = location.rstrip(",")

    header_line = job_title
    if company:
        header_line += f" – {company}"
    if location:
        # Extract just the city (first part before comma)
        city = location.split(',')[0].strip()
        header_line += f" <span class='exp-location'>({city})</span>"



    # New Layout: Header and Dates on the same line using Flexbox with CSS classes
    html_parts = [
        "<div style='margin-bottom: 20px; border-left: 3px solid #eee; padding-left: 15px;'>",
        "<div class='exp-header-row'>",
        f"<div class='exp-header-left'>{header_line}</div>",
        "<div class='exp-header-right'>"
    ]

    if dates:
        html_parts.append(f"<span class='exp-date'>{dates}</span>")
    
    if duration:
        html_parts.append(f"<span class='exp-duration'>({duration})</span>")
    
    html_parts.append("</div></div>") # Close right div and row div

    if summary:
        html_parts.append("<p style='margin-top: 0;'><strong>Résumé</strong><br>" + summary + "</p>")

    if tasks:
        html_parts.append("<p><strong>Tâches principales</strong></p><ul>")
        html_parts.extend([f"<li>{task}</li>" for task in tasks])
        html_parts.append("</ul>")

    if skills:
        html_parts.append("<p><strong>Environnement Technologique</strong></p>")
        html_parts.append("<ul style='list-style-type: none; padding: 0; margin: 0;'>")
        for skill in skills:
            html_parts.append(
                f"<li style='display: inline-block; background: #eef2f5; padding: 4px 8px; margin: 2px; border-radius: 4px; font-size: 13px; color: #2c3e50; border: 1px solid #dce4ec;'>{skill}</li>"
            )
        html_parts.append("</ul>")

    if not summary and not tasks and not skills and full_text:
        html_parts.append(
            "<p style='color:#555; font-size: 0.95em;'>"
            + full_text.replace("\n", "<br>")
            + "</p>"
        )

    html_parts.append("</div>")
    return "".join(html_parts)


def format_education_entry(entry) -> str:
    """
    Formats a single education entry into HTML.
    Handles both dictionary and EducationEntry object.
    """
    # Helper to get attribute from dict or object
    def get_field(obj, field_name):
        if isinstance(obj, dict):
            return obj.get(field_name, "")
        return getattr(obj, field_name, "")

    degree = get_field(entry, "degree")
    institution = get_field(entry, "institution")
    year = get_field(entry, "date_end") or get_field(entry, "date_start")
    
    # Clean up institution location if needed (e.g. remove Canada)
    if institution:
        institution = re.sub(r",?\s*canada", "", institution, flags=re.IGNORECASE).strip()
        institution = institution.rstrip(",")

    header_line = degree
    if institution:
        header_line += f" – {institution}"
        
    html_parts = [
        "<div style='margin-bottom: 15px; border-left: 3px solid #eee; padding-left: 0px;'>",
        "<div class='exp-header-row'>",
        f"<div class='exp-header-left'>{header_line}</div>",
        f"<div class='exp-header-right'><span class='exp-date'>{year}</span></div>",
        "</div></div>"
    ]
    
    return "".join(html_parts)


def format_contact_info(contact_info) -> str:
    """
    Formats contact info into HTML.
    """
    html_parts = []
    
    # Name
    if contact_info.name:
        html_parts.append(f"<h1>{contact_info.name}</h1>")
    
    # Contact details container
    html_parts.append("<div class='contact-info'>")
    
    details = []
    if contact_info.email:
        details.append(f"<strong>Email:</strong> {contact_info.email}")
    if contact_info.phone:
        details.append(f"<strong>Téléphone:</strong> {contact_info.phone}")
    if contact_info.linkedin:
        details.append(f"<strong>LinkedIn:</strong> {contact_info.linkedin}")
    if contact_info.location:
        details.append(f"<strong>Localisation:</strong> {contact_info.location}")
        
    html_parts.append(" | ".join(details))
    html_parts.append("</div>")
    
    return "\n".join(html_parts)


def normalize_data_for_template(data):
    """
    Adapte la structure de données du parser vers le template HTML.
    """
    if "basics" not in data:
        return data

    basics = data.get("basics", {})

    # Fusion skills techniques et soft pour l'affichage global
    all_skills = data.get("skills_tech", []) + data.get("skills_soft", [])

    formatted_experiences = [format_experience_entry(exp) for exp in data.get("experience", [])]

    formatted_education = []
    for edu in data.get("education", []):
        if isinstance(edu, str):
            formatted_education.append(edu)
        else:
            formatted_education.append(format_education_entry(edu))

    normalized = {
        "name": basics.get("name", "Nom Inconnu"),
        "email": basics.get("email", ""),
        "mobile_number": basics.get("phone", ""),
        "skills": all_skills,
        "experience": formatted_experiences,
        "education": formatted_education,
        "summary": data.get("summary", ""),
        "achievements_global": data.get("achievements_global", []),
        "extra_info": data.get("extra_info", []),
        "unmapped": data.get("unmapped", []),
        "raw_text": data.get("raw_text", ""),
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
