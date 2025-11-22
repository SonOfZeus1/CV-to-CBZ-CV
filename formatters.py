from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML
import os


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

    header_line = job_title
    if company:
        header_line += f" – {company}"
    if location:
        header_line += f", {location}"

    dates_line = dates
    if duration:
        dates_line = dates_line + f" ({duration})" if dates_line else f"({duration})"

    html_parts = [
        "<div style='margin-bottom: 20px; border-left: 3px solid #eee; padding-left: 15px;'>",
        f"<p><strong>{header_line}</strong></p>",
    ]

    if dates_line:
        html_parts.append(f"<p>{dates_line}</p>")

    if summary:
        html_parts.append("<p><strong>Résumé</strong><br>" + summary + "</p>")

    if tasks:
        html_parts.append("<p><strong>Tâches principales</strong></p><ul>")
        html_parts.extend([f"<li>{task}</li>" for task in tasks])
        html_parts.append("</ul>")

    if skills:
        html_parts.append(
            "<p><strong>Compétences liées à cette expérience</strong><br>"
            + ", ".join(skills)
            + "</p>"
        )

    if not summary and not tasks and not skills and full_text:
        html_parts.append(
            "<p style='color:#555; font-size: 0.95em;'>"
            + full_text.replace("\n", "<br>")
            + "</p>"
        )

    html_parts.append("</div>")
    return "".join(html_parts)


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
            formatted_education.append(edu.get("full_text", edu.get("degree", "")))

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
