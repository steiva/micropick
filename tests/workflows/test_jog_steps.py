"""The jog step sizes: one list, walked one size at a time."""

from micropick.hardware.mock import MockRobot
from micropick.workflows.jog import DEFAULT_STEPS, JogController

TEN = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10]


def test_the_steps_are_the_ten_sizes_of_the_pad():
    assert list(DEFAULT_STEPS) == TEN


def test_step_up_and_down_walk_the_sizes_and_stop_at_the_ends():
    jog = JogController(MockRobot(), step=1.0)
    assert [jog.step_up() for _ in range(4)] == [2, 5, 10, 10]
    jog.step = 0.02
    assert [jog.step_down() for _ in range(2)] == [0.01, 0.01]


class _CountingRobot(MockRobot):
    """A mock whose pose reads are counted: each is a robot command (a
    savePosition, waited for), so each is a round trip a step waits on."""

    def __init__(self):
        super().__init__()
        self.reads = 0

    def get_position(self, verbose=True):
        self.reads += 1
        return super().get_position(verbose=verbose)


def _at_work_height(robot):
    robot.noise_mm = 0.0
    robot.move_to_coordinates((100.0, 100.0, 50.0), min_z_height=1,
                              verbose=False)
    robot.reads = 0


def test_a_step_reads_the_pose_once_before_the_move_and_once_after():
    robot = _CountingRobot()
    _at_work_height(robot)
    jog = JogController(robot, step=1.0)
    reads = []
    move_relative = robot.move_relative

    def counted(*args, **kwargs):
        reads.append(robot.reads)
        return move_relative(*args, **kwargs)
    robot.move_relative = counted

    result = jog.move("x", +1)
    assert result.ok
    assert reads == [1]
    assert robot.reads == 2
    assert result.position == (101.0, 100.0, 50.0)


def test_the_status_of_a_step_is_its_pose_and_reads_nothing_more():
    robot = _CountingRobot()
    _at_work_height(robot)
    jog = JogController(robot, step=1.0)
    result = jog.move("y", -1)
    robot.reads = 0
    assert jog.status(result.position).startswith(
        "(  100.00,    99.00,   50.00)")
    assert robot.reads == 0


def test_a_refused_step_reads_the_pose_once_and_moves_nothing():
    robot = _CountingRobot()
    _at_work_height(robot)
    jog = JogController(robot, step=1.0)
    jog.limits.x = (0.0, 50.0)          # already out, and going further
    result = jog.move("x", +1)
    assert not result.ok
    assert robot.reads == 1
    assert result.position == (100.0, 100.0, 50.0)
