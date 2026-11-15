from avk import cli, doctor


def test_parse_powercfg_ac():
    out = "    Current AC Power Setting Index: 0x00000384\n    Current DC Power Setting Index: 0x0000012c"
    assert doctor.parse_powercfg_ac(out) == 900
    assert doctor.parse_powercfg_ac("Current AC Power Setting Index: 0x00000000") == 0
    assert doctor.parse_powercfg_ac("nothing here") is None


def test_loose_acl_entries():
    tight = r"C:\avk NT AUTHORITY\SYSTEM:(OI)(CI)(F)" + "\n" + r"       PC\me:(OI)(CI)(F)"
    loose = tight + "\n" + r"       BUILTIN\Users:(OI)(CI)(RX)"
    assert doctor.loose_acl_entries(tight) == []
    assert doctor.loose_acl_entries(loose) == ["BUILTIN\\Users"]


def test_wsl_memory_cap():
    assert doctor.wsl_memory_cap("[wsl2]\nmemory=12GB\n") == "12GB"
    assert doctor.wsl_memory_cap("[wsl2]\nprocessors=4\n") is None
    assert doctor.wsl_memory_cap("[experimental]\nmemory=8GB\n") is None


def test_run_reports_missing_tool():
    code, text = doctor.run(["definitely-not-a-real-tool-xyz"])
    assert code == 127 and "not found" in text


def test_run_checks_flattens_and_survives_crash():
    def one():
        return doctor.Result(doctor.OK, "one", "")

    def many():
        return [doctor.Result(doctor.WARN, "a", ""), doctor.Result(doctor.OK, "b", "")]

    def boom():
        raise RuntimeError("bad")

    results = doctor.run_checks([one, many, boom])
    assert [r.name for r in results] == ["one", "a", "b", "boom"]
    assert results[-1].status == doctor.FAIL and "crashed" in results[-1].detail


def test_main_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "run_checks", lambda: [doctor.Result(doctor.OK, "x", "fine")])
    assert doctor.main() == 0
    monkeypatch.setattr(doctor, "run_checks", lambda: [doctor.Result(doctor.FAIL, "x", "broken")])
    assert doctor.main() == 1
    assert "broken" in capsys.readouterr().out


def test_stack_stub_logs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AVK_HOME", str(tmp_path))
    assert cli.main(["prod", "up"]) == 0
    log = (tmp_path / "logs" / "avk.log").read_text(encoding="utf-8")
    assert '"event": "prod.up"' in log


def test_validate_command_ok(capsys):
    assert cli.main(["validate"]) == 0
    assert "ok" in capsys.readouterr().out
