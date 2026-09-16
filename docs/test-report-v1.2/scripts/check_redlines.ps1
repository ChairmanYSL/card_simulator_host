# Verify every test listed in test-plan-v1.2.md section 3.2 (regression red lines).
# Reads the pytest log and writes a verdict file; never appends to the file it reads.
$log = 'E:\Work\ZT\card_simulator_host\tmp\v12test\evidence\L2_pytest_full.log'
$out = 'E:\Work\ZT\card_simulator_host\tmp\v12test\evidence\L2_redlines.txt'

$redlines = @(
  'test_protocol_server.py::test_handshake_auth_and_different_device_rejected',
  'test_protocol_server.py::test_v11_protocol_version_rejected',
  'test_protocol_server.py::test_missing_protocol_version_rejected',
  'test_protocol_server.py::test_newer_protocol_version_accepted',
  'test_protocol_server.py::test_bad_frames_are_skipped_until_five',
  'test_protocol_server.py::test_close_fails_pending_ack_with_connection_closed',
  'test_runner_result.py::test_result_is_persisted_before_ack',
  'test_runner_result.py::test_persist_failure_acks_ok_false_and_does_not_resolve_waiter',
  'test_runner_result.py::test_duplicate_msg_id_is_acked_duplicate',
  'test_runner_timeout.py::test_result_timeout_fails_case_stops_run_and_skips_remainder',
  'test_runner_timeout.py::test_pos_offline_fails_case_instead_of_warning',
  'test_runner_timeout.py::test_android_error_keeps_status_error_and_stops_run',
  'test_runner_timeout.py::test_kernel_config_is_pushed_before_pos_config',
  'test_runner_timeout.py::test_case_level_kernel_params_override_brand',
  'test_loader_store.py::test_duplicate_case_id_rejected_and_first_kept',
  'test_loader_store.py::test_case_id_colliding_with_group_id_is_an_error',
  'test_loader_store.py::test_atomic_write_leaves_no_tmp_and_reads_back',
  'test_loader_store.py::test_local_kernel_params_are_loaded',
  'test_api_cases.py::test_write_path_guard_refuses_escape',
  'test_api_cases.py::test_group_payload_cannot_carry_kernel_params',
  'test_api_cases.py::test_websocket_since_garbage_is_tolerated',
  'test_launch.py::test_pid_alive_true_for_self_false_for_dead_pid'
)

$lines = Get-Content $log
$results = @()
$missing = 0
$notpassed = 0
foreach ($r in $redlines) {
  $hit = $lines | Where-Object { $_ -like "*::$($r.Split('::')[1]) *" -and $_ -like "*$($r.Split('::')[0])*" }
  if (-not $hit) {
    $results += "MISSING   $r"
    $missing++
  } elseif ($hit -match 'PASSED') {
    $results += "PASSED    $r"
  } else {
    $results += "NOT-PASS  $r  ==> $hit"
    $notpassed++
  }
}

$header = @(
  "section 3.2 red-line verification",
  "log: $log",
  "total red lines: $($redlines.Count)",
  "missing from log: $missing",
  "present but not PASSED: $notpassed",
  "verdict: $(if ($missing -eq 0 -and $notpassed -eq 0) { 'ALL RED LINES PASSED' } else { 'RED LINE FAILURE' })",
  ""
)
$header + $results | Set-Content $out
$header + $results | ForEach-Object { $_ }
