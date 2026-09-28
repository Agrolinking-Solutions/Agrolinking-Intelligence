"""
Smoke tests for Agrolinking Intelligence Platform.

Run: pytest tests/ -v
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import COMMODITIES, PATHS, MODEL_CONFIG


def test_commodities_defined():
    """Ensure all 17 commodities are defined."""
    assert len(COMMODITIES) == 17, f"Expected 17 commodities, got {len(COMMODITIES)}"
    assert "Rice" in COMMODITIES
    assert "Meat (beef)" in COMMODITIES


def test_paths_configured():
    """Ensure key data paths are configured."""
    assert "master" in PATHS
    assert "forecasts_dir" in PATHS
    assert "logs_dir" in PATHS


def test_model_config_valid():
    """Ensure model configuration has all required keys."""
    required_models = ["arima", "prophet", "xgboost", "lstm", "ensemble"]
    for model in required_models:
        assert model in MODEL_CONFIG, f"Missing model config: {model}"


def test_api_imports():
    """Ensure API can be imported without errors."""
    try:
        import api
        assert hasattr(api, 'app'), "API missing 'app' object"
    except ImportError as e:
        assert False, f"Failed to import API: {e}"


def test_quality_gate_exists():
    """Ensure quality gate script exists."""
    gate_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "pipeline",
        "quality_gate.py"
    )
    assert os.path.exists(gate_path), f"Quality gate not found: {gate_path}"


def test_critical_files_exist():
    """Ensure critical documentation exists."""
    docs_files = [
        "docs/ARCHITECTURE.md",
        "docs/SECURITY.md",
        "docs/FIXES_AND_IMPROVEMENTS.md",
        "docs/OPERATIONAL_GUIDE.md",
        "README.md",
    ]
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for file_path in docs_files:
        full_path = os.path.join(base, file_path)
        assert os.path.exists(full_path), f"Missing: {file_path}"
