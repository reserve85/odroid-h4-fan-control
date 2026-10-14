"""Temperature readers, part 2: NVMe fallback/caching, drive smartctl."""

import fan_control


def test_read_nvme_hwmon_fallback(monkeypatch, tmp_path):
    hw = tmp_path / "hwmon1"
    hw.mkdir()
    (hw / "name").write_text("nvme\n")
    (hw / "temp1_input").write_text("41000\n")

    def fake_glob(pattern):
        if pattern == "/dev/nvme*n1":
            return []
        if pattern == "/sys/class/hwmon/hwmon*":
            return [str(hw)]
        if pattern.endswith("temp*_input"):
            return [str(hw / "temp1_input")]
        return []

    monkeypatch.setattr(fan_control.glob, "glob", fake_glob)

    def fail(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(fan_control.subprocess, "check_output", fail)
    assert fan_control.read_nvme_temp() == 41


def test_read_nvme_all_missing_returns_none(monkeypatch):
    monkeypatch.setattr(fan_control.glob, "glob", lambda pattern: [])
    assert fan_control.read_nvme_temp() is None


def test_read_nvme_negative_result_cached(monkeypatch):
    monkeypatch.setattr(fan_control.glob, "glob", lambda pattern: [])
    assert fan_control.read_nvme_temp() is None
    assert fan_control._missing_path_cache.get("nvme_devices") is True
    # a second call still short-circuits the /dev/nvme probe
    assert fan_control.read_nvme_temp() is None


def test_read_drive_smartctl(monkeypatch):
    def fake_glob(pattern):
        if pattern == "/dev/sd[a-z]":
            return ["/dev/sda"]
        return []

    monkeypatch.setattr(fan_control.glob, "glob", fake_glob)

    def fake_check_output(cmd, **kwargs):
        assert cmd[:2] == ["smartctl", "-A"]
        assert kwargs.get("timeout") == fan_control.SUBPROCESS_TIMEOUT
        return (
            "194 Temperature_Celsius     0x0022   100   100   000    "
            "Old_age   Always       -       38\n"
        )

    monkeypatch.setattr(fan_control.subprocess, "check_output", fake_check_output)
    assert fan_control.read_drive_temp() == 38


def test_read_drive_smartctl_missing_returns_none(monkeypatch):
    def fake_glob(pattern):
        if pattern == "/dev/sd[a-z]":
            return ["/dev/sda"]
        return []

    monkeypatch.setattr(fan_control.glob, "glob", fake_glob)

    def fail(cmd, **kwargs):
        raise FileNotFoundError("smartctl not installed")

    monkeypatch.setattr(fan_control.subprocess, "check_output", fail)
    assert fan_control.read_drive_temp() is None
    assert fan_control._missing_path_cache.get("smartctl") is True


def test_read_drive_no_devices_returns_none(monkeypatch):
    monkeypatch.setattr(fan_control.glob, "glob", lambda pattern: [])
    assert fan_control.read_drive_temp() is None
    assert fan_control._missing_path_cache.get("sd_devices") is True