import textwrap

import pytest

from fastci import config


def write(tmp_path, name, text):
    (tmp_path / name).write_text(textwrap.dedent(text))


def test_defaults_without_any_file(tmp_path):
    c = config.load(str(tmp_path))
    assert c.source == "defaults" and c.default_branch == "main"
    assert c.tests.release_only == ["slow", "gpu", "browser", "e2e", "bench"]
    assert c.local.memory == 0.5 and c.local.workers is None and c.local.fail_fast


def test_pyproject_block(tmp_path):
    write(
        tmp_path,
        "pyproject.toml",
        """
        [tool.fastci]
        default-branch = "trunk"
        [tool.fastci.stages]
        types = "mypy src"
        docs = { run = "make docs-check", tiers = ["T1", "T2"], after = "lint" }
        [tool.fastci.tests]
        full = ["data/schema.sql"]
        global-share = 0.1
        [tool.fastci.local]
        memory = "30%"
        workers = 2
        fail-fast = false
        """,
    )
    c = config.load(str(tmp_path))
    assert c.source == "pyproject" and c.default_branch == "trunk"
    assert c.stages["types"].run == "mypy src"
    assert c.stages["docs"].tiers == frozenset({"T1", "T2"}) and c.stages["docs"].after == "lint"
    assert c.tests.full == ["data/schema.sql"] and c.tests.global_share == 0.1
    assert (c.local.memory, c.local.workers, c.local.fail_fast) == (0.3, 2, False)


def test_fastci_toml_when_pyproject_has_no_block(tmp_path):
    write(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    write(tmp_path, "fastci.toml", "default-branch = 'dev'\n")
    c = config.load(str(tmp_path))
    assert c.source == "fastci.toml" and c.default_branch == "dev"


def test_testmap_block_is_read_with_a_warning(tmp_path, capsys):
    write(
        tmp_path, "pyproject.toml", "[tool.testmap]\nalways = ['tests/t.py']\nglobal_share = 0.2\n"
    )
    c = config.load(str(tmp_path))
    assert c.source == "testmap" and c.tests.always == ["tests/t.py"]
    assert c.tests.global_share == 0.2
    assert "fastci: [tool.testmap] is deprecated" in capsys.readouterr().err


def test_unknown_key_names_it(tmp_path):
    write(tmp_path, "pyproject.toml", "[tool.fastci]\ndefualt-branch = 'x'\n")
    with pytest.raises(config.ConfigError, match="defualt-branch"):
        config.load(str(tmp_path))
