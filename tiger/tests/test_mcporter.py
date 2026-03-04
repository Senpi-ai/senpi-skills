"""
Tests for mcporter_call and mcporter_call_safe — core MCP I/O primitive.
Verifies retry logic, temp-file handling, envelope stripping, timeout,
and the safe wrapper's error-dict return.
"""

import json
import os
import pytest

import tiger_config


class _FakeProc:
    """Minimal subprocess.Popen stand-in for mcporter_call tests."""

    def __init__(self, stdout_data=None, returncode=0, stderr="", raise_timeout=False):
        self._stdout_data = stdout_data
        self.returncode = returncode
        self._stderr = stderr
        self._raise_timeout = raise_timeout

    def communicate(self, timeout=None):
        if self._raise_timeout:
            import subprocess
            raise subprocess.TimeoutExpired(cmd="mcporter", timeout=timeout)
        return None, self._stderr

    def kill(self):
        pass

    def wait(self):
        pass


def _make_runner(stdout_data, returncode=0, stderr="", raise_timeout=False):
    """Return a runner function that writes stdout_data to the temp file."""

    def runner(cmd, stdout=None, stderr=None, text=True):
        proc = _FakeProc(
            stdout_data=stdout_data,
            returncode=returncode,
            stderr=stderr if isinstance(stderr, str) else "",
            raise_timeout=raise_timeout,
        )
        # Write data to the temp file (stdout is an open file handle)
        if stdout is not None and stdout_data is not None:
            stdout.write(json.dumps(stdout_data))
            stdout.flush()
        return proc

    return runner


def _noop_sleep(seconds):
    """No-op sleep for fast tests."""
    pass


class TestMcporterCall:
    """Tests for the raising mcporter_call variant."""

    def test_success_returns_data_directly(self):
        """Raw dict without envelope is returned as-is."""
        data = {"candles": {"1h": [1, 2, 3]}}
        result = tiger_config.mcporter_call(
            "get_candles",
            runner=_make_runner(data),
            sleep_fn=_noop_sleep,
            asset="ETH",
        )
        assert result == {"candles": {"1h": [1, 2, 3]}}

    def test_envelope_stripped(self):
        """Success envelope {success, data} is stripped — inner data returned."""
        inner = {"instruments": [{"name": "BTC"}]}
        envelope = {"success": True, "data": inner}
        result = tiger_config.mcporter_call(
            "market_list_instruments",
            runner=_make_runner(envelope),
            sleep_fn=_noop_sleep,
        )
        assert result == inner

    def test_list_response_returned_as_is(self):
        """Non-dict responses (e.g., JSON arrays) pass through unchanged."""
        data = [1, 2, 3]
        result = tiger_config.mcporter_call(
            "some_tool",
            runner=_make_runner(data),
            sleep_fn=_noop_sleep,
        )
        assert result == [1, 2, 3]

    def test_success_false_retries_and_raises(self):
        """{success: false} triggers retry and eventually raises."""
        data = {"success": False, "error": "rate limited"}
        with pytest.raises(RuntimeError, match="failed after 3 attempts"):
            tiger_config.mcporter_call(
                "some_tool",
                runner=_make_runner(data),
                sleep_fn=_noop_sleep,
            )

    def test_nonzero_returncode_retries_and_raises(self):
        """Non-zero exit code triggers retry and eventually raises."""
        with pytest.raises(RuntimeError, match="failed after 3 attempts"):
            tiger_config.mcporter_call(
                "some_tool",
                runner=_make_runner(None, returncode=1, stderr="connection refused"),
                sleep_fn=_noop_sleep,
            )

    def test_timeout_retries_and_raises(self):
        """Subprocess timeout triggers retry and eventually raises."""
        with pytest.raises(RuntimeError, match="failed after 3 attempts"):
            tiger_config.mcporter_call(
                "some_tool",
                runner=_make_runner(None, raise_timeout=True),
                sleep_fn=_noop_sleep,
                timeout_seconds=1,
            )

    def test_retries_three_times_before_raising(self):
        """Verify exactly 3 attempts are made."""
        attempt_count = 0

        def counting_runner(cmd, stdout=None, stderr=None, text=True):
            nonlocal attempt_count
            attempt_count += 1
            return _FakeProc(returncode=1, stderr="fail")

        with pytest.raises(RuntimeError):
            tiger_config.mcporter_call(
                "some_tool",
                runner=counting_runner,
                sleep_fn=_noop_sleep,
            )
        assert attempt_count == 3

    def test_sleep_called_between_retries(self):
        """Sleep is called with 3s between retry attempts (not after last)."""
        sleeps = []

        def tracking_sleep(seconds):
            sleeps.append(seconds)

        with pytest.raises(RuntimeError):
            tiger_config.mcporter_call(
                "some_tool",
                runner=_make_runner(None, returncode=1, stderr="fail"),
                sleep_fn=tracking_sleep,
            )
        # 3 attempts → 2 sleeps (between attempt 1→2 and 2→3)
        assert sleeps == [3, 3]

    def test_temp_file_cleaned_up_on_success(self, tmp_path):
        """Temp file is removed after successful call."""
        created_files = []
        original_mkstemp = __import__("tempfile").mkstemp

        def tracking_mkstemp(**kwargs):
            fd, path = original_mkstemp(**kwargs)
            created_files.append(path)
            return fd, path

        import tempfile
        original = tempfile.mkstemp
        tempfile.mkstemp = tracking_mkstemp
        try:
            tiger_config.mcporter_call(
                "some_tool",
                runner=_make_runner({"result": "ok"}),
                sleep_fn=_noop_sleep,
            )
        finally:
            tempfile.mkstemp = original

        # All temp files should be cleaned up
        for f in created_files:
            assert not os.path.exists(f), f"Temp file {f} was not cleaned up"

    def test_temp_file_cleaned_up_on_failure(self):
        """Temp file is removed even when all attempts fail."""
        created_files = []
        original_mkstemp = __import__("tempfile").mkstemp

        def tracking_mkstemp(**kwargs):
            fd, path = original_mkstemp(**kwargs)
            created_files.append(path)
            return fd, path

        import tempfile
        original = tempfile.mkstemp
        tempfile.mkstemp = tracking_mkstemp
        try:
            with pytest.raises(RuntimeError):
                tiger_config.mcporter_call(
                    "some_tool",
                    runner=_make_runner(None, returncode=1, stderr="fail"),
                    sleep_fn=_noop_sleep,
                )
        finally:
            tempfile.mkstemp = original

        assert len(created_files) == 3  # One per attempt
        for f in created_files:
            assert not os.path.exists(f), f"Temp file {f} was not cleaned up"

    def test_cmd_builds_correctly(self):
        """Verify command construction with various kwarg types."""
        captured_cmd = []

        def capturing_runner(cmd, stdout=None, stderr=None, text=True):
            captured_cmd.extend(cmd)
            if stdout is not None:
                stdout.write(json.dumps({"ok": True}))
                stdout.flush()
            return _FakeProc()

        tiger_config.mcporter_call(
            "get_candles",
            runner=capturing_runner,
            sleep_fn=_noop_sleep,
            asset="ETH",
            intervals=["1h", "4h"],
            include_funding=True,
        )
        assert captured_cmd[0] == "mcporter"
        assert captured_cmd[1] == "call"
        assert captured_cmd[2] == "senpi.get_candles"
        assert "asset=ETH" in captured_cmd
        assert 'intervals=["1h", "4h"]' in captured_cmd
        assert "include_funding=true" in captured_cmd


class TestMcporterCallSafe:
    """Tests for the non-raising mcporter_call_safe wrapper."""

    def test_success_passes_through(self):
        """Successful calls return data directly."""
        data = {"instruments": []}
        result = tiger_config.mcporter_call_safe(
            "some_tool",
            runner=_make_runner(data),
            sleep_fn=_noop_sleep,
        )
        assert result == {"instruments": []}

    def test_failure_returns_error_dict(self):
        """Failed calls return {error: str} instead of raising."""
        result = tiger_config.mcporter_call_safe(
            "some_tool",
            runner=_make_runner(None, returncode=1, stderr="timeout"),
            sleep_fn=_noop_sleep,
        )
        assert "error" in result
        assert "failed after 3 attempts" in result["error"]

    def test_envelope_stripped_through_safe(self):
        """Envelope stripping works through the safe wrapper too."""
        envelope = {"success": True, "data": {"prices": {"BTC": "67000"}}}
        result = tiger_config.mcporter_call_safe(
            "get_prices",
            runner=_make_runner(envelope),
            sleep_fn=_noop_sleep,
        )
        assert result == {"prices": {"BTC": "67000"}}
