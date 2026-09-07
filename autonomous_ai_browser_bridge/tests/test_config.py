from bridge.config import settings


def test_limits():
    assert 1 <= settings.max_pending_jobs <= 3
    assert 30 <= settings.hard_timeout_sec <= 3600


def test_live_configuration_has_three_queue_slots_and_thirty_minute_budget():
    assert settings.max_pending_jobs == 3
    assert settings.hard_timeout_sec == 1800


def test_gemini_fallback_has_an_isolated_profile_and_complete_contract():
    assert settings.gemini_profile_dir != settings.browser_profile_dir
    assert settings.gemini_start_url == "https://gemini.google.com/app"
    assert settings.gemini_configured is True
    assert settings.gemini_prompt_delivery_mode == "file"
    assert settings.gemini_response_mode == "download"
    assert settings.gemini_file_input_selector
    assert settings.gemini_upload_open_selector
    assert settings.gemini_download_open_selector == ".attachment-container"
    assert "drive-viewer" in settings.gemini_download_link_selector
    assert settings.gemini_desired_model == "3.1 Pro"
    assert "mode picker" in settings.gemini_model_control_selector


def test_po3_folders_are_separate():
    assert "po3_ai_bus" not in {p.casefold() for p in settings.folder_response_dir.resolve().parts}
