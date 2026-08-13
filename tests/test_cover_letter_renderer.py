from pathlib import Path

from career_engine.cover_letter import _filename_token


def test_cover_letter_filename_token():
    assert _filename_token("Senior Design Manager") == "Senior_Design_Manager"
    assert _filename_token("") == "Application"
