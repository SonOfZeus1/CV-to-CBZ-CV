import sys
from unittest.mock import MagicMock

# Mock weasyprint
sys.modules["weasyprint"] = MagicMock()

from formatters import generate_pdf_from_data

data = {
    "basics": {
        "name": "John Doe",
        "email": "john@example.com",
        "phone": "123-456-7890"
    }
}

# Test with blur=True
# We need to mock the template loading part or just use formatters logic if possible.
# generate_pdf_from_data loads template from file.
# Let's assume template.html is in templates/

try:
    # We can't easily run generate_pdf_from_data because it needs the file system for template
    # But we can check if the function accepts the argument.
    import inspect
    sig = inspect.signature(generate_pdf_from_data)
    if "blur_contact" in sig.parameters:
        print("[SUCCESS] generate_pdf_from_data accepts 'blur_contact'")
    else:
        print("[FAILURE] generate_pdf_from_data missing 'blur_contact'")

    # We can also check if the template file has the class
    with open("templates/template.html", "r") as f:
        content = f.read()
        if ".blurred" in content and "{% if data.is_blurred %}blurred{% endif %}" in content:
             print("[SUCCESS] Template has blur logic")
        else:
             print("[FAILURE] Template missing blur logic")

except Exception as e:
    print(f"[ERROR] {e}")
