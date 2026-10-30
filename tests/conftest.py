import pytest

from aegaeon_lite.bundles import initialize


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "canonical"
    root.mkdir()
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_calc.py").write_text(
        "import unittest\nfrom calc import add\n"
        "class Tests(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n",
        encoding="utf-8",
    )
    (root / ".env").write_text("TOKEN=must-not-export")
    initialize(root)
    return root


@pytest.fixture
def task():
    return {
        "request": "Fix add so it adds numbers. Keep existing tests.",
        "mode": "fix",
        "allowed_paths": ["calc.py"],
        "checks": [
            {
                "id": "regression",
                "argv": ["python", "-m", "unittest", "discover", "-s", "tests"],
                "cwd": ".",
                "timeout": 10,
            }
        ],
    }
