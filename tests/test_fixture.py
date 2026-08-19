from statebench.config import load_config
from statebench.upstream import build_fixture


def test_fixture_has_expected_graph_shape() -> None:
    settings = load_config("configs/final.yaml")
    fixture = build_fixture(settings["configs"][0], 0, settings)
    assert fixture.minimum_calls == 5
    assert fixture.call_budget == 10
    assert len(fixture.functions) == 5
    assert fixture.target_variable in fixture.all_values
    assert fixture.input_variables
