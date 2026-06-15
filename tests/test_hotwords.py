from meet.transcribe import _load_hotwords


def test_reads_file_skips_comments_and_blanks(tmp_path):
    f = tmp_path / "hotwords.txt"
    f.write_text("# комментарий\nМандалорцы\n\nФиксики  # хвостовой коммент\n", "utf-8")
    assert _load_hotwords(None, f) == "Мандалорцы, Фиксики"


def test_merges_cli_extra_and_dedups(tmp_path):
    f = tmp_path / "hotwords.txt"
    f.write_text("Мандалорцы\nФиксики\n", "utf-8")
    assert _load_hotwords("CCS, Фиксики", f) == "Мандалорцы, Фиксики, CCS"


def test_missing_file_uses_only_cli(tmp_path):
    assert _load_hotwords("CCS, hotfix", tmp_path / "нет.txt") == "CCS, hotfix"


def test_nothing_returns_none(tmp_path):
    assert _load_hotwords(None, tmp_path / "нет.txt") is None
