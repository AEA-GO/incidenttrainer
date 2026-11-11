import pytest
from itr_agent.verbs import VerbError, Verbs, write_env_value


def test_write_env_value_replaces_and_keeps_other_lines(tmp_path):
    env = tmp_path / "x.env"
    env.write_text("A=1\nFLAG=false\nB=2\n")
    write_env_value(env, "FLAG", "true")
    assert env.read_text() == "A=1\nFLAG=true\nB=2\n"


def test_write_env_value_appends_a_missing_key(tmp_path):
    env = tmp_path / "x.env"
    env.write_text("A=1\n")
    write_env_value(env, "NEW", "yes")
    assert env.read_text() == "A=1\nNEW=yes\n"


def test_set_flag_shows_the_command_and_recreates(world, runner):
    result = Verbs(world, runner).set_flag("catalog", "SEARCH_V2", "true")
    assert result.ok
    assert result.verb == "set_flag"
    assert result.command == (
        "sed -i 's/^SEARCH_V2=.*/SEARCH_V2=true/' config/catalog.env"
        " && docker compose up -d catalog"
    )
    assert "Recreated" in result.output
    assert runner.calls[0][0][-3:] == ["up", "-d", "catalog"]


@pytest.mark.parametrize(
    ("service", "key", "value"),
    [
        ("orders", "SEARCH_V2", "true"),  # not in this world
        ("catalog", "search_v2", "true"),  # keys are UPPER_CASE
        ("catalog", "SEARCH_V2", "true; rm -rf /"),  # no shell characters
        ("catalog", "SEARCH_V2", "$(id)"),
    ],
)
def test_bad_input_changes_nothing(world, runner, service, key, value):
    before = world.config_file("catalog").read_text()
    with pytest.raises(VerbError):
        Verbs(world, runner).set_config(service, key, value)
    assert world.config_file("catalog").read_text() == before
    assert runner.calls == []


def test_flags_are_true_or_false(world, runner):
    with pytest.raises(VerbError):
        Verbs(world, runner).set_flag("catalog", "SEARCH_V2", "maybe")


def test_long_output_is_truncated(world, runner):
    class Noisy:
        def run(self, argv, cwd, timeout_s):
            from itr_agent.verbs import CommandResult

            return CommandResult(0, "x" * 5000, 10)

    result = Verbs(world, Noisy()).set_flag("catalog", "SEARCH_V2", "false")
    assert result.truncated is True
    assert len(result.output) == 4000
