from analyst_agent.modelstore import serving

SERVED = serving.Serving(cubes=13, views=["sales"], described=13)


def test_an_undeployed_source_and_an_uncompilable_model_are_not_the_same_state():
    """Both leave `serving` null, so anything reading only that has to call one of them by the
    other's name — "no model yet" for a source with thirteen cubes is wrong, not vague."""
    assert serving.state(deployed=False, ok=False, served=None) == "not deployed"
    assert serving.state(deployed=True, ok=True, served=None) == "broken"


def test_a_deployment_that_is_not_answering_is_its_own_state():
    assert serving.state(deployed=True, ok=False, served=None) == "unreachable"


def test_cube_answering_with_no_cubes_is_a_model_not_built_yet():
    """Distinct from `broken`: Cube is fine, there is simply no model on the ref."""
    empty = serving.Serving(cubes=0, views=[], described=0)
    assert serving.state(deployed=True, ok=True, served=empty) == "empty"
    assert serving.state(deployed=True, ok=True, served=SERVED) == "ok"


def test_no_state_claims_the_portal_stopped_something():
    """The portal starts nothing, so it can never observe a stop. "stopped" was a leftover from
    when it ran the containers, and it reached the browser's copy as a word for "not deployed"."""
    assert "stopped" not in serving.STATES


def test_every_state_is_declared():
    """STATES is what the browser's copy is keyed on, so a new one must be added there rather
    than falling through to undefined."""
    states = {
        serving.state(deployed=d, ok=o, served=s)
        for d in (True, False)
        for o in (True, False)
        for s in (None, SERVED, serving.Serving(cubes=0, views=[], described=0))
    }
    assert states == set(serving.STATES)
