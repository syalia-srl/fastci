import re
import textwrap

import pytest

from fastci import config


def write(tmp_path, name, text):
    (tmp_path / name).write_text(textwrap.dedent(text))


def test_defaults_without_any_file(tmp_path):
    c = config.load(str(tmp_path))
    assert c.source == "defaults" and c.default_branch == "main"
    assert c.tests.release_only == ["slow", "gpu", "browser", "e2e", "bench"]
    assert c.local.memory == 0.5 and c.local.workers is None
    assert list(c.tiers) == ["local", "release", "main", "pr"]
    assert c.tiers["local"].capped and c.tiers["local"].fail_fast
    assert c.tiers["release"].marks == c.tests.release_only


def test_pyproject_block(tmp_path):
    write(
        tmp_path,
        "pyproject.toml",
        """
        [tool.fastci]
        default-branch = "trunk"
        [tool.fastci.stages]
        types = "mypy src"
        docs = { run = "make docs-check", tiers = ["pr", "main"], after = "lint" }
        [tool.fastci.tests]
        full = ["data/schema.sql"]
        global-share = 0.1
        [tool.fastci.local]
        memory = "30%"
        workers = 2
        """,
    )
    c = config.load(str(tmp_path))
    assert c.source == "pyproject" and c.default_branch == "trunk"
    assert c.stages["types"].run == "mypy src"
    assert c.stages["docs"].tiers == frozenset({"pr", "main"}) and c.stages["docs"].after == "lint"
    assert c.tests.full == ["data/schema.sql"] and c.tests.global_share == 0.1
    assert (c.local.memory, c.local.workers) == (0.3, 2)


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


def test_tier_tables_override_and_add(tmp_path):
    write(
        tmp_path,
        "pyproject.toml",
        """
        [tool.fastci.tests]
        release-only = ["slow", "e2e"]
        [tool.fastci.tiers.main]
        marks = ["slow"]
        [tool.fastci.tiers.local]
        fail-fast = false
        [tool.fastci.tiers.nightly]
        when = { event = "schedule" }
        tests = "all"
        marks = ["slow", "e2e"]
        [tool.fastci.stages]
        bench = { run = "make bench", tiers = ["nightly"] }
        """,
    )
    c = config.load(str(tmp_path))
    assert list(c.tiers) == ["nightly", "local", "release", "main", "pr"]
    assert c.tiers["nightly"].when == {"event": ["schedule"]}
    assert c.tiers["main"].marks == ["slow"] and c.tiers["main"].tests == "all"
    assert not c.tiers["local"].fail_fast and c.tiers["local"].capped
    assert c.tiers["release"].marks == ["slow", "e2e"]


@pytest.mark.parametrize(
    "text,match",
    [
        ("[tool.fastci.tiers.pr]\ntests = 'some'\n", 'tests must be "picked" or "all"'),
        ("[tool.fastci.tiers.pr]\nwhen = { branch = 'x' }\n", "unknown key 'branch'"),
        ("[tool.fastci.stages]\nx = { run = 'y', tiers = ['T1'] }\n", "unknown tiers \\['T1'\\]"),
    ],
)
def test_bad_tier_config_is_named(tmp_path, text, match):
    write(tmp_path, "pyproject.toml", text)
    with pytest.raises(config.ConfigError, match=match):
        config.load(str(tmp_path))


def load_text(tmp_path, body):
    write(tmp_path, "pyproject.toml", '[project]\nname = "x"\n' + body)
    return config.load(str(tmp_path))


def test_flaky_defaults(tmp_path):
    cfg = load_text(tmp_path, "")
    assert cfg.flaky == config.FlakyConfig()
    assert cfg.retries_for("pr") == 1


def test_flaky_table_and_tier_retries(tmp_path):
    cfg = load_text(
        tmp_path,
        """
[tool.fastci.flaky]
retries = 2
machine-threshold = 4
store = "both"
reach-share = "10%"
forgive-reached = true
[tool.fastci.tiers.release]
retries = 0
""",
    )
    assert cfg.flaky.retries == 2 and cfg.flaky.machine_threshold == 4
    assert cfg.flaky.store == "both" and cfg.flaky.reach_share == 0.1
    assert cfg.flaky.forgive_reached is True
    assert cfg.retries_for("release") == 0 and cfg.retries_for("main") == 2


@pytest.mark.parametrize(
    "body,msg",
    [
        ("[tool.fastci.flaky]\nretry = 1", "unknown key 'retry'"),
        ('[tool.fastci.flaky]\nstore = "db"', 'store must be "issues", "marks" or "both"'),
        ('[tool.fastci.flaky]\nflagged = "hide"', 'flagged must be "run" or "skip"'),
        ("[tool.fastci.flaky]\nretries = -1", "retries must be 0 or more"),
        ('[tool.fastci.flaky]\nretries = "x"', "flaky retries must be a whole number"),
        ("[tool.fastci.flaky]\nretries = 1.5", "flaky retries must be a whole number"),
        ("[tool.fastci.flaky]\nretries = true", "flaky retries must be a whole number"),
        ('[tool.fastci.tiers.pr]\nretries = "2"', "tiers.pr retries must be a whole number"),
        ("[tool.fastci.tiers.pr]\nretries = 1.5", "tiers.pr retries must be a whole number"),
        ("[tool.fastci.tiers.pr]\nretries = -1", "tiers.pr retries must be 0 or more"),
        ("[tool.fastci.flaky]\nmachine-threshold = 0", "machine-threshold must be 1 or more"),
        ("[tool.fastci.flaky]\nmachine-threshold = -2", "machine-threshold must be 1 or more"),
        ('[tool.fastci.flaky]\nmachine-threshold = "3"', "machine-threshold must be a whole"),
        ("[tool.fastci.flaky]\nunflag-after = -1", "unflag-after must be 0 or more"),
        ("[tool.fastci.flaky]\nunflag-after = 2.5", "unflag-after must be a whole number"),
        ("[tool.fastci.flaky]\nreach-share = 1.5", "reach-share must be between 0 and 1"),
        ('[tool.fastci.flaky]\nreach-share = "150%"', "reach-share must be between 0 and 1"),
        ("[tool.fastci.flaky]\nreach-share = -0.1", "reach-share must be between 0 and 1"),
        ("[tool.fastci.flaky]\nreach-share = true", "reach-share must be a share"),
        ('[tool.fastci.flaky]\nreach-share = "x%"', "reach-share in"),
        ("[tool.fastci.flaky]\nlabel = 3", "label must be a string"),
        ("[tool.fastci.flaky]\nforgive-reached = 1", "forgive-reached must be true or false"),
        ('[tool.fastci.flaky]\nmark = "slow"', "mark must not be one of fastci's own marks"),
        ('[tool.fastci.flaky]\nmark = "always"', "mark must not be one of fastci's own marks"),
    ],
)
def test_flaky_rejects(tmp_path, body, msg):
    # A ConfigError naming the key, never a TypeError or a ValueError from int().
    with pytest.raises(config.ConfigError, match=re.escape(msg)) as e:
        load_text(tmp_path, body)
    assert type(e.value) is config.ConfigError
