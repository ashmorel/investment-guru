from app.services.guru.llm.scrub import scrub_secrets


def test_scrubs_google_key_query_param():
    text = "request failed: https://x/y?key=AIzaSyFAKE1234567890 (401)"
    out = scrub_secrets(text)
    assert "AIzaSyFAKE1234567890" not in out
    assert "***" in out


def test_scrubs_openai_style_sk_token():
    text = "invalid api key: sk-supersecret456"
    out = scrub_secrets(text)
    assert "sk-supersecret456" not in out
    assert "***" in out


def test_scrubs_google_style_aiza_token():
    text = "bad key AIzaSECRET123 supplied"
    out = scrub_secrets(text)
    assert "AIzaSECRET123" not in out
    assert "***" in out


def test_leaves_ordinary_text_untouched():
    text = "connection timed out after 30s"
    assert scrub_secrets(text) == text
