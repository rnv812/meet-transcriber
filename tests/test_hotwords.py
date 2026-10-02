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


def test_add_term_appends_once_keeping_the_users_order():
    from meet import hotwords

    text = "# мои термины\nSIEM\nКубернетис  # старое\n"
    got, added = hotwords.add_term(text, "Kubernetes")
    assert added and got == text + "Kubernetes\n"
    assert hotwords.terms(got)[-1] == "Kubernetes"
    # Уже есть (регистр и «ё» не важны) — не дублируем.
    assert hotwords.add_term(got, "kubernetes") == (got, False)
    assert hotwords.add_term("Ёлка", "елка") == ("Ёлка", False)
    # Без перевода строки в конце — сначала он.
    assert hotwords.add_term("SIEM", "SOC") == ("SIEM\nSOC\n", True)
    assert hotwords.add_term("", "SOC") == ("SOC\n", True)


def test_remove_term_drops_only_that_line():
    from meet import hotwords

    text = "SIEM\nKubernetes\n# Kubernetes в комментарии\nSOC\n"
    assert hotwords.remove_term(text, "Kubernetes") == "SIEM\n# Kubernetes в комментарии\nSOC\n"
    assert hotwords.remove_term(text, "нет такого") == text


def test_write_and_read_roundtrip(tmp_path):
    from meet import hotwords

    path = tmp_path / "lex" / "hotwords.txt"
    assert hotwords.read(path) == ""
    hotwords.write(path, "SIEM\n")
    assert hotwords.read(path) == "SIEM\n"
