from career_engine.template import status


def test_installed_approved_template_integrity():
    result = status()
    assert result["valid"] is True
    assert result["hash_matches"] is True
    assert result["zip_valid"] is True
