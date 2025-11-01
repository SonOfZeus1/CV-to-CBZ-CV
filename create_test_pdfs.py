import os
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch

def create_pdf_from_text(text_file, pdf_file):
    """Crée un PDF à partir d'un fichier texte."""
    with open(text_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    c = canvas.Canvas(pdf_file, pagesize=letter)
    width, height = letter
    
    text = c.beginText()
    text.setTextOrigin(inch, height - inch)
    text.setFont("Helvetica", 10)
    
    for line in lines:
        text.textLine(line.strip())
        
    c.drawText(text)
    c.save()
    print(f"PDF de test créé : {pdf_file}")

if __name__ == "__main__":
    test_cvs_dir = "data/test_cvs"
    if not os.path.exists(test_cvs_dir):
        os.makedirs(test_cvs_dir)

    create_pdf_from_text(os.path.join(test_cvs_dir, "cv1.txt"), os.path.join(test_cvs_dir, "cv1.pdf"))
    create_pdf_from_text(os.path.join(test_cvs_dir, "cv2.txt"), os.path.join(test_cvs_dir, "cv2.pdf"))

    # Suppression des fichiers textes après la conversion
    os.remove(os.path.join(test_cvs_dir, "cv1.txt"))
    os.remove(os.path.join(test_cvs_dir, "cv2.txt"))
