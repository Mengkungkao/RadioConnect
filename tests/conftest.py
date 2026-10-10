import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest


@pytest.fixture(autouse=True)
def private_radio_store(tmp_path, monkeypatch):
    """The radio identity and keys shared with other radio apps live under
    mFruit OS's home; tests get their own, never the host's."""
    monkeypatch.setenv("MFRUIT_HOME", str(tmp_path / "mfruit-home"))
    monkeypatch.delenv("WHISPLAY_OS_HOME", raising=False)
    # Nor the app's own data folder, managed or not.
    monkeypatch.setenv("RADIOCONNECT_DATA_DIR", str(tmp_path / "radioconnect-data"))
    monkeypatch.setenv("WALKIE_DATA_DIR", str(tmp_path / "walkietalkie-data"))
    monkeypatch.delenv("WHISPLAY_OS_APP_DATA", raising=False)
