from aegaeon_lite.session import Session


def session_for(root):
    return Session(
        root,
        {
            "request": "Edit a source file",
            "mode": "fix",
            "allowed_paths": ["code.py"],
            "checks": [],
        },
        None,
    )


def test_read_content_can_be_used_as_an_exact_multiline_crlf_edit(tmp_path):
    data = b"def add(a, b):\r\n    return a - b\r\n"
    path = tmp_path / "code.py"
    path.write_bytes(data)
    session = session_for(tmp_path)
    read = session.tool({"action": "read", "path": "code.py"})
    assert read["content"] == data.decode()
    assert read["start_line"] == 1 and read["end_line"] == 2
    session.tool(
        {
            "action": "edit",
            "path": "code.py",
            "expected_sha256": read["sha256"],
            "old": read["content"],
            "new": read["content"].replace("a - b", "a + b"),
        }
    )
    assert path.read_bytes() == data.replace(b"a - b", b"a + b")


def test_existing_empty_source_file_can_be_filled_after_a_fresh_read(tmp_path):
    path = tmp_path / "code.py"
    path.write_bytes(b"")
    session = session_for(tmp_path)
    read = session.tool({"action": "read", "path": "code.py"})
    assert read["content"] == ""
    session.tool(
        {
            "action": "edit",
            "path": "code.py",
            "expected_sha256": read["sha256"],
            "old": "",
            "new": "VALUE = 7\n",
        }
    )
    assert path.read_bytes() == b"VALUE = 7\n"
