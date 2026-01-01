import unittest
from text_processor import preprocess_markdown
from date_extractor import extract_date_anchors, DateAnchor

class TestPipeline2(unittest.TestCase):

    def test_digit_join(self):
        """Test that spaced digits are joined correctly."""
        raw = "Experience in 2 0 0 8 and 2 0 1 4."
        expected = "Experience in 2008 and 2014."
        self.assertEqual(preprocess_markdown(raw), expected)

    def test_year_only_date(self):
        """Test that a single year produces a year-only date anchor."""
        raw = "Project Manager\n2017\nCompany X"
        anchors = extract_date_anchors(raw)
        
        # Find the anchor for 2017
        anchor_2017 = next((a for a in anchors if a.raw == "2017"), None)
        self.assertIsNotNone(anchor_2017)
        self.assertEqual(anchor_2017.start, "2017")
        self.assertEqual(anchor_2017.end, "2017") # Single year start=end roughly
        self.assertTrue(anchor_2017.start_is_year_only)
        self.assertTrue(anchor_2017.end_is_year_only)
        self.assertEqual(anchor_2017.precision, "year")

    def test_range_preservation(self):
        """Test that 'Mar 2024 – Present' is captured exactly."""
        raw = "Software Engineer | Google (Mar 2024 – Present)"
        anchors = extract_date_anchors(raw)
        
        anchor = next((a for a in anchors if "Mar 2024" in a.raw), None)
        self.assertIsNotNone(anchor)
        self.assertEqual(anchor.start, "2024-03")
        self.assertIsNone(anchor.end) # Present -> None end date usually, or we check is_current
        self.assertTrue(anchor.is_current)
        self.assertFalse(anchor.start_is_year_only)

if __name__ == '__main__':
    unittest.main()
