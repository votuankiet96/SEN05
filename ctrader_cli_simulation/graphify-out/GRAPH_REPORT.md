# Graph Report - Robots  (2026-10-10)

## Corpus Check
- 78 files · ~165,517 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 11 file(s) not represented in the graph (top: (none) 7, .algo 3, .bat 1)

## Summary
- 1783 nodes · 4006 edges · 121 communities (74 shown, 47 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 143 edges (avg confidence: 0.92)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Optimize Backend Tests
- Dashboard Session Store
- Single Backtest Views
- Single Callback Wiring
- Core Run Lifecycle
- Grid Search Views
- MA Cross cBot
- Result Data Access
- DSR PBO Views
- MACD Break cBot
- Backtest Settings Mapper
- Redis Signal Materialization
- Statistical Readout Pipeline
- Grid Session Actions
- Walk Forward Views
- Trial Selection Logic
- Plugin Backtest Runner
- CLI Optimize Execution
- Core Time Models
- Experiment Planning
- Experiment Result Store
- CLI Binary Selection
- Strategy Configuration
- Engine Profile Configuration
- API Bridge Client
- Shared cTrader References
- CLI Result Classification
- Combo cBot Lifecycle
- API Runner Results
- Walk Forward Orchestration
- Experiment Facilitator
- Pipeline Stage Locks
- Grid Callback Progress
- Trial Eligibility Tests
- Backtest Job Contract
- Diagnostics Callbacks
- Backtest Result Contract
- Walk Forward Cache Tests
- Plugin Deployment Tools
- Evidence Parsing
- Redis Profile Tests
- Deflated Sharpe Ratio
- Dashboard Composition Tests
- Store Claim Ownership
- Live Dashboard Contracts
- Monte Carlo Engine
- Plugin Capability Contracts
- Runtime Diagnostics Contract
- PBO Computation
- Diagnostics Source Actions
- CLI Reliability Research
- Grid Audit Tests
- Diagnostics Planning Tests
- Walk Forward Data Access
- Protocol Budget Store
- Dashboard Resource Groups
- MA Cross Signal Loading
- MACD Signal Loading
- Report Validation
- Run Specification Model
- Signal Path Resolution
- Single Control Tests
- Combo Stop Loss Levels
- Combo Take Profit Levels
- MA Cross Stop Loss
- MA Cross Take Profit
- MACD Stop Loss
- MACD Take Profit
- API Redis Configuration
- Workflow Session Launch
- Evidence Gate Tests
- Method Audit Tests
- Combo Order Placement
- Repository Research Rules
- Dashboard Filter Options
- CLI Capability Tests
- Monte Carlo OOS Tests
- Research Provenance
- Form Input Preparation
- Dashboard Layout Components
- Bootstrap Method Research
- API Validation Tests
- CLI Version Tests
- Objective Readout Tests
- DSR Formula Tests
- PBO Objective Tests
- PBO Algorithm Tests
- DSR PBO Research
- Full Validation Research
- Pipeline Lock Tests
- Monte Carlo Store Tests
- Combo Signal Row
- Plugin Job Context
- CLI Argument Builder
- Authentication Failure Tests
- Signal Hash Risk Tests
- Pipeline Cache Tests
- Walk Forward Readout
- API JSON Contracts
- Plugin Project Configuration
- DSR Selected Trial
- Stale Claim Recovery
- Pipeline Registry
- CLI Optimization Research
- Experiment Lock Tests
- Combo Project Configuration
- MA Cross Project
- MACD Project
- Readout API Tests
- Grid Lock Policy

## God Nodes (most connected - your core abstractions)
1. `RunSpec` - 68 edges
2. `Combo` - 48 edges
3. `MACross` - 43 edges
4. `MACDBreak` - 43 edges
5. `ExperimentStore` - 41 edges
6. `load_strategy_profile()` - 37 edges
7. `RunStatus` - 34 edges
8. `run_experiment()` - 32 edges
9. `BacktestJob` - 30 edges
10. `run_optimize_box()` - 29 edges

## Surprising Connections (you probably didn't know these)
- `Preregistered Experiment Contract` --semantically_similar_to--> `Five-Stage Validation Pipeline`  [INFERRED] [semantically similar]
  bo_workflow/research_notes/phase-2-research-and-optimization-design.md → bo_workflow/research_notes/full-validation-pipeline-design.md
- `cBot Redis Signal-Source Feasibility Study` --references--> `Repository Design and Coding Rules`  [EXTRACTED]
  bo_workflow/research_notes/cbot-network-access-and-redis-signal-source-feasibility.md → CLAUDE.md
- `validate_capabilities()` --uses--> `RunSpec`  [INFERRED]
  bo_workflow/api_engine/api_runner.py → bo_workflow/core_engine/models.py
- `run_backtest()` --uses--> `RunSpec`  [INFERRED]
  bo_workflow/api_engine/api_runner.py → bo_workflow/core_engine/models.py
- `_classify()` --uses--> `BacktestApiResult`  [INFERRED]
  bo_workflow/api_engine/api_runner.py → bo_workflow/api_engine/contracts.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **cTrader Backend Execution Equivalence** — bo_workflow_research_notes_ab_plugin_api_vs_cli_2026_09_11_plugin_api_equals_cli_engine, bo_workflow_research_notes_ctrader_backtest_api_plugin_optimize_record_2026_09_11_executor_backend_architecture, bo_workflow_api_engine_readme_file_mailbox_bridge [EXTRACTED 1.00]
- **Full Strategy Validation Flow** — bo_workflow_research_notes_full_validation_pipeline_design_five_stage_validation_pipeline, bo_workflow_research_notes_time_series_strategy_optimization_methodology_walk_forward_optimization, bo_workflow_research_notes_dsr_pbo_formula_verification_deflated_sharpe_ratio, bo_workflow_research_notes_dsr_pbo_formula_verification_cscv_pbo, bo_workflow_research_notes_montecarlo_equity_vs_balance_daily_return_methodology_dual_series_monte_carlo [EXTRACTED 1.00]
- **CLI Tick Reliability Controls** — bo_workflow_research_notes_cli_tick_bottleneck_diagnosis_2026_09_10_three_tick_execution_modes, bo_workflow_research_notes_cli_tick_bottleneck_diagnosis_2026_09_10_conversion_symbol_tick_hang, bo_workflow_research_notes_ctrader_cli_practical_issues_catalog_2026_09_10_cli_operational_safety_controls, bo_workflow_research_notes_ctrader_cli_concurrent_backtest_analysis_bounded_parallelism [INFERRED 0.85]

## Communities (121 total, 47 thin omitted)

### Community 0 - "Optimize Backend Tests"
Cohesion: 0.06
Nodes (16): OptimizeBox, _box(), _config(), _epoch_ms(), FacilitatorBoxTests, run_optimize_box(), short(), _fake_result() (+8 more)

### Community 1 - "Dashboard Session Store"
Cohesion: 0.07
Nodes (41): active_session(), ActiveSessionError, cancel_analysis(), claim_run(), complete(), confirmed_signal_profile(), create_draft_group(), create_grid_diagnostics() (+33 more)

### Community 2 - "Single Backtest Views"
Cohesion: 0.09
Nodes (33): refresh_selected_run_status(), refresh_session_detail(), active_run_summary(), bot_activity_rows(), _bot_activity_table(), _bot_event_time(), _cell(), _chart_time() (+25 more)

### Community 3 - "Single Callback Wiring"
Cohesion: 0.08
Nodes (13): save_signal_profile(), refresh_status(), refresh_grid_status(), _truncate(), _filtered_rows(), page_bot_activity(), _selected_sessions(), _sorted_rows() (+5 more)

### Community 4 - "Core Run Lifecycle"
Cohesion: 0.09
Nodes (12): RunResult, DailyCliBudgetTests, run_backtest(), _fake_result(), _fake_run_result(), _record_report(), RunExperimentLifecycleTests, run_backtest() (+4 more)

### Community 5 - "Grid Search Views"
Cohesion: 0.13
Nodes (26): _configuration(), detail(), _heatmap(), heatmap_figure(), gap_text(), layout(), _leaderboard(), leaderboard_table() (+18 more)

### Community 6 - "MA Cross cBot"
Cohesion: 0.10
Nodes (11): MACross, EnableDailyLossLimit, EnableMaxConsecutiveLosses, EnableMaxDrawdown, KslLevel, KtpLevel, MaxConsecutiveLosses, MaxDailyLossPercent (+3 more)

### Community 7 - "Result Data Access"
Cohesion: 0.10
Nodes (18): _bot_events(), _clean_log_text(), commission_warning(), _execution_summary(), _fib_label(), grid_rows(), grid_snapshot(), _labelled_values() (+10 more)

### Community 8 - "DSR PBO Views"
Cohesion: 0.18
Nodes (21): _by_objective(), _conclusion(), _decimal(), detail(), _diagnostic_source_label(), _frozen_objective_text(), _metric(), _minimum_trades_label() (+13 more)

### Community 9 - "MACD Break cBot"
Cohesion: 0.11
Nodes (11): MACDBreak, EnableDailyLossLimit, EnableMaxConsecutiveLosses, EnableMaxDrawdown, KslLevel, KtpLevel, MaxConsecutiveLosses, MaxDailyLossPercent (+3 more)

### Community 10 - "Backtest Settings Mapper"
Cohesion: 0.12
Nodes (6): BacktestMapper, Capabilities, SupportsAutomaticCommission, SupportsPreciseConversion, UnsupportedBacktestingCapabilityException, Capability

### Community 11 - "Redis Signal Materialization"
Cohesion: 0.12
Nodes (10): RedisProfile, _connection_pool(), fetch_signal_rows(), materialize_signal_csv(), redis_client(), resolve_signal_path(), signal_list_key(), signal_window_sha256() (+2 more)

### Community 12 - "Statistical Readout Pipeline"
Cohesion: 0.13
Nodes (12): _load_for(), _montecarlo_config(), _resolve_objective(), _resolve_trial_policy(), run_dsr(), run_montecarlo(), run_montecarlo_oos(), run_pbo() (+4 more)

### Community 13 - "Grid Session Actions"
Cohesion: 0.14
Nodes (13): auth_options(), completed_execution_policy_available(), _enum_index(), _enum_selection(), _execution_key(), prepare_grid_diagnostics_session(), prepare_grid_search_session(), preview_walkforward_windows() (+5 more)

### Community 14 - "Walk Forward Views"
Cohesion: 0.17
Nodes (19): label_from_input(), detail(), _duration(), _elapsed_text(), _fib(), layout(), live_progress(), _median() (+11 more)

### Community 15 - "Trial Selection Logic"
Cohesion: 0.14
Nodes (13): coerce_float(), _load_trials(), _trades_by_day(), classify(), eligible(), eligible_under_policy(), exclusion_reasons(), ftmo_screen() (+5 more)

### Community 16 - "Plugin Backtest Runner"
Cohesion: 0.13
Nodes (7): BoBacktestRunner, ClaimedDir, EffectiveMaxConcurrent, FailedDir, HeartbeatDir, JobsDir, ResultsDir

### Community 17 - "CLI Optimize Execution"
Cohesion: 0.14
Nodes (12): _bot_log(), cancel_requested(), child_environment(), last_progress_pct(), _make_poll(), poll(), _notify_progress(), optimize_parameters() (+4 more)

### Community 18 - "Core Time Models"
Cohesion: 0.15
Nodes (7): daily_balance_and_floor(), daily_returns(), daily_returns_by_day(), Experiment, parse_point_time(), _points_by_day(), DailySeriesTests

### Community 19 - "Experiment Planning"
Cohesion: 0.21
Nodes (15): Window, as_date(), expand_grid(), finite_positive_float(), _is_arithmetic(), _list(), nonnegative_int(), _param_combinations() (+7 more)

### Community 20 - "Experiment Result Store"
Cohesion: 0.17
Nodes (3): ExperimentStore, _has_bad_flag(), _lock_view()

### Community 21 - "CLI Binary Selection"
Cohesion: 0.20
Nodes (14): advertised_data_modes(), cli_has_command(), cli_has_option(), cli_option_supported(), _cli_version(), _command_reference(), _match_command(), _match_option() (+6 more)

### Community 22 - "Strategy Configuration"
Cohesion: 0.13
Nodes (5): _config(), FtmoProfile, load_ftmo_profile(), ParamDef, StrategyProfile

### Community 23 - "Engine Profile Configuration"
Cohesion: 0.13
Nodes (6): load_strategy_profile(), EngineProfile, ConfigurationPlannerTests, argv_for(), FacilitatorTests, metadata_for()

### Community 24 - "API Bridge Client"
Cohesion: 0.22
Nodes (5): BridgeClient, BacktestApiJob, BacktestApiResult, main(), BridgeClientTests

### Community 26 - "CLI Result Classification"
Cohesion: 0.18
Nodes (14): _auth_env_available(), _check_period(), _classify(), _load_optimize_passes(), _metadata_text(), _param_key(), ProcResult, _read_optres() (+6 more)

### Community 27 - "Combo cBot Lifecycle"
Cohesion: 0.11
Nodes (11): Combo, EnableDailyLossLimit, EnableMaxConsecutiveLosses, EnableMaxDrawdown, KslLevel, KtpLevel, MaxConsecutiveLosses, MaxDailyLossPercent (+3 more)

### Community 28 - "API Runner Results"
Cohesion: 0.17
Nodes (8): _api_parameters(), _classify(), _find_bot_log(), run_backtest(), validate_capabilities(), BridgeTimeout, FailureCode, RunStatus

### Community 29 - "Walk Forward Orchestration"
Cohesion: 0.16
Nodes (9): load_engine_profile(), resolve_input_fingerprint(), run_grid(), _month_add(), pick(), plateau(), run_walkforward(), walkforward_windows() (+1 more)

### Community 30 - "Experiment Facilitator"
Cohesion: 0.19
Nodes (11): _fingerprint_end(), input_fingerprint(), InputFingerprintMismatch, run_experiment(), crash_result(), one(), record_result(), reserve_launch() (+3 more)

### Community 31 - "Pipeline Stage Locks"
Cohesion: 0.17
Nodes (10): check(), get_stage(), _lock_path(), _read(), read_all(), record(), stage_hash(), _write() (+2 more)

### Community 32 - "Grid Callback Progress"
Cohesion: 0.12
Nodes (8): grid_execution_status(), _duration(), _elapsed_seconds(), _live_grid_progress(), register(), strategy_option(), update_strategy(), update_workload()

### Community 34 - "Backtest Job Contract"
Cohesion: 0.11
Nodes (18): BacktestJob, AlgoPath, Balance, Commission, CommissionAuto, CommissionType, DataFile, DataMode (+10 more)

### Community 35 - "Diagnostics Callbacks"
Cohesion: 0.12
Nodes (9): html_empty(), register(), create_session(), refresh_status(), show_commission_warning(), strategy_option(), update_strategy(), update_workload() (+1 more)

### Community 36 - "Backtest Result Contract"
Cohesion: 0.11
Nodes (18): BacktestResult, BacktestingError, Capabilities, CommissionAutoApplied, CommissionAutoRequested, Diagnostics, EndedUtc, Error (+10 more)

### Community 37 - "Walk Forward Cache Tests"
Cohesion: 0.13
Nodes (4): WalkforwardCacheKeyTests, WalkforwardHookTests, WalkforwardTests, fake_run()

### Community 38 - "Plugin Deployment Tools"
Cohesion: 0.21
Nodes (10): _cli_version(), deploy(), _find_cli(), main(), _roots(), CliProbeError, _command_reference_for(), kill_tree() (+2 more)

### Community 39 - "Evidence Parsing"
Cohesion: 0.22
Nodes (11): _all(), bot_summary(), compare_history(), _count_rejections(), environment(), metrics(), _ms_date(), read_gui_report() (+3 more)

### Community 41 - "Deflated Sharpe Ratio"
Cohesion: 0.19
Nodes (8): deflated_sharpe_ratio(), _evaluate(), expected_max_sharpe(), kurtosis(), run(), sharpe_ratio(), skewness(), _standardized_moment()

### Community 43 - "Store Claim Ownership"
Cohesion: 0.22
Nodes (6): Claim, export_release(), LostClaimError, _owner(), _owner_alive(), _pid_alive()

### Community 45 - "Monte Carlo Engine"
Cohesion: 0.29
Nodes (7): _cut_blocks(), MonteCarloConfig, _pct(), _resample_stationary(), run(), run_dual(), run_methods_compare()

### Community 46 - "Plugin Capability Contracts"
Cohesion: 0.15
Nodes (9): BacktestingCapabilities, ApiAssemblyVersion, AutomaticCommission, LegacyUsdPerMillionCommission, PreciseConversion, TickCsv, TypedCommission, BridgeSchemas (+1 more)

### Community 47 - "Runtime Diagnostics Contract"
Cohesion: 0.17
Nodes (10): LeverageTierInfo, Leverage, Volume, RuntimeDiagnostics, AccountBrokerName, AccountPreciseLeverage, AccountStopOutLevel, AccountTotalMarginCalculationType (+2 more)

### Community 48 - "PBO Computation"
Cohesion: 0.27
Nodes (5): _average_rank(), _block_totals(), _order_key(), _performance(), run()

### Community 49 - "Diagnostics Source Actions"
Cohesion: 0.18
Nodes (5): _nonnegative_int(), register(), create_diagnostics(), inspect_source(), run_diagnostics()

### Community 50 - "CLI Reliability Research"
Cohesion: 0.18
Nodes (10): Plugin API versus CLI A/B Audit, Approximate Margin Conversion Look-Ahead, Plugin API Equals CLI Execution Engine, Conversion-Symbol Tick Hang, Three Tick Execution Modes, CLI Tick Bottleneck Diagnosis, Concurrent CLI Backtest Analysis, cTrader CLI Practical Issues Catalog (+2 more)

### Community 53 - "Walk Forward Data Access"
Cohesion: 0.17
Nodes (5): walkforward_progress(), walkforward_table(), historical_seconds_per_day(), walkforward_execution_status(), walkforward_results()

### Community 55 - "Dashboard Resource Groups"
Cohesion: 0.38
Nodes (4): DashboardSessionStoreTests, _diagnostics_plan(), _grid_plan(), isolated_store()

### Community 56 - "MA Cross Signal Loading"
Cohesion: 0.17
Nodes (6): SignalRow, Atr, AvailableTime, BarTime, Direction, IsHandled

### Community 57 - "MACD Signal Loading"
Cohesion: 0.17
Nodes (6): SignalRow, Atr, AvailableTime, BarTime, Direction, IsHandled

### Community 62 - "Combo Stop Loss Levels"
Cohesion: 0.18
Nodes (11): SlFibLevel, Fib0618, Fib0786, Fib1000, Fib1272, Fib1618, Fib2058, Fib2618 (+3 more)

### Community 63 - "Combo Take Profit Levels"
Cohesion: 0.18
Nodes (11): TpFibLevel, Fib1000, Fib1272, Fib1618, Fib2058, Fib2618, Fib3330, Fib4236 (+3 more)

### Community 64 - "MA Cross Stop Loss"
Cohesion: 0.18
Nodes (11): SlFibLevel, Fib0618, Fib0786, Fib1000, Fib1272, Fib1618, Fib2058, Fib2618 (+3 more)

### Community 65 - "MA Cross Take Profit"
Cohesion: 0.18
Nodes (11): TpFibLevel, Fib1000, Fib1272, Fib1618, Fib2058, Fib2618, Fib3330, Fib4236 (+3 more)

### Community 66 - "MACD Stop Loss"
Cohesion: 0.18
Nodes (11): SlFibLevel, Fib0618, Fib0786, Fib1000, Fib1272, Fib1618, Fib2058, Fib2618 (+3 more)

### Community 67 - "MACD Take Profit"
Cohesion: 0.18
Nodes (11): TpFibLevel, Fib1000, Fib1272, Fib1618, Fib2058, Fib2618, Fib3330, Fib4236 (+3 more)

### Community 68 - "API Redis Configuration"
Cohesion: 0.20
Nodes (6): API Engine Documentation, File-Mailbox Backtest Bridge, Redis Signal Profiles, Unified Workflow Configuration, cBot Redis Signal-Source Feasibility Study, Backtest API Plugin Optimize Spike Record

### Community 69 - "Workflow Session Launch"
Cohesion: 0.29
Nodes (5): run_grid_search_session(), run_single_backtest_session(), run_walkforward_session(), profile_from_input(), require_profile()

### Community 73 - "Repository Research Rules"
Cohesion: 0.22
Nodes (6): Project State and Handoff Log, Time-Series Strategy Optimization Methodology, Parameter Stability Plateau, Robert Pardo â€” Design, Testing, and Optimization of Trading Systems, Rolling Walk-Forward Optimization, Repository Design and Coding Rules

### Community 74 - "Dashboard Filter Options"
Cohesion: 0.28
Nodes (4): session_form_options(), signal_date_bounds(), signal_date_bounds_for_selection(), _unique_choices()

### Community 77 - "Research Provenance"
Cohesion: 0.25
Nodes (5): Immutable Run Provenance, Interface Parity versus Research Validity, Phase 2 Research and Optimization Design, Content-Addressed Stage Cache, Pipeline Lock and Dashboard Architecture

### Community 78 - "Form Input Preparation"
Cohesion: 0.32
Nodes (4): prepare_single_backtest_session(), prepare_walkforward_session(), command_field(), identity_field()

### Community 79 - "Dashboard Layout Components"
Cohesion: 0.29
Nodes (3): serve_layout(), layout(), status_region()

### Community 80 - "Bootstrap Method Research"
Cohesion: 0.25
Nodes (7): Monte Carlo Block-Bootstrap Variants, Carlstein 1986 Non-Overlapping Bootstrap, KÃ¼nsch 1989 Moving Block Bootstrap, Moving Block Bootstrap, Non-Overlapping Block Bootstrap, Politis and Romano 1994 Stationary Bootstrap, Stationary Bootstrap

### Community 87 - "DSR PBO Research"
Cohesion: 0.38
Nodes (6): Bailey and LÃ³pez de Prado â€” The Deflated Sharpe Ratio, Bailey et al. â€” The Probability of Backtest Overfitting, CSCV Probability of Backtest Overfitting, Deflated Sharpe Ratio, DSR and PBO Formula Verification, Trading Strategy Overfit Validation Toolkit

### Community 88 - "Full Validation Research"
Cohesion: 0.29
Nodes (4): Five-Stage Validation Pipeline, Full Validation Pipeline Design, Monte Carlo Equity versus Balance Methodology, FTMO Equity-Based Loss Model

### Community 91 - "Combo Signal Row"
Cohesion: 0.29
Nodes (7): SignalRow, Atr, AvailableTime, BarTime, Direction, EntryPrice, IsHandled

### Community 92 - "Plugin Job Context"
Cohesion: 0.33
Nodes (5): JobContext, ClaimedPath, Job, LastProgress, StartedUtc

### Community 93 - "CLI Argument Builder"
Cohesion: 0.40
Nodes (4): build_argv(), build_optimize_argv(), _cli_date(), _engine_flags()

### Community 101 - "Plugin Project Configuration"
Cohesion: 0.40
Nodes (4): net6.0, cTrader.Automate (*), Microsoft.NET.Sdk, cTrader.Automate.SourceGenerators (*)

### Community 108 - "CLI Optimization Research"
Cohesion: 0.67
Nodes (3): cTrader CLI Backtest and Optimize Research, Native CLI 5.10 Optimization, Single-Thread Backtests in a Process Pool

### Community 111 - "Combo Project Configuration"
Cohesion: 0.50
Nodes (3): net6.0, cTrader.Automate (*), Microsoft.NET.Sdk

### Community 113 - "MA Cross Project"
Cohesion: 0.50
Nodes (3): net6.0, cTrader.Automate (*), Microsoft.NET.Sdk

### Community 114 - "MACD Project"
Cohesion: 0.50
Nodes (3): net6.0, cTrader.Automate (*), Microsoft.NET.Sdk

## Knowledge Gaps
- **190 isolated node(s):** `Fib0618`, `Fib0786`, `Fib1000`, `Fib1272`, `Fib1618` (+185 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 706 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **47 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `RunSpec` connect `Run Specification Model` to `Optimize Backend Tests`, `Core Run Lifecycle`, `Redis Signal Materialization`, `CLI Optimize Execution`, `Core Time Models`, `Experiment Planning`, `Experiment Result Store`, `CLI Binary Selection`, `Strategy Configuration`, `API Bridge Client`, `CLI Result Classification`, `API Runner Results`, `Evidence Parsing`, `Dashboard Composition Tests`, `Store Claim Ownership`, `Protocol Budget Store`, `Signal Path Resolution`, `Evidence Gate Tests`, `Monte Carlo OOS Tests`, `API Validation Tests`, `Objective Readout Tests`, `Monte Carlo Store Tests`, `CLI Argument Builder`, `Authentication Failure Tests`, `Walk Forward Readout`, `Stale Claim Recovery`?**
  _High betweenness centrality (0.048) - this node is a cross-community bridge._
- **Are the 32 inferred relationships involving `RunSpec` (e.g. with `_find_bot_log()` and `run_backtest()`) actually correct?**
  _`RunSpec` has 32 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Fib0618`, `Fib0786`, `Fib1000` to the rest of the system?**
  _190 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Optimize Backend Tests` be split into smaller, more focused modules?**
  _Cohesion score 0.05660945498343872 - nodes in this community are weakly interconnected._
- **Why does `ExperimentStore` connect `Experiment Result Store` to `Grid Callback Progress`, `Result Data Access`, `Store Claim Ownership`, `Statistical Readout Pipeline`, `Trial Selection Logic`, `Core Time Models`, `Walk Forward Data Access`, `Run Specification Model`, `API Runner Results`, `Walk Forward Orchestration`, `Experiment Facilitator`?**
  _High betweenness centrality (0.026) - this node is a cross-community bridge._
- **Should `Dashboard Session Store` be split into smaller, more focused modules?**
  _Cohesion score 0.06659056316590563 - nodes in this community are weakly interconnected._
- **Why does `RunnerCapabilityAndStallTests` connect `CLI Capability Tests` to `Dashboard Composition Tests`?**
  _High betweenness centrality (0.018) - this node is a cross-community bridge._