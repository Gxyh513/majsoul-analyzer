"""Ensure supported deployment paths use the pinned production checkpoint."""
from pathlib import Path
from server.engine.model_policy import MODEL_FILE, MODEL_SHA256


def test_docker_pins_the_supported_weight():
    docker = Path("Dockerfile").read_text(encoding="utf8")
    assert "-o data/models/" + MODEL_FILE in docker
    assert MODEL_SHA256 in docker
    assert "data/models/akagi.pth" not in docker


def test_colab_pins_weights_and_secures_public_backend():
    launcher = Path("colab_launcher.py").read_text(encoding="utf8")
    assert MODEL_FILE in launcher and MODEL_SHA256 in launcher
    assert "MAJSOUL_API_TOKEN" in launcher
    assert "MAJSOUL_ALLOWED_ORIGINS" in launcher
    assert "#api_token=" in launcher
    assert 'os.path.join(models_dir, "akagi.pth")' not in launcher
