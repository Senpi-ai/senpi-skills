"""`deploy.py where` prints the durable strategies root — the one place a fork may be written."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEPLOY = os.path.join(HERE, "..", "scripts", "deploy.py")


def test_where_prints_the_env_root_verbatim(tmp_path):
    env = dict(os.environ, SENPI_STRATEGIES_DIR=str(tmp_path))
    out = subprocess.run([sys.executable, DEPLOY, "where"], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == str(tmp_path)


def test_where_falls_back_to_the_workspace_root(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "SENPI_STRATEGIES_DIR"}
    env["OPENCLAW_WORKSPACE_DIR"] = str(tmp_path)
    out = subprocess.run([sys.executable, DEPLOY, "where"], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == os.path.join(str(tmp_path), "strategies")
