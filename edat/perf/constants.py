"""Names, ids and numbers fixed by spec 04 (performance, energy and memory).

Every module in ``edat.perf`` takes its vocabulary from here, so a value the spec
defines appears once. Section numbers refer to ``docs/benchmark/04-perf-energy-memory-spec.md``
(revision 6).
"""
from __future__ import annotations

GIB = 1 << 30
MIB = 1 << 20

# --- schema ids --------------------------------------------------------------
PERF_SCHEMA = "edat.perf/1.0"                 # §3.1 run document
PROMPT_PACK_SCHEMA = "edat.prompt-pack/0.1"   # §2.3; layout as the iPhone runner reads it
AGENT_CORPUS_SCHEMA = "edat-agent-corpus-v1"  # 02 §11.1
AGENT_TRAJECTORY_SCHEMA = "edat-agent-trajectory-v1"  # 02 §11.2
SIM_DAY_SCHEMA = "edat-sim-day-v1"            # 02 §11.4
EXPORT_MANIFEST_SCHEMA = "edat.export-manifest/1"  # iPhone export manifest (10 §8.3)
POWER_SAMPLE_SCHEMA = "edat.power-samples/1"  # this runner's power-sample JSONL lines
CLOCK = "mach_continuous_time"                # §2.5, §3.1
RUNNER_NAME = "mac-omlx"                      # §3.1
RUNNER_VERSION = "0.1.0"

# --- context levels (§6.1, D9) -----------------------------------------------
LEVELS = (512, 1024, 2048, 5120, 10240, 32768, 65536)
SMALL_LEVEL = 2048        # S, §5.5
WORKING_LEVEL = 10240     # W, §5.5
HEADLINE_ENERGY_LEVEL = 2048  # §4.1
LONG_LEVELS = (32768, 65536)
NONCE_VARIANTS = 16       # §2.3
FIRST_TOKEN_PROMPT_TOKENS = 8  # §2.3, §7
OUTPUT_TOKENS = 256       # §2.4
REPLAY_STEPS = 6          # 02 §11.2
REPLAY_PREFIX_TOKENS = 4096  # 02 §11.2

# --- repetitions and order (§2.6, §6.4) -------------------------------------
WARMUP_REPS = 1
MEASURED_REPS = 10        # levels up to 10,240
MEASURED_REPS_LONG = 5    # 32,768 and 65,536
LOAD_WARM_REPS = 5        # §7 (Mac: 5 cold and 5 warm, alternating)
LOAD_COLD_REPS_MAC = 5
AGENT_REPLAY_REPS = 5
COOLDOWN_MAX_S = 300.0    # §2.6
GAP_AFTER_SAMPLE_S = 5.0  # §2.6
REQUEST_TIMEOUT_S = 600.0  # §5.3 `timeout`
REFERENCE_PAIR_LEVELS = (512, 2048, 10240)  # §1.5 per-session base levels
REFERENCE_PAIR_REPS = 10

# --- energy (§4) ---------------------------------------------------------------
ENERGY_PAIR_K = 20            # §4.2
ENERGY_PAIR_K_LONG = 5        # §4.2 "cut K to 5 there and above 10k"
ENERGY_PAIR_K_LONG_ABOVE = 10240  # levels strictly above this use K_LONG
SHAPE_A_TOKENS = 1
SHAPE_B_TOKENS = 257
SHAPE_C_TOKENS = 65
SHAPE_C_RESIDUAL_FLAG = 0.05
IDLE_WINDOW_S = 60.0          # §4.3
IDLE_DRIFT_FLAG = 0.15        # §4.3
PSTR_SOURCE_ERROR = 0.01      # §4.3
PSTR_RATE_HZ = 20.0
IDLE_RATE_HZ = 1.0            # §9.1 idle stretches
BATTERY_CROSSCHECK_FLAG = 0.03  # §4.3

ENERGY_SOURCES = ("smc_pstr", "ioreport_gpu", "battery_accumulator", "powermetrics", "external_meter")
ENERGY_SCOPES = ("whole_device_internal", "whole_device_external", "gpu_rail", "soc_rails",
                 "whole_device_battery_drain")
SOURCE_SCOPE = {"smc_pstr": "whole_device_internal", "ioreport_gpu": "gpu_rail",
                "battery_accumulator": "whole_device_internal", "powermetrics": "soc_rails",
                "external_meter": "whole_device_external"}

# --- memory (§5) ---------------------------------------------------------------
FOOTPRINT_RATE_HZ = 20.0       # §2.5 / §5.1
MEMORY_PRESSURE_BYTES = 256 * MIB  # §5.3
KV_FORMULA_FLAG = 0.25         # §5.2

# --- 16 GB fit (§5.5–§5.7) -------------------------------------------------------
B16_BYTES = 10 * GIB           # `edat.fit16.budget_bytes`
B16_SOURCE = "derived-v1"
FIT_MARGIN = 0.10              # fits needs peak <= 0.9 x B16
BALLOON_GIB = 10.5             # §5.5 emulation
BALLOON_UNDER_LOAD_GIB = 8.0   # §5.6
APP_LOAD_GIB = 2.5             # §5.6 L_apps
APP_LOAD_HOT_GIB = 1.5
APP_LOAD_COLD_GIB = 1.0
PRESSURE_SWEEP_GIB = (2.5, 4.0, 5.5)
OMLX_CEILING_16GB_GB = 13      # §5.5 step 3
EMULATION_REPS = 3
EMULATION_SLOWDOWN_LIMIT = 0.10
# The 16 GB default iogpu.wired_limit_mb is unverified (§5.5, §16). One constant, as §11.1 asks.
EDAT_16GB_WIRED_MB = 10900
AUX_ALLOWANCE_BYTES = int(0.4 * GIB)  # §5.5 helper-model allowance, §5.7 rule 5

# --- sustained (§9) --------------------------------------------------------------
S1_MINUTES_MAC = 15
S1_INITIAL_MIN = 2
S1_SUSTAINED_LAST_MIN = 5
THROTTLE_FRACTION = 0.90
S2_MINUTES = 60
S2_BURST_EVERY_S = 180
S3_MINUTES = 30
SIM_DAY_GAP_S = 30.0           # §9.1 step 1
SIM_DAY_IDLE_WINDOW_S = 30 * 60.0
SIM_DAY_VALIDATION_TOLERANCE = 0.10
IDLE_UNLOAD_MINUTES = 5        # Harbor default (§9.1, §9.3)
RELOAD_REPS = 5
LONG_SESSION_MIN_TRIMS = 10
LEAK_FLAG_FRACTION = 0.05
TIME_BUDGET_S = {"question": 10.0, "one_tool": 20.0, "multi_step": 60.0, "digest": 120.0,
                 "brief": 30.0}   # §9.4 (analyst's proposal)
# Additive: screen-control tasks by level (11 §3.10, that spec's proposal) and sidekick tasks
# (12 SK-25 uses §9.4's question and multi-step budgets).
TIME_BUDGET_SCREEN_S = {"L1": 15.0, "L2": 30.0, "L3": 60.0, "L4": 120.0, "L5": 180.0}
TIME_BUDGET_SIDEKICK_S = {"answer_only": 10.0, "tool_mode": 60.0}

# --- reference models (§1.2, §1.5) ---------------------------------------------
BASELINE_ID = "Gemma-Wisp"
REFERENCE_ID = "gemma-4-E4B-it-oQ4e-mtp"
BASELINE_DRIFT_LIMIT = 0.05    # §1.3
MODES = ("as_shipped", "plain")
ROLES = ("baseline", "reference", "candidate", "variant", "cloud_reference")

# --- enums (§3.6, §3.7, §3.12) ---------------------------------------------------
TESTS = ("level", "energy_pair", "load_warm", "load_cold", "first_token", "agent_replay",
         "sustained", "battery", "idle_resident", "wake",
         "fit16_emulation",                                   # §3.10
         "cloud_level", "cloud_task",                         # §3.11
         "sim_day_event", "sim_day_idle", "sim_day_realtime", "long_session_turn",
         "reload_after_idle", "fit16_under_load", "pressure_sweep", "aux_baseline",  # §3.12
         "screen_task", "sidekick_task")   # additive: timed tasks from specs 11 and 12
PHASES = ("warmup", "measured")
STATUSES = ("ok", "oom_killed", "oom_killed_at_load", "killed_unknown", "not_run_above_oom",
            "exceeds_native_window", "ok_extended", "engine_refused", "memory_pressure", "truncated",
            "timeout", "unsupported_by_runtime", "voided_backgrounded", "voided_foreign_work", "voided_not_on_battery",
            "provider_error", "rate_limited", "voided_response_cache", "provider_fallback")
# Statuses whose numbers count as measurements (kept, possibly flagged).
OK_STATUSES = ("ok", "ok_extended", "memory_pressure")
# Worst-first order used when a cell's status is "the worst over its samples" (§3.9).
STATUS_SEVERITY = ("oom_killed_at_load", "oom_killed", "killed_unknown", "engine_refused",
                   "unsupported_by_runtime", "exceeds_native_window", "not_run_above_oom", "timeout",
                   "truncated", "voided_foreign_work", "voided_backgrounded", "voided_not_on_battery", "voided_response_cache",
                   "provider_error", "rate_limited", "memory_pressure", "provider_fallback",
                   "ok_extended", "ok")
EVENT_KINDS = ("thermal_change", "memory_warning", "relaunch_after_kill", "cooldown_timeout",
               "operator_note", "foreign_request", "metrickit_exit",
               # additive, this runner: trims and unloads seen in long-running tests
               "trim", "engine_unload", "admin_action", "task_start", "task_end")
THERMAL_STATES = ("nominal", "fair", "serious", "critical")
SIM_DAY_EVENT_KINDS = ("brief", "question", "action", "digest", "idle")

# Ports the runner must never talk to (Harbor's backend, its gateway, the user's own oMLX).
FORBIDDEN_PORTS = (8000, 8765, 8791)
