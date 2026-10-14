"""Contracts for the guest-local SIGKILL timeout measurement."""

from pathlib import Path

from scripts.run import PCM_READY_MARKER, _qemu_environment, _timeout_command


def test_timeout_command_emits_status_markers_and_guest_end_time() -> None:
    """Keep runner status names aligned and measure immediately after wait."""
    command = _timeout_command("stop")

    assert "DIRECT_RESET_STOP_KILL_RC:$kill_rc" in command
    assert "DIRECT_RESET_STOP_CHILD_EXIT_RC:$child_rc" in command
    assert "DIRECT_RESET_STOP_END_UPTIME:$ended" in command
    assert command.index('kill -KILL "$pid"') < command.index('wait "$pid"')
    assert command.index('wait "$pid"') < command.index("read -r ended idle")
    assert "DIRECT_RESET_STOP_CHILD_EXIT:$child_rc" not in command


def test_qemu_environment_drops_other_fixture_controls(monkeypatch) -> None:
    """The explicit direct-reset run is isolated from inherited fixture knobs."""
    monkeypatch.setenv("QEMU_VIRTIO_SND_CTRL_TEST", "release,1,1500")
    monkeypatch.setenv("QEMU_VIRTIO_SND_CAPTURE_TEST", "short-packed")
    monkeypatch.setenv("FSM_QEMU_BINARY", "/tmp/another-qemu")
    monkeypatch.setenv("DIRECT_RESET_TEST_PASSTHROUGH", "preserved")

    environment = _qemu_environment()

    assert not any(name.startswith("QEMU_VIRTIO_SND_") for name in environment)
    assert "FSM_QEMU_BINARY" not in environment
    assert environment["DIRECT_RESET_TEST_PASSTHROUGH"] == "preserved"


def test_guest_init_reports_the_verified_playback_node() -> None:
    """Keep the serial readiness check tied to the actual guest device node."""
    package = Path(__file__).resolve().parents[1]
    guest_init = (package / "guest/lab-init").read_text()
    runner = (package / "scripts/run.py").read_text()

    assert "test -c /dev/snd/pcmC0D0p" in guest_init
    assert f"printf '{PCM_READY_MARKER}\\n'" in guest_init
    assert "serial.wait_for(PCM_READY_MARKER, 5)" in runner
