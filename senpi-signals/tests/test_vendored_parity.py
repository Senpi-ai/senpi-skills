"""senpi-signals carries a verbatim copy of senpi-smart-money's MCP transport, for hyperfeed.py.

Skills install standalone, so a script never reaches into another skill's folder at run time. The copy
must stay byte-identical to the original, so a fix lands in both — this test is what fails when one is
edited alone. (The signals themselves come from the Senpi backend; sweep.py makes no MCP call.)

Run: python3 -m pytest senpi-signals/tests -q
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SIGNALS = ROOT / "senpi-signals" / "scripts"
SMART_MONEY = ROOT / "senpi-smart-money" / "scripts"


@pytest.mark.parametrize("name", ["mcp_client.py"])
def test_vendored_copy_is_byte_identical(name):
    assert (SIGNALS / name).read_bytes() == (SMART_MONEY / name).read_bytes(), (
        f"senpi-signals/scripts/{name} drifted from senpi-smart-money/scripts/{name}: copy the file across")


def test_the_sweep_never_reaches_into_another_skills_folder():
    src = (SIGNALS / "sweep.py").read_text(encoding="utf-8")
    for gone in ("skill_scripts", "SENPI_SKILLS_DIR", "/data/.openclaw/skills", "senpi-smart-money/scripts"):
        assert gone not in src, gone
