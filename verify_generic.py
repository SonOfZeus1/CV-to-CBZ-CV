import os
import sys
import json
import logging
from parsers import parse_cv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def verify_any_cv(file_path):
    if not os.path.exists(file_path):
        logger.error(f"File not found: {file_path}")
        return

    logger.info(f"Processing CV: {file_path}")
    result = parse_cv(file_path)
    
    if not result:
        logger.error("Parsing failed (returned None).")
        return

    # Check Contact
    basics = result.get("basics", {})
    name = basics.get("name", "Unknown")
    print(f"\n--- CONTACT INFO ---")
    print(f"Name: {name}")
    print(f"Title: {basics.get('title', '')}")
    print(f"Email: {basics.get('email', '')}")
    
    if name.upper() in ["COMPÉTENCES TECHNIQUES", "CURRICULUM VITAE", "EXPERIENCE"]:
        print("\n[FAIL] Name is still a section title!")
    else:
        print("\n[OK] Name looks like a name (or at least not a known section title).")

    # Check Experience
    print(f"\n--- EXPERIENCE ---")
    experiences = result.get("experience", [])
    print(f"Found {len(experiences)} experiences.")
    for i, exp in enumerate(experiences):
        tasks = exp.get("tasks", [])
        print(f"Exp {i+1}: {exp.get('job_title')} at {exp.get('company')} -> {len(tasks)} tasks")
        if not tasks:
            print(f"  [WARNING] No tasks found for this experience!")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 verify_generic.py <path_to_cv.pdf>")
        sys.exit(1)
    
    cv_path = sys.argv[1]
    verify_any_cv(cv_path)
