import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_uses_ecr_non_root_and_healthcheck():
    lines = (ROOT / "Dockerfile").read_text().splitlines()
    assert any(line.startswith("FROM public.ecr.aws/") for line in lines)
    assert any(re.match(r"^USER\s+(?!root(?:\s|$)|0(?:\s|$))\S+", line) for line in lines)
    assert any(line.startswith("HEALTHCHECK ") for line in lines)


def test_compose_only_publishes_loopback_port():
    compose = (ROOT / "docker-compose.yml").read_text()
    port_mappings = re.findall(
        r"""^\s*-\s*["']([^"']+:[^"']+)["']\s*$""",
        compose,
        flags=re.MULTILINE,
    )
    assert port_mappings == ["127.0.0.1:8000:8000"]


def test_dockerignore_excludes_env():
    lines = (ROOT / ".dockerignore").read_text().splitlines()
    assert ".env" in lines
