"""The robot's one lease: one holder, a refusal for the rest, no queue."""
from __future__ import annotations

import threading

from micropick.hardware.robot_lease import RobotLease


def test_a_second_claim_is_refused_while_the_first_is_held():
    lease = RobotLease()
    run = lease.claim("picking run")
    assert run is not None and lease.busy and lease.holder == "picking run"
    assert lease.claim("jog") is None
    assert lease.holder == "picking run"


def test_once_released_the_robot_can_be_claimed_again():
    lease = RobotLease()
    lease.claim("jog").release()
    assert not lease.busy and lease.holder is None
    assert lease.claim("Home") is not None


def test_a_refused_claim_is_not_kept_for_later():
    lease = RobotLease()
    run = lease.claim("liquid run")
    assert lease.claim("jog") is None
    run.release()
    assert not lease.busy                      # nothing ran in its place


def test_a_second_release_does_nothing():
    changes = []
    lease = RobotLease(on_change=changes.append)
    first = lease.claim("jog")
    first.release()
    second = lease.claim("Home")
    first.release()                            # late, from the old holder
    assert lease.holder == "Home" and not second.released
    assert changes == ["jog", None, "Home"]


def test_only_one_of_many_threads_gets_it():
    lease = RobotLease()
    got, start = [], threading.Barrier(8)

    def claim(i):
        start.wait()
        if lease.claim(f"job {i}") is not None:
            got.append(i)

    threads = [threading.Thread(target=claim, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(got) == 1


def test_a_run_says_which_plates_it_uses_until_it_ends():
    lease = RobotLease()
    run = lease.claim("picking run", run=True, plates={("5", "corning_96")})
    assert lease.uses_plate(5, "corning_96")
    assert not lease.uses_plate(5, "nest_96")
    assert not lease.uses_plate(2, "corning_96")
    run.release()
    assert not lease.uses_plate(5, "corning_96")


def test_a_short_job_uses_no_plate():
    lease = RobotLease()
    lease.claim("jog")
    assert not lease.uses_plate(5, "corning_96")
