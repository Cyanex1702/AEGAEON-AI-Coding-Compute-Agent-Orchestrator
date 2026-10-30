import ast
import json
import socket
from pathlib import Path

import pytest

from aegaeon_lite.notebook import _model_port


def test_notebook_code_cells_parse_and_have_no_embedded_outputs():
    root = Path(__file__).resolve().parents[1]
    for path in (root / "notebooks").glob("*.ipynb"):
        notebook = json.loads(path.read_text(encoding="utf-8"))
        assert notebook["nbformat"] == 4
        code = [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]
        assert len(code) >= 6
        for cell in code:
            ast.parse("".join(cell["source"]))
            assert cell["execution_count"] is None and cell["outputs"] == []


def test_model_auto_port_does_not_reuse_an_occupied_service():
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        assert _model_port(0) != port
        with pytest.raises(RuntimeError, match="already in use"):
            _model_port(port)
        # The unrelated listener is still alive.
        assert occupied.getsockname()[1] == port


def test_notebook_workers_use_the_runtime_endpoint():
    root = Path(__file__).resolve().parents[1]
    for path in (root / "notebooks").glob("*.ipynb"):
        notebook = json.loads(path.read_text(encoding="utf-8"))
        cell = next(
            "".join(c["source"])
            for c in notebook["cells"]
            if "report = run_uploaded_task" in "".join(c["source"])
        )
        tree = ast.parse(cell)
        call = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "run_uploaded_task"
        )
        provider = next(k.value for k in call.keywords if k.arg == "provider")
        endpoint = next(k.value for k in provider.keywords if k.arg == "base_url")
        assert isinstance(endpoint, ast.Subscript)
        assert endpoint.value.id == "RUNTIME" and endpoint.slice.value == "endpoint"
