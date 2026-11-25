"""docker-watch: starts Docker Desktop when it stopped, never twice in a row, and restarts a hung engine only after 15 minutes."""

from avk import dockerwatch


class World:
    """A fake PC: whether the engine answers, whether Docker Desktop's process exists, and what the watchdog did."""

    def __init__(self, engine=False, desktop=False, comes_up_after_start=True, desktop_exists=True):
        self.engine, self.desktop = engine, desktop
        self.comes_up_after_start, self.desktop_exists = comes_up_after_start, desktop_exists
        self.t = 1_000_000.0
        self.commands, self.starts, self.alerts = [], 0, []

    def run(self, argv, timeout):
        self.commands.append(argv[0] if argv[0] != "cmd" else "tasklist")
        if argv[:2] == ["docker", "info"]:
            return 0 if self.engine else 1
        if argv[0] == "cmd":                                   # tasklist | find
            return 0 if self.desktop else 1
        if argv[0] == "taskkill":
            self.desktop = False
        return 0

    def start(self):
        if not self.desktop_exists:
            return False
        self.starts += 1
        self.desktop = True
        self.engine = self.comes_up_after_start
        return True

    def sleep(self, seconds):
        self.t += seconds

    def now(self):
        return self.t

    def ensure(self, tmp_path, **kw):
        return dockerwatch.ensure_engine(run=self.run, start=self.start, sleep=self.sleep, now=self.now,
                                         state_path=tmp_path / "dockerwatch.json", alert=self.alerts.append, **kw)


def test_a_healthy_engine_is_left_alone(tmp_path):
    w = World(engine=True, desktop=True)
    assert w.ensure(tmp_path) == "ok" and w.starts == 0 and w.alerts == []


def test_a_stopped_docker_desktop_is_started_and_the_recovery_is_alerted(tmp_path):
    w = World(engine=False, desktop=False)
    assert w.ensure(tmp_path) == "recovered"
    assert w.starts == 1
    assert "not running, so prod is down" in w.alerts[0] and "engine is back" in w.alerts[-1]


def test_it_does_not_start_twice_within_ten_minutes(tmp_path):
    w = World(engine=False, desktop=False, comes_up_after_start=False)
    assert w.ensure(tmp_path, wait_s=30) == "failed"             # started, engine never answered
    w.desktop = False                                            # and it died again
    w.t += 120
    assert w.ensure(tmp_path, wait_s=30) == "cooldown"
    assert w.starts == 1
    w.t += 600
    assert w.ensure(tmp_path, wait_s=30) == "failed" and w.starts == 2


def test_a_running_but_silent_engine_gets_time_then_one_restart(tmp_path):
    w = World(engine=False, desktop=True)
    assert w.ensure(tmp_path) == "starting" and w.starts == 0     # first sighting: maybe just starting
    w.t += 600
    assert w.ensure(tmp_path) == "starting" and w.starts == 0     # 10 minutes: still patient
    w.t += 400                                                    # past 15 minutes
    assert w.ensure(tmp_path) == "recovered"
    assert w.starts == 1 and "taskkill" in w.commands and "wsl" in w.commands
    assert any("15 minutes" in a for a in w.alerts)


def test_the_down_timer_resets_once_the_engine_answers(tmp_path):
    w = World(engine=False, desktop=True)
    w.ensure(tmp_path)
    w.engine = True
    assert w.ensure(tmp_path) == "ok"
    w.engine = False
    w.t += 2000                                                   # a new outage must not inherit the old timer
    assert w.ensure(tmp_path) == "starting"


def test_missing_docker_desktop_is_reported_not_crashed(tmp_path):
    w = World(engine=False, desktop=False, desktop_exists=False)
    assert w.ensure(tmp_path) == "no-desktop"
    assert any("cannot start Docker Desktop" in a for a in w.alerts)
