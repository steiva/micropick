"""The shared waits of the GUI conftest (#151): a wait that runs out fails
the test there, naming what it waited for; one that is met returns at once."""

import pytest


def test_a_wait_that_runs_out_fails_naming_what_it_waited_for(until):
    with pytest.raises(pytest.fail.Exception, match="the tip lifted"):
        until(lambda: False, timeout_s=0.05, what="the tip lifted")


def test_a_wait_met_returns_on_its_first_look(until):
    seen = []
    until(lambda: seen.append(1) or True)
    assert seen == [1]
