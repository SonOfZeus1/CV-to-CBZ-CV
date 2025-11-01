from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML
import os

def generate_pdf_from_data(data, template_path, output_path):
    """
    Génère un PDF à partir des données extraites d'un CV en utilisant un template HTML.
    """
    if not data:
        print("Les données sont vides, impossible de générer le PDF.")
        return None

    template_dir = os.path.dirname(template_path)
    template_name = os.path.basename(template_path)

    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template(template_name)

    # Rendu du template HTML avec les données
    html_out = template.render(data=data)

    # Génération du PDF
    if not os.path.exists(os.path.dirname(output_path)):
        os.makedirs(os.path.dirname(output_path))
        
    HTML(string=html_out).write_pdf(output_path)
    
    print(f"PDF généré avec succès : {output_path}")
    return output_path
