from dgi.fsutil import atomic_replace, file_sha256, text_sha256


def test_atomic_replace_moves_source_over_destination(tmp_path):
    src, dst = tmp_path / "new", tmp_path / "live"
    dst.write_text("old")
    src.write_text("new")
    atomic_replace(src, dst)
    assert dst.read_text() == "new"
    assert not src.exists()


def test_text_sha256_is_the_known_digest():
    assert text_sha256("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_file_sha256_matches_text_sha256(tmp_path):
    path = tmp_path / "f.yaml"
    path.write_text("abc")
    assert file_sha256(path) == text_sha256("abc")
