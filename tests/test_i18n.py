from app.services.i18n import normalize_locale, get_disclaimer


def test_normalize_locale():
    assert normalize_locale("ru") == "ru"
    assert normalize_locale("ru-RU") == "ru"
    assert normalize_locale("en_US") == "en"
    assert normalize_locale("kk-KZ") == "kk"
    assert normalize_locale("de") == "ru"
    assert normalize_locale(None) == "ru"


def test_get_disclaimer():
    assert "medical diagnosis" in get_disclaimer("en")
    assert "медицинский диагноз" in get_disclaimer("ru")