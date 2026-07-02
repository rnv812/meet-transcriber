from meet.tray import OUT_ROOT, _icon_image


def test_icon_image_is_drawable():
    img = _icon_image()
    assert img.size == (64, 64)


def test_out_root_is_repo_recordings():
    # ярлык может запускаться с любым cwd — путь должен быть абсолютным
    assert OUT_ROOT.is_absolute()
    assert OUT_ROOT.name == "recordings"
    assert (OUT_ROOT.parent / "pyproject.toml").exists()
