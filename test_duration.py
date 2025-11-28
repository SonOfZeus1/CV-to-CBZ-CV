import sys
import os
from parsers import calculate_duration_string

def test_duration():
    # Test cases: (start, end, expected_substring)
    test_cases = [
        ("Septembre 2021", "Aujourd'hui", "ans"), 
        ("Janvier 2020", "Décembre 2020", "1 ans"),
        ("Jan 2020", "Mar 2020", "3 mois"), 
        ("2018", "2019", "2 ans"), 
        ("Février 2022", "Présent", "ans"),
    ]

    print("Testing Duration Calculation...")
    failed = False
    for start, end, expected in test_cases:
        try:
            duration = calculate_duration_string(start, end)
            print(f"Input: {start} - {end} -> Duration: {duration}")
            if expected not in duration and expected != "Wait":
                print(f"FAIL: Expected '{expected}' in '{duration}'")
                failed = True
        except Exception as e:
            print(f"Error for {start} - {end}: {e}")
            failed = True
            
    if failed:
        sys.exit(1)
    else:
        print("All tests passed!")

if __name__ == "__main__":
    test_duration()
