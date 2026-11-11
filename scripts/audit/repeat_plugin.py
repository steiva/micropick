"""Test suite audit: run each selected test N times (no pytest-repeat here).

    PYTHONPATH=scripts/audit python -m pytest -p repeat_plugin \
        --audit-repeat 30 -n 8 tests/gui/test_dish_bottom.py

Each test becomes N items ("[r0]" .. "[rN-1]"), spread over the xdist
workers like any others, so a rare failure gets a rate.
"""

import pytest


def pytest_addoption(parser):
    parser.addoption("--audit-repeat", type=int, default=1,
                     help="run each test this many times")


def pytest_generate_tests(metafunc):
    count = metafunc.config.getoption("--audit-repeat")
    if count > 1:
        metafunc.fixturenames.append("_audit_repeat")
        metafunc.parametrize("_audit_repeat", range(count),
                             ids=[f"r{i}" for i in range(count)])
