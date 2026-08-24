from app.pipeline_state import PipelineRunState, RunStatus


def test_reset_for_new_run_sets_running_and_clears_fields():
    state = PipelineRunState()
    state.discovered = 5
    state.logs = ["stale"]

    state.reset_for_new_run()

    assert state.status == RunStatus.RUNNING
    assert state.started_at is not None
    assert state.discovered == 0
    assert state.logs == []
    assert state.error is None


def test_log_appends_timestamped_entry_and_updates_current_step():
    state = PipelineRunState()
    state.log("Doing a thing")

    assert len(state.logs) == 1
    assert state.logs[0].endswith("Doing a thing")
    assert state.current_step == "Doing a thing"


def test_log_caps_history_at_max_logs():
    state = PipelineRunState()
    state.MAX_LOGS = 3
    for i in range(5):
        state.log(f"line {i}")

    assert len(state.logs) == 3
    assert state.logs[-1].endswith("line 4")


def test_finish_without_error_sets_done():
    state = PipelineRunState()
    state.reset_for_new_run()
    state.finish()

    assert state.status == RunStatus.DONE
    assert state.error is None
    assert state.finished_at is not None


def test_finish_with_error_sets_error_status():
    state = PipelineRunState()
    state.reset_for_new_run()
    state.finish(error="boom")

    assert state.status == RunStatus.ERROR
    assert state.error == "boom"


def test_snapshot_is_a_copy_not_a_live_reference():
    state = PipelineRunState()
    state.log("first")
    snap = state.snapshot()
    state.log("second")

    assert len(snap["logs"]) == 1


def test_request_stop_sets_flag_only_while_running():
    state = PipelineRunState()
    state.reset_for_new_run()

    state.request_stop()

    assert state.should_stop() is True
    assert state.stop_requested is True


def test_request_stop_is_a_noop_when_not_running():
    state = PipelineRunState()  # status defaults to IDLE

    state.request_stop()

    assert state.should_stop() is False


def test_reset_for_new_run_clears_a_previous_stop_request():
    state = PipelineRunState()
    state.reset_for_new_run()
    state.request_stop()

    state.reset_for_new_run()

    assert state.should_stop() is False


def test_finish_stopped_sets_stopped_status():
    state = PipelineRunState()
    state.reset_for_new_run()
    state.request_stop()

    state.finish(stopped=True)

    assert state.status == RunStatus.STOPPED
    assert state.current_step == "Stopped"
