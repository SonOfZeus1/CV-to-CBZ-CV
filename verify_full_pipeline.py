from parsers import parse_cv_from_text
import json
import logging

# Configure logging to see info
logging.basicConfig(level=logging.INFO)

def test_pipeline():
    # Mock AI response (Pre-parsed data structure that matches what AI would return)
    # Since parse_cv_from_text calls call_ai, we can't easily mock that without patching.
    # ACTUALLY, parse_cv_from_text CALLS call_ai.
    # To test without making a real API call (cost/time), I should probably mock call_ai.
    # checking ai_client import...
    
    from unittest.mock import patch
    
    mock_ai_response = {
        "is_cv": True,
        "total_experience_declared": "10 ans",
        "contact_info": {
            "first_name": "Jean",
            "last_name": "Dupont",
            "email": "jean@test.com",
            "languages": ["Français"]
        },
        "experiences": [
            {
                "job_title": "Dev Senior",
                "company": "Tech Corp",
                "dates_raw": "Janv 2020 - Présent",
                "date_start": "Janv 2020", # RAW TEXT as per new strategy
                "date_end": "Présent",     # RAW TEXT as per new strategy
                "is_current": True,
                "description": "Code all day.\n- Python\n- AI",
                "block_id": "b1",
                "anchor_ids": ["a1"]
            },
            {
                "job_title": "Dev Junior",
                "company": "Small Corp",
                "dates_raw": "Sep 2018 - Déc 2019",
                "date_start": "Sep 2018", # RAW TEXT
                "date_end": "Déc 2019",   # RAW TEXT
                "is_current": False,
                "description": "Bug fix.",
                "block_id": "b2",
                "anchor_ids": ["a2"]
            }
        ],
        "projects_and_other": [
            "Side Project: CV Parser"
        ],
        "education": [
            {"degree": "Master", "school": "Univ", "year": "2018"}
        ]
    }

    # Patch call_ai where it is ACTUALLY used (in ai_parsers.py)
    with patch('ai_parsers.call_ai', return_value=mock_ai_response):
        
        # Run parsing
        result = parse_cv_from_text("Dummy Markdown Text", "test.pdf")
        
        # Verify Output Structure
        print(json.dumps(result, indent=2, ensure_ascii=False))
        
        # Assertions
        basics = result['basics']
        assert basics['total_experience_declared'] == "10 ans"
        # Calculate expected years:
        # Exp 1: Jan 2020 - Now. (Assume Now is 2026-01 for test context or whatever dateparser uses)
        # Exp 2: Sep 2018 - Dec 2019. (1 year 3 months = 1.25 years)
        # dateparser uses current date for "Present".
        print(f"Calculated Experience: {basics.get('total_experience_calculated')}")
        
        exp1 = result['experience'][0]
        assert exp1['date_start'] == "2020-01" # Normalized!
        assert exp1['description'].startswith("Code all day")
        assert "skills" not in exp1 # Should be removed
        
        assert isinstance(result['projects_and_other'], list)
        assert isinstance(result['projects_and_other'][0], str)
        
        print("\n✅ Pipeline Verification Passed!")

if __name__ == "__main__":
    test_pipeline()
