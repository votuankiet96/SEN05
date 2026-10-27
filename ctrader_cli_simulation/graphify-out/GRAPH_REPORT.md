# Graph Report - Robots  (2026-10-26)

## Corpus Check
- 104 files · ~203,797 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 3 file(s) not represented in the graph (top: (none) 1, .algo 1, .bat 1)

## Summary
- 2238 nodes · 5104 edges · 145 communities (98 shown, 47 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 189 edges (avg confidence: 0.91)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- session_store.py
- require_profile
- load_strategy_profile
- FacilitatorBoxTests
- build_experiment
- single/views.py
- Five-Stage Validation Pipeline
- load_engine_profile
- ui_common.py
- test_core_engine.py
- master_db.py
- facilitator.py
- with_signal_overrides
- planner.py
- walkforward/views.py
- Backtest Defaults
- selection.py
- dsr_pbo/views.py
- ExperimentStore
- _fake_result
- BacktestBot
- dash_mantine_components
- sha256_file
- evidence.py
- signal_trans.py
- multichannel/views.py
- walkforward.py
- US30 Net and Drawdown Report
- prepare_grid_search_session
- pipeline_lock.py
- test_risk_allocation.py
- DashboardPlanTests
- SignalRow
- data_access.py
- ProtocolStore
- ProfileAndSourceContractTests
- SelectionClassifyTests
- Reproducible SQL Backtest Repository
- hashlib
- register
- RunSpec
- register
- WalkforwardCacheKeyTests
- dsr.py
- AuditRemediationTests
- _FakeApp
- run
- .GetTradingSymbol
- _reference_mean_return_pbo
- grid/callbacks.py
- 002_views.sql
- DashboardSessionStoreTests
- math
- cli_runner.py
- _texts
- Proposed backtest design (smoke test, full period, per year, per half-year, rolling windows, stress test)
- SlFibLevel
- TpFibLevel
- ExperimentLockTests
- backtest_run Table
- SignalWindowHashTests
- Experiment
- run_name
- 001_schema.sql
- RunnerCapabilityAndStallTests
- MonteCarloOosTests
- BacktestBot.cs
- channel_selection.py
- os
- SelectCliTests
- ReadoutObjectiveTests
- DsrTests
- PboTests
- PipelineLockTests
- MonteCarloIntradayFloorTests
- test_dashboard_contracts.py
- Channel
- ReadoutTests
- ChannelSourceTests
- PipelineCachingIntegrationTests
- RunGridLockPolicyTests
- DsrSelectedTrialTests
- Q: [$graphify] tôi yêu cầu bạn chạy backtest cho chiến lược macd break cho xau full khung thời gian từ 2022 đến hiện tại thì bạn chạy được ko? Chưa làm nhé, nếu chạy thì chạy sao? full các năm, giữa các năm, nữa năm, hay thế nào? ksl và ktp lần lượt là 1.618 và 2.618, risk per trade để 1%
- Q: Tức là dashboard vẫn dg chạy tót phải ko? Hiện các session trên đó là gì? Hình như chưa có session nào chạy đa symbol đa tf đa chiến lược phải ko
- Q: Thiết kế session dashboard đa symbol, đa timeframe, đa chiến lược cho BacktestBot
- Q: Ok, triển khai session dashboard đa symbol đa timeframe đa chiến lược
- Q: Ok, đọc đi
- Q: Validate BacktestBot multi-strategy multi-symbol multi-timeframe Q1 2026
- Q: Các tiến trình đó khi chạy xong, lưu kết quả như thế nào? Lưu ở đâu
- Q: Okie, hãy tập trung vào job us30 vừa rồi, hãy cho tôi biết yêu cầu và tiến trình chạy là gì? Kết quả thế nào?
- Q: Tôi muốn đọc và giải thích các cấu phần trong backtet_defaults..yaml, trước khi rtar lời hãy đọc graphify để nắm băt nhé
- Q: Tôi ko hiểu yếu tố kĩ thuật, nhưng trong này có phải bộ tham số mẫu để đi backtest khi yêu cầu triển khai ko?
- Q: Nhóm shared, shared_06, shared_mix là gì?
- Q: Báo cáo chi tiết cho tôi biết theo chiến lược/khung thời gian; chỉ cần Net/DD; làm rõ bao nhiêu kỳ có lãi trên tổng
- Q: Nếu xét trên lãi lớn, bền và dd nhỏ, so sánh giữa db0 và db1, thì nên chọn combo theo khung nào?
- Q: Okie, với MA Cross thì tốt nhất trong các con xấu nhất?
- Q: Okie, giờ tới MACD Break
- Q: Okie, giờ nêu tôi thực hiện 1 backtest đa symbol đa tiemframes và đa nguồn dữ liệu theo cachsn ày thì có triển khai được không: DB0 Combo H4; DB1 MA M30; DB0 MACD Break H4
- MA Cross Source Query
- Q: Tại sao lại đặt tên là shared, rồi shared_06, rồi shared_mix?
- Q: Ok, vậy hãy chạy thử nhé, cũng 19 kỳ luôn
- Q: Giờ chạy trường hợp bỏ MA Cross ra nhé
- Q: Tôi muốn đọc lại kết quả của combo và macd break theo từng khung vào lệnh với db0 và db1
- Q: Triển khai DB1 Combo H1 H4 cùng MACD Break H3 H4 risk 0.6% một cụm lệnh, 19 kỳ
- Q: Hãy thống kê xem nhóm lệnh của khung nào gây lỗ nhiều nhất
- Q: Nhóm nào cho DD ít mà lời vẫn cao trong ba kênh US30 DB1 Combo H4, MACD H3, MACD H4?
- Q: Đó có là thiết kế tốt chưa?
- Q: Single backtest và multi-backtest đang cấu hình ở đâu, prefix và tham số chạy là gì, multi một kênh có thành single không?
- Q: Làm sao giữ trọn vẹn một luồng, chỉ config.yaml, truyền tham số linh động cho single và multi backtest mà không tạo YAML mới?
- Q: Kế hoạch hợp nhất single và multi backtest thành một luồng là gì?
- Q: Ok, giờ tôi muốn bạn chuyển sang sesssion backtest đa symbol, đa timeframes, đa chiến lược.  Hãy cho tôi biết session đó dang chạy thế nào, luồng ra sao, cách chạy?
- Q: Ok, hãy tổng hợp lại xem bạn hiểu thế nào, đầy đủ ko, và dự kiến triển khai ra sao, thay đổi gì ở nhóm file nò?
- Q: Ok, đúng hết rồi nhé. Tuy nhiên chổ output sẽ cần làm thêm thế này, Kết quả theo từng kỳ là đúng rồi, nhưng chủ yếu cần Net Profit và DD. Bên cạnh đó nên có tổng lỗ tổng lãi, đóng góp của nhóm channel nào trong lãi hay trong lỗ. Hiểu y tôi ko? Phần output đó kas quan trọng trọng cách tôi quyết định kết hợp ra sao, chốt điểm này nưa rồi làm
- Q: Ok, tôi cần bạn kiểm tra lại dashboard, nhiều cấu phần tiếng Anh và Việt lẫn lộn, hãy đồng nhất tiếng anh hết nhé!
- StrategyDef
- BacktestBot.csproj
- EngineProfile
- cTrader CLI Backtest and Optimize Research
- WalkforwardUsesTheSelectedStrategiesTests
- ChildEnvironmentTests
- DashboardCompositionTests
- Q: Multi-channel dashboard v2 chạy, triển khai và đọc kết quả như thế nào?
- Live Network Backtest Non-Reproducibility
- MasterDbPureTests
- _specs
- actions.py
- trade_excursion.py
- sqlite3
- Q: Ok, tôi muốn kiểm tra điểm này, vì sao lại có folder này trong bo_workflow, lẽ a phải nằm trong ROBOTs thôi chứ: C:\Users\Administrator\Documents\cAlgo\Sources\Robots\bo_workflow\graphify-out
- RedisKeyForSnapshotTests

## God Nodes (most connected - your core abstractions)
1. `BacktestBot` - 68 edges
2. `RunSpec` - 66 edges
3. `ExperimentStore` - 48 edges
4. `load_strategy_profile()` - 46 edges
5. `with_signal_overrides()` - 38 edges
6. `expand_grid()` - 38 edges
7. `run_experiment()` - 36 edges
8. `load_engine_profile()` - 34 edges
9. `RunStatus` - 33 edges
10. `run_optimize_box()` - 29 edges

## Surprising Connections (you probably didn't know these)
- `ArchivedRuns Preservation Protocol` --semantically_similar_to--> `File Store as Source of Truth`  [INFERRED] [semantically similar]
  AGENT.md → bo_workflow/research_notes/sql-master-database-plan-2026-10-14.md
- `Portfolio Risk Allocation` --conceptually_related_to--> `Multi-channel Dashboard v2`  [INFERRED]
  bo_workflow/config.example.yaml → graphify-out/memory/query_20261018_104407_1ad086d0_ok__hãy_tổng_hợp_lại_xem_bạn_hiểu_thế_nào__đầy_đủ.md
- `run_session()` --indirect_call--> `detail()`  [INFERRED]
  bo_workflow/dashboard/multichannel/callbacks.py → bo_workflow/dashboard/multichannel/views.py
- `BacktestBot Strategy Profile` --conceptually_related_to--> `Multi-leg Profit Taking`  [INFERRED]
  bo_workflow/config.example.yaml → CLAUDE.md
- `Preregistered Experiment Contract` --semantically_similar_to--> `Five-Stage Validation Pipeline`  [INFERRED] [semantically similar]
  bo_workflow/research_notes/phase-2-research-and-optimization-design.md → bo_workflow/research_notes/full-validation-pipeline-design.md

## Import Cycles
- 2-file cycle: `bo_workflow/core_engine/master_db.py -> bo_workflow/core_engine/models.py -> bo_workflow/core_engine/master_db.py`
- 3-file cycle: `bo_workflow/core_engine/master_db.py -> bo_workflow/core_engine/output_util/run_export.py -> bo_workflow/core_engine/models.py -> bo_workflow/core_engine/master_db.py`
- 3-file cycle: `bo_workflow/core_engine/configuration.py -> bo_workflow/core_engine/models.py -> bo_workflow/core_engine/master_db.py -> bo_workflow/core_engine/configuration.py`
- 4-file cycle: `bo_workflow/core_engine/configuration.py -> bo_workflow/core_engine/models.py -> bo_workflow/core_engine/master_db.py -> bo_workflow/core_engine/output_util/run_export.py -> bo_workflow/core_engine/configuration.py`
- 4-file cycle: `bo_workflow/core_engine/master_db.py -> bo_workflow/core_engine/output_util/run_export.py -> bo_workflow/core_engine/output_util/evidence.py -> bo_workflow/core_engine/models.py -> bo_workflow/core_engine/master_db.py`
- 4-file cycle: `bo_workflow/core_engine/master_db.py -> bo_workflow/core_engine/output_util/run_export.py -> bo_workflow/core_engine/signal_trans.py -> bo_workflow/core_engine/models.py -> bo_workflow/core_engine/master_db.py`
- 5-file cycle: `bo_workflow/core_engine/configuration.py -> bo_workflow/core_engine/models.py -> bo_workflow/core_engine/master_db.py -> bo_workflow/core_engine/output_util/run_export.py -> bo_workflow/core_engine/signal_trans.py -> bo_workflow/core_engine/configuration.py`

## Hyperedges (group relationships)
- **Current Master Database Schema** — bo_workflow_research_notes_sql_master_database_plan_2026_10_14_current_five_table_schema, bo_workflow_research_notes_sql_master_database_plan_2026_10_14_backtest_run, bo_workflow_research_notes_sql_master_database_plan_2026_10_14_trade, bo_workflow_research_notes_sql_master_database_plan_2026_10_14_daily_equity, bo_workflow_research_notes_sql_master_database_plan_2026_10_14_bot_build, bo_workflow_research_notes_sql_master_database_plan_2026_10_14_signal_snapshot [EXTRACTED 1.00]
- **Full Strategy Validation Flow** — bo_workflow_research_notes_full_validation_pipeline_design_five_stage_validation_pipeline, bo_workflow_research_notes_time_series_strategy_optimization_methodology_walk_forward_optimization, bo_workflow_research_notes_dsr_pbo_formula_verification_deflated_sharpe_ratio, bo_workflow_research_notes_dsr_pbo_formula_verification_cscv_pbo, bo_workflow_research_notes_montecarlo_equity_vs_balance_daily_return_methodology_dual_series_monte_carlo [EXTRACTED 1.00]
- **CLI Tick Reliability Controls** — bo_workflow_research_notes_cli_tick_bottleneck_diagnosis_2026_09_10_three_tick_execution_modes, bo_workflow_research_notes_cli_tick_bottleneck_diagnosis_2026_09_10_conversion_symbol_tick_hang, bo_workflow_research_notes_ctrader_cli_practical_issues_catalog_2026_09_10_cli_operational_safety_controls, bo_workflow_research_notes_ctrader_cli_concurrent_backtest_analysis_bounded_parallelism [INFERRED 0.85]
- **Multi-channel Dashboard Lifecycle** — graphify_out_memory_query_20261016_115552_bbfaba15_t_c_l_dashboard_v_n_dg_ch_y_t_t_ph_i_ko_hi_n_c_c_dashboard_session_inventory, graphify_out_memory_query_20261016_121101_53fbf58f_thi_t_k_session_dashboard_a_symbol_a_timeframe_multi_channel_dashboard_design, graphify_out_memory_query_20261016_135735_f2c80821_ok_tri_n_khai_session_dashboard_a_symbol_a_time_multi_channel_dashboard_implementation, graphify_out_memory_query_20261017_114935_a5a5de41_ok_c_i_multi_channel_validation_plan, graphify_out_memory_query_20261017_121110_c49f3f0d_validate_backtestbot_multi_strategy_multi_symbol_m_exact_four_channel_runtime_validation, graphify_out_memory_query_20261017_145706_9094d5b6_c_l_thi_t_k_t_t_ch_a_canonical_channel_manifest_design, graphify_out_memory_query_20261017_150320_64f286ee_single_backtest_v_multi_backtest_ang_c_u_h_nh_single_multi_backtest_configuration [INFERRED 0.85]
- **Per-period Channel Reporting** — graphify_out_memory_query_20261018_104407_1ad086d0_ok_h_y_t_ng_h_p_l_i_xem_b_n_hi_u_th_n_o_y_independent_period_portfolios, graphify_out_memory_query_20261018_104956_713f4cb4_ok_ng_h_t_r_i_nh_tuy_nhi_n_ch_output_s_c_n_per_period_portfolio_metrics, graphify_out_memory_query_20261018_104956_713f4cb4_ok_ng_h_t_r_i_nh_tuy_nhi_n_ch_output_s_c_n_channel_pnl_attribution, graphify_out_memory_query_20261018_113907_762e0d8c_multi_channel_dashboard_v2_ch_y_tri_n_khai_v_c_run_key_result_resolution [INFERRED 0.85]
- **Shared-account Risk Execution** — graphify_out_memory_query_20261017_123742_7824ff08_nh_m_shared_shared_06_shared_mix_l_g_shared_account_risk_presets, graphify_out_memory_query_20261017_131034_8f30b40f_ok_v_y_h_y_ch_y_th_nh_c_ng_19_k_lu_n_three_channel_shared_portfolio, graphify_out_memory_query_20261017_131941_4142b526_gi_ch_y_tr_ng_h_p_b_ma_cross_ra_nh_two_channel_shared_portfolio, graphify_out_memory_query_20261017_133148_0b08d8b0_tri_n_khai_db1_combo_h1_h4_c_ng_macd_break_h3_h4_r_four_channel_db1_shared_portfolio [INFERRED 0.85]
- **Unified Channel Backtest Flow** — graphify_out_memory_query_20261017_150701_5bcb36d8_l_m_sao_gi_tr_n_v_n_m_t_lu_ng_ch_config_yaml_t_channelspec, graphify_out_memory_query_20261017_150924_95de33a1_k_ho_ch_h_p_nh_t_single_v_multi_backtest_th_nh_m_unified_backtest_pipeline, graphify_out_memory_query_20261018_113907_762e0d8c_multi_channel_dashboard_v2_ch_y_tri_n_khai_v_c_multichannel_v2_execution_flow, bo_workflow_config_example_backtest_configuration [INFERRED 0.85]
- **US30 Nineteen-period Evaluation** — graphify_out_memory_query_20261017_123254_f4891c34_okie_h_y_t_p_trung_v_o_job_us30_v_a_r_i_h_y_cho_us30_nineteen_period_research_run, graphify_out_memory_query_20261017_124132_6ab92f46_b_o_c_o_chi_ti_t_cho_t_i_bi_t_theo_chi_n_l_c_khun_us30_net_dd_report, graphify_out_memory_query_20261017_125626_4150d024_n_u_x_t_tr_n_l_i_l_n_b_n_v_dd_nh_so_s_nh_gi_a_combo_h4_selection, graphify_out_memory_query_20261017_125741_2c1fb476_okie_v_i_ma_cross_th_t_t_nh_t_trong_c_c_con_x_u_ma_cross_fallback_selection, graphify_out_memory_query_20261017_125855_ba3a8164_okie_gi_t_i_macd_break_macd_break_h4_selection, graphify_out_memory_query_20261017_130129_02eae1c1_okie_gi_n_u_t_i_th_c_hi_n_1_backtest_a_symbol_hybrid_channel_input_design, graphify_out_memory_query_20261017_130217_6bef47ca_v_y_so_s_nh_gi_a_d1_v_db0_c_a_ma_cross_m30_ma_cross_m30_source_choice, graphify_out_memory_query_20261017_131034_8f30b40f_ok_v_y_h_y_ch_y_th_nh_c_ng_19_k_lu_n_three_channel_shared_portfolio, graphify_out_memory_query_20261017_131941_4142b526_gi_ch_y_tr_ng_h_p_b_ma_cross_ra_nh_two_channel_shared_portfolio, graphify_out_memory_query_20261017_132117_32a3ac5c_t_i_mu_n_c_l_i_k_t_qu_c_a_combo_v_macd_break_t_isolated_combo_macd_results, graphify_out_memory_query_20261017_133148_0b08d8b0_tri_n_khai_db1_combo_h1_h4_c_ng_macd_break_h3_h4_r_four_channel_db1_shared_portfolio, graphify_out_memory_query_20261017_142800_1d87a731_h_y_th_ng_k_xem_nh_m_l_nh_c_a_khung_n_o_g_y_l_nh_channel_loss_attribution, graphify_out_memory_query_20261017_144428_d0038319_nh_m_n_o_cho_dd_t_m_l_i_v_n_cao_trong_ba_k_nh_us_low_drawdown_channel_choice [INFERRED 0.85]
- **Reproducible Backtest Identity Inputs** — bo_workflow_research_notes_sql_master_database_plan_2026_10_14_reproducible_run_identity, bo_workflow_research_notes_sql_master_database_plan_2026_10_14_bot_build, bo_workflow_research_notes_sql_master_database_plan_2026_10_14_signal_snapshot [INFERRED 0.95]

## Communities (145 total, 47 thin omitted)

### Community 0 - "session_store.py"
Cohesion: 0.05
Nodes (49): active_session(), ActiveSessionError, cancel_analysis(), claim_run(), complete(), confirmed_signal_profile(), create_draft_group(), create_grid_diagnostics() (+41 more)

### Community 1 - "require_profile"
Cohesion: 0.12
Nodes (13): _load_dashboard_profile(), _prepare_legacy_multi_channel_session(), prepare_walkforward_session(), run_grid_search_session(), _run_legacy_multi_channel_session(), run_multi_channel_session(), run_single_backtest_session(), run_walkforward_session() (+5 more)

### Community 2 - "load_strategy_profile"
Cohesion: 0.20
Nodes (4): load_strategy_profile(), ParamDef, ResolveSignalPathTests, metadata_for()

### Community 3 - "FacilitatorBoxTests"
Cohesion: 0.14
Nodes (5): FacilitatorBoxTests, run_optimize_box(), short(), _fake_result(), _metadata()

### Community 4 - "build_experiment"
Cohesion: 0.06
Nodes (11): build_experiment(), EvidenceExportTests, FindNativeOutputTests, make_events(), make_report(), MasterDbTests, work(), ms() (+3 more)

### Community 5 - "single/views.py"
Cohesion: 0.09
Nodes (36): active_run_summary(), bot_activity_rows(), _bot_activity_table(), _bot_event_time(), _cell(), _chart_time(), detail(), _drawdown_axis() (+28 more)

### Community 6 - "Five-Stage Validation Pipeline"
Cohesion: 0.05
Nodes (37): Plugin API versus CLI A/B Audit, Approximate Margin Conversion Look-Ahead, Plugin API Equals CLI Execution Engine, Conversion-Symbol Tick Hang, Three Tick Execution Modes, CLI Tick Bottleneck Diagnosis, Backtest API Plugin Optimize Spike Record, Concurrent CLI Backtest Analysis (+29 more)

### Community 7 - "load_engine_profile"
Cohesion: 0.14
Nodes (12): _config(), load_backtest_settings(), load_engine_profile(), load_master_db_profile(), resolve_backtest_config(), connect(), prepare_standard_single_backtest_session(), _standard_signal_key() (+4 more)

### Community 8 - "ui_common.py"
Cohesion: 0.07
Nodes (12): save_signal_profile(), _nonnegative_int(), register(), create_diagnostics(), inspect_source(), refresh_status(), run_diagnostics(), refresh_status() (+4 more)

### Community 9 - "test_core_engine.py"
Cohesion: 0.07
Nodes (20): FtmoProfile, load_ftmo_profile(), daily_balance_and_floor(), daily_equity_rows(), daily_returns(), daily_returns_by_day(), FailureCode, parse_point_time() (+12 more)

### Community 10 - "master_db.py"
Cohesion: 0.08
Nodes (19): MasterDbProfile, connection_string(), _convert(), _datetime(), describe_run(), drop_database(), _ensure_bot_build(), ensure_database() (+11 more)

### Community 11 - "facilitator.py"
Cohesion: 0.13
Nodes (19): ctrader_data_dir(), _find_known_runs(), _fingerprint_end(), input_fingerprint(), InputFingerprintMismatch, _redis_profile_id(), resolve_input_fingerprint(), run_backtest() (+11 more)

### Community 12 - "with_signal_overrides"
Cohesion: 0.26
Nodes (4): _select_strategies(), with_signal_overrides(), StrategySelectionTests, tables()

### Community 13 - "planner.py"
Cohesion: 0.15
Nodes (15): StrategyProfile, Window, as_date(), expand_grid(), finite_positive_float(), _is_arithmetic(), _list(), _param_combinations() (+7 more)

### Community 14 - "walkforward/views.py"
Cohesion: 0.16
Nodes (21): label_from_input(), numeric_column(), right_align(), detail(), _duration(), _elapsed_text(), _fib(), layout() (+13 more)

### Community 15 - "Backtest Defaults"
Cohesion: 0.07
Nodes (23): Backtest Configuration, Backtest Defaults, BacktestBot Strategy Profile, cTrader Engine Profiles, FTMO Rules Profile, Portfolio Risk Allocation, Redis Signal Profiles, Backtest Execution Convention (+15 more)

### Community 16 - "selection.py"
Cohesion: 0.14
Nodes (13): coerce_float(), _load_trials(), _TrialSet, classify(), eligible(), eligible_under_policy(), exclusion_reasons(), ftmo_screen() (+5 more)

### Community 17 - "dsr_pbo/views.py"
Cohesion: 0.17
Nodes (22): _by_objective(), _conclusion(), _decimal(), detail(), _diagnostic_source_label(), _frozen_objective_text(), _metric(), _minimum_trades_label() (+14 more)

### Community 18 - "ExperimentStore"
Cohesion: 0.14
Nodes (4): Claim, ExperimentStore, _lock_view(), LostClaimError

### Community 19 - "_fake_result"
Cohesion: 0.09
Nodes (10): DailyCliBudgetTests, run_backtest(), _fake_result(), RunExperimentLifecycleTests, run_backtest(), run_with_fake_sql(), run_backtest(), run_backtest() (+2 more)

### Community 20 - "BacktestBot"
Cohesion: 0.09
Nodes (16): BacktestBot, EnableDailyLossLimit, EnableMaxConsecutiveLosses, EnableMaxDrawdown, KslLevel, KtpLevel, Leg2Ktp, Leg3Ktp (+8 more)

### Community 21 - "dash_mantine_components"
Cohesion: 0.18
Nodes (22): _configuration(), detail(), _heatmap(), heatmap_figure(), gap_text(), layout(), _leaderboard(), leaderboard_table() (+14 more)

### Community 22 - "sha256_file"
Cohesion: 0.14
Nodes (13): MonteCarloConfig, run_dual(), stage_hash(), _load_for(), _montecarlo_config(), _resolve_objective(), _resolve_trial_policy(), run_dsr() (+5 more)

### Community 23 - "evidence.py"
Cohesion: 0.08
Nodes (26): read_algo_manifest(), _all(), bot_summary(), _close_key(), compare_history(), _count_rejections(), environment(), events_match_report() (+18 more)

### Community 24 - "signal_trans.py"
Cohesion: 0.08
Nodes (14): load_redis_profile(), RedisProfile, SignalChannel, _source_label(), _connection_pool(), fetch_signal_rows(), materialize_channel_csv(), materialize_strategy_csv() (+6 more)

### Community 25 - "multichannel/views.py"
Cohesion: 0.14
Nodes (12): serve_layout(), layout(), _bool_param(), channel_preview(), channel_rows(), contribution_detail(), default_channel(), detail() (+4 more)

### Community 26 - "walkforward.py"
Cohesion: 0.21
Nodes (6): _month_add(), pick(), plateau(), run_walkforward(), walkforward_windows(), nonnegative_int()

### Community 27 - "US30 Net and Drawdown Report"
Cohesion: 0.10
Nodes (14): Dashboard Session Inventory, Multi-channel Dashboard Implementation, Exact Four-channel Runtime Validation, Experiment Artifact Persistence, US30 Nineteen-period Research Run, Backtest Defaults Configuration, Shared-account Risk Presets, US30 Net and Drawdown Report (+6 more)

### Community 28 - "prepare_grid_search_session"
Cohesion: 0.22
Nodes (5): _enum_selection(), prepare_grid_search_session(), prepare_single_backtest_session(), _profile_timeframes(), _single_channel_overrides()

### Community 29 - "pipeline_lock.py"
Cohesion: 0.15
Nodes (10): check(), get_stage(), list_pipelines(), _lock_path(), _read(), read_all(), record(), _write() (+2 more)

### Community 30 - "test_risk_allocation.py"
Cohesion: 0.11
Nodes (12): load_portfolio_risk_settings(), PortfolioRiskSettings, allocate_equal_risk(), _channel_count(), _positive_decimal(), validate_manual_risks(), validated_decimals(), validated_total_percent() (+4 more)

### Community 32 - "SignalRow"
Cohesion: 0.12
Nodes (12): SignalRow, Atr, AvailableTime, BarDuration, BarTime, Channel, Direction, EntryPrice (+4 more)

### Community 33 - "data_access.py"
Cohesion: 0.06
Nodes (30): _bot_events(), _channel_label_map(), _clean_log_text(), cli_launch_budget(), commission_warning(), _execution_summary(), _fib_label(), grid_execution_status() (+22 more)

### Community 35 - "ProfileAndSourceContractTests"
Cohesion: 0.15
Nodes (3): _bot_text(), _metadata(), ProfileAndSourceContractTests

### Community 37 - "Reproducible SQL Backtest Repository"
Cohesion: 0.11
Nodes (12): Live Project State, Migration to Multi Bots, Project Status and Handoff Log, Two-Backend Pipeline Hardening, VM-BO20 Execution Environment, SQL Analysis Views, bo_research SQL Server Database, Concurrency-Safe Snapshot Deduplication (+4 more)

### Community 39 - "register"
Cohesion: 0.13
Nodes (8): html_empty(), register(), create_session(), show_commission_warning(), strategy_option(), update_strategy(), update_workload(), _validate_dates()

### Community 40 - "RunSpec"
Cohesion: 0.06
Nodes (8): RunSpec, ClassifyAuthFailureTests, CancellationHookTests, EvidenceSelectionTests, spec(), StaleClaimTests, StoreMonteCarloTests, WalkforwardReadoutTests

### Community 41 - "register"
Cohesion: 0.13
Nodes (6): _form_rows(), register(), change_rows(), create_session(), preview(), run_session()

### Community 42 - "WalkforwardCacheKeyTests"
Cohesion: 0.13
Nodes (4): WalkforwardCacheKeyTests, WalkforwardHookTests, WalkforwardTests, fake_run()

### Community 43 - "dsr.py"
Cohesion: 0.19
Nodes (8): deflated_sharpe_ratio(), _evaluate(), expected_max_sharpe(), kurtosis(), run(), sharpe_ratio(), skewness(), _standardized_moment()

### Community 47 - "run"
Cohesion: 0.31
Nodes (5): _cut_blocks(), _pct(), _resample_stationary(), run(), run_methods_compare()

### Community 48 - ".GetTradingSymbol"
Cohesion: 0.17
Nodes (3): SymbolQueue, Next, Signals

### Community 50 - "grid/callbacks.py"
Cohesion: 0.12
Nodes (9): _duration(), _elapsed_seconds(), _live_grid_progress(), register(), refresh_grid_status(), strategy_option(), update_strategy(), update_workload() (+1 more)

### Community 51 - "002_views.sql"
Cohesion: 0.27
Nodes (7): dbo.v_cell_half_year_profit, dbo.v_compare_same_cell, dbo.v_durable_cells, dbo.v_ftmo_daily_loss, dbo.v_run_overview, dbo.v_walkforward_oos_chain, dbo.v_walkforward_selected

### Community 52 - "DashboardSessionStoreTests"
Cohesion: 0.25
Nodes (4): DashboardSessionStoreTests, _diagnostics_plan(), _grid_plan(), isolated_store()

### Community 53 - "math"
Cohesion: 0.27
Nodes (5): _average_rank(), _block_totals(), _order_key(), _performance(), run()

### Community 54 - "cli_runner.py"
Cohesion: 0.06
Nodes (50): advertised_data_modes(), _auth_env_available(), _bot_log(), build_argv(), build_optimize_argv(), cancel_requested(), _check_period(), child_environment() (+42 more)

### Community 56 - "Proposed backtest design (smoke test, full period, per year, per half-year, rolling windows, stress test)"
Cohesion: 0.33
Nodes (12): Graphify query: run MACD Break backtest on XAU 2022-present?, Proposed backtest design (smoke test, full period, per year, per half-year, rolling windows, stress test), BacktestJob, BoBacktestRunner, Fixed spread/commission/swap/balance/currency assumptions, KslLevel, KtpLevel, MACDBreak (strategy / cBot) (+4 more)

### Community 57 - "SlFibLevel"
Cohesion: 0.18
Nodes (11): SlFibLevel, Fib0618, Fib0786, Fib1000, Fib1272, Fib1618, Fib2058, Fib2618 (+3 more)

### Community 58 - "TpFibLevel"
Cohesion: 0.18
Nodes (11): TpFibLevel, Fib1000, Fib1272, Fib1618, Fib2058, Fib2618, Fib3330, Fib4236 (+3 more)

### Community 60 - "backtest_run Table"
Cohesion: 0.20
Nodes (8): backtest_run Table, bot_build Table, daily_equity Table, Native events.json Reconciliation, Plateau Tie Nondeterminism, Reproducible Run Identity, signal_snapshot Table, trade Table

### Community 65 - "001_schema.sql"
Cohesion: 0.27
Nodes (7): dbo.bot_build, dbo.daily_equity, dbo.portfolio_channel, dbo.trade, ix_backtest_run_batch, ix_backtest_run_cell, ix_backtest_run_train

### Community 69 - "channel_selection.py"
Cohesion: 0.31
Nodes (4): available_timeframes(), canonical_channels(), canonical_selection(), _choices()

### Community 70 - "os"
Cohesion: 0.43
Nodes (3): _owner(), _owner_alive(), _pid_alive()

### Community 78 - "Channel"
Cohesion: 0.33
Nodes (6): Channel, Labels, RiskPercent, Strategy, SymbolName, TimeFrameName

### Community 80 - "ChannelSourceTests"
Cohesion: 0.15
Nodes (4): ChannelSourceTests, StrategyFileTests, fetch(), fetch()

### Community 85 - "Q: [$graphify] tôi yêu cầu bạn chạy backtest cho chiến lược macd break cho xau full khung thời gian từ 2022 đến hiện tại thì bạn chạy được ko? Chưa làm nhé, nếu chạy thì chạy sao? full các năm, giữa các năm, nữa năm, hay thế nào? ksl và ktp lần lượt là 1.618 và 2.618, risk per trade để 1%"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: [$graphify] tôi yêu cầu bạn chạy backtest cho chiến lược macd break cho xau full khung thời gian từ 2022 đến hiện tại thì bạn chạy được ko? Chưa làm nhé, nếu chạy thì chạy sao? full các năm, giữa các năm, nữa năm, hay thế nào? ksl và ktp lần lượt là 1.618 và 2.618, risk per trade để 1%, Source Nodes

### Community 86 - "Q: Tức là dashboard vẫn dg chạy tót phải ko? Hiện các session trên đó là gì? Hình như chưa có session nào chạy đa symbol đa tf đa chiến lược phải ko"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Tức là dashboard vẫn dg chạy tót phải ko? Hiện các session trên đó là gì? Hình như chưa có session nào chạy đa symbol đa tf đa chiến lược phải ko, Source Nodes

### Community 87 - "Q: Thiết kế session dashboard đa symbol, đa timeframe, đa chiến lược cho BacktestBot"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Thiết kế session dashboard đa symbol, đa timeframe, đa chiến lược cho BacktestBot, Source Nodes

### Community 88 - "Q: Ok, triển khai session dashboard đa symbol đa timeframe đa chiến lược"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Ok, triển khai session dashboard đa symbol đa timeframe đa chiến lược, Source Nodes

### Community 89 - "Q: Ok, đọc đi"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Ok, đọc đi, Source Nodes

### Community 90 - "Q: Validate BacktestBot multi-strategy multi-symbol multi-timeframe Q1 2026"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Validate BacktestBot multi-strategy multi-symbol multi-timeframe Q1 2026, Source Nodes

### Community 91 - "Q: Các tiến trình đó khi chạy xong, lưu kết quả như thế nào? Lưu ở đâu"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Các tiến trình đó khi chạy xong, lưu kết quả như thế nào? Lưu ở đâu, Source Nodes

### Community 92 - "Q: Okie, hãy tập trung vào job us30 vừa rồi, hãy cho tôi biết yêu cầu và tiến trình chạy là gì? Kết quả thế nào?"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Okie, hãy tập trung vào job us30 vừa rồi, hãy cho tôi biết yêu cầu và tiến trình chạy là gì? Kết quả thế nào?, Source Nodes

### Community 93 - "Q: Tôi muốn đọc và giải thích các cấu phần trong backtet_defaults..yaml, trước khi rtar lời hãy đọc graphify để nắm băt nhé"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Tôi muốn đọc và giải thích các cấu phần trong backtet_defaults..yaml, trước khi rtar lời hãy đọc graphify để nắm băt nhé, Source Nodes

### Community 94 - "Q: Tôi ko hiểu yếu tố kĩ thuật, nhưng trong này có phải bộ tham số mẫu để đi backtest khi yêu cầu triển khai ko?"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Tôi ko hiểu yếu tố kĩ thuật, nhưng trong này có phải bộ tham số mẫu để đi backtest khi yêu cầu triển khai ko?, Source Nodes

### Community 95 - "Q: Nhóm shared, shared_06, shared_mix là gì?"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Nhóm shared, shared_06, shared_mix là gì?, Source Nodes

### Community 96 - "Q: Báo cáo chi tiết cho tôi biết theo chiến lược/khung thời gian; chỉ cần Net/DD; làm rõ bao nhiêu kỳ có lãi trên tổng"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Báo cáo chi tiết cho tôi biết theo chiến lược/khung thời gian; chỉ cần Net/DD; làm rõ bao nhiêu kỳ có lãi trên tổng, Source Nodes

### Community 97 - "Q: Nếu xét trên lãi lớn, bền và dd nhỏ, so sánh giữa db0 và db1, thì nên chọn combo theo khung nào?"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Nếu xét trên lãi lớn, bền và dd nhỏ, so sánh giữa db0 và db1, thì nên chọn combo theo khung nào?, Source Nodes

### Community 98 - "Q: Okie, với MA Cross thì tốt nhất trong các con xấu nhất?"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Okie, với MA Cross thì tốt nhất trong các con xấu nhất?, Source Nodes

### Community 99 - "Q: Okie, giờ tới MACD Break"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Okie, giờ tới MACD Break, Source Nodes

### Community 100 - "Q: Okie, giờ nêu tôi thực hiện 1 backtest đa symbol đa tiemframes và đa nguồn dữ liệu theo cachsn ày thì có triển khai được không: DB0 Combo H4; DB1 MA M30; DB0 MACD Break H4"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Okie, giờ nêu tôi thực hiện 1 backtest đa symbol đa tiemframes và đa nguồn dữ liệu theo cachsn ày thì có triển khai được không: DB0 Combo H4; DB1 MA M30; DB0 MACD Break H4, Source Nodes

### Community 101 - "MA Cross Source Query"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Vậy so sánh giữa D1 và Db0 của MA Cross M30, Source Nodes

### Community 102 - "Q: Tại sao lại đặt tên là shared, rồi shared_06, rồi shared_mix?"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Tại sao lại đặt tên là shared, rồi shared_06, rồi shared_mix?, Source Nodes

### Community 103 - "Q: Ok, vậy hãy chạy thử nhé, cũng 19 kỳ luôn"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Ok, vậy hãy chạy thử nhé, cũng 19 kỳ luôn, Source Nodes

### Community 104 - "Q: Giờ chạy trường hợp bỏ MA Cross ra nhé"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Giờ chạy trường hợp bỏ MA Cross ra nhé, Source Nodes

### Community 105 - "Q: Tôi muốn đọc lại kết quả của combo và macd break theo từng khung vào lệnh với db0 và db1"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Tôi muốn đọc lại kết quả của combo và macd break theo từng khung vào lệnh với db0 và db1, Source Nodes

### Community 106 - "Q: Triển khai DB1 Combo H1 H4 cùng MACD Break H3 H4 risk 0.6% một cụm lệnh, 19 kỳ"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Triển khai DB1 Combo H1 H4 cùng MACD Break H3 H4 risk 0.6% một cụm lệnh, 19 kỳ, Source Nodes

### Community 107 - "Q: Hãy thống kê xem nhóm lệnh của khung nào gây lỗ nhiều nhất"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Hãy thống kê xem nhóm lệnh của khung nào gây lỗ nhiều nhất, Source Nodes

### Community 108 - "Q: Nhóm nào cho DD ít mà lời vẫn cao trong ba kênh US30 DB1 Combo H4, MACD H3, MACD H4?"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Nhóm nào cho DD ít mà lời vẫn cao trong ba kênh US30 DB1 Combo H4, MACD H3, MACD H4?, Source Nodes

### Community 109 - "Q: Đó có là thiết kế tốt chưa?"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Đó có là thiết kế tốt chưa?, Source Nodes

### Community 110 - "Q: Single backtest và multi-backtest đang cấu hình ở đâu, prefix và tham số chạy là gì, multi một kênh có thành single không?"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Single backtest và multi-backtest đang cấu hình ở đâu, prefix và tham số chạy là gì, multi một kênh có thành single không?, Source Nodes

### Community 111 - "Q: Làm sao giữ trọn vẹn một luồng, chỉ config.yaml, truyền tham số linh động cho single và multi backtest mà không tạo YAML mới?"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Làm sao giữ trọn vẹn một luồng, chỉ config.yaml, truyền tham số linh động cho single và multi backtest mà không tạo YAML mới?, Source Nodes

### Community 112 - "Q: Kế hoạch hợp nhất single và multi backtest thành một luồng là gì?"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Kế hoạch hợp nhất single và multi backtest thành một luồng là gì?, Source Nodes

### Community 113 - "Q: Ok, giờ tôi muốn bạn chuyển sang sesssion backtest đa symbol, đa timeframes, đa chiến lược.  Hãy cho tôi biết session đó dang chạy thế nào, luồng ra sao, cách chạy?"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Ok, giờ tôi muốn bạn chuyển sang sesssion backtest đa symbol, đa timeframes, đa chiến lược.  Hãy cho tôi biết session đó dang chạy thế nào, luồng ra sao, cách chạy?, Source Nodes

### Community 114 - "Q: Ok, hãy tổng hợp lại xem bạn hiểu thế nào, đầy đủ ko, và dự kiến triển khai ra sao, thay đổi gì ở nhóm file nò?"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Ok, hãy tổng hợp lại xem bạn hiểu thế nào, đầy đủ ko, và dự kiến triển khai ra sao, thay đổi gì ở nhóm file nò?, Source Nodes

### Community 115 - "Q: Ok, đúng hết rồi nhé. Tuy nhiên chổ output sẽ cần làm thêm thế này, Kết quả theo từng kỳ là đúng rồi, nhưng chủ yếu cần Net Profit và DD. Bên cạnh đó nên có tổng lỗ tổng lãi, đóng góp của nhóm channel nào trong lãi hay trong lỗ. Hiểu y tôi ko? Phần output đó kas quan trọng trọng cách tôi quyết định kết hợp ra sao, chốt điểm này nưa rồi làm"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Ok, đúng hết rồi nhé. Tuy nhiên chổ output sẽ cần làm thêm thế này, Kết quả theo từng kỳ là đúng rồi, nhưng chủ yếu cần Net Profit và DD. Bên cạnh đó nên có tổng lỗ tổng lãi, đóng góp của nhóm channel nào trong lãi hay trong lỗ. Hiểu y tôi ko? Phần output đó kas quan trọng trọng cách tôi quyết định kết hợp ra sao, chốt điểm này nưa rồi làm, Source Nodes

### Community 116 - "Q: Ok, tôi cần bạn kiểm tra lại dashboard, nhiều cấu phần tiếng Anh và Việt lẫn lộn, hãy đồng nhất tiếng anh hết nhé!"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Ok, tôi cần bạn kiểm tra lại dashboard, nhiều cấu phần tiếng Anh và Việt lẫn lộn, hãy đồng nhất tiếng anh hết nhé!, Source Nodes

### Community 117 - "StrategyDef"
Cohesion: 0.50
Nodes (4): StrategyDef, Code, IsStopOrder, Token

### Community 118 - "BacktestBot.csproj"
Cohesion: 0.50
Nodes (3): net6.0, cTrader.Automate (*), Microsoft.NET.Sdk

### Community 119 - "EngineProfile"
Cohesion: 0.13
Nodes (5): EngineProfile, ConfigurationPlannerTests, argv_for(), spec_for(), FacilitatorTests

### Community 120 - "cTrader CLI Backtest and Optimize Research"
Cohesion: 0.67
Nodes (3): cTrader CLI Backtest and Optimize Research, Native CLI 5.10 Optimization, Single-Thread Backtests in a Process Pool

### Community 126 - "Q: Multi-channel dashboard v2 chạy, triển khai và đọc kết quả như thế nào?"
Cohesion: 0.50
Nodes (3): Answer, Outcome, Q: Multi-channel dashboard v2 chạy, triển khai và đọc kết quả như thế nào?

### Community 129 - "_specs"
Cohesion: 0.09
Nodes (11): OptimizeBox, _box(), _config(), _epoch_ms(), FakeOptimizeCli, OptimizeArgumentTests, validate(), PlanOptimizeBoxesTests (+3 more)

### Community 132 - "actions.py"
Cohesion: 0.10
Nodes (19): auth_options(), allocate_multi_channel_risk(), completed_execution_policy_available(), _enum_index(), _execution_key(), _multi_channel_preflight(), _multi_signal_key(), prepare_multi_channel_session() (+11 more)

### Community 133 - "trade_excursion.py"
Cohesion: 0.06
Nodes (28): _agree(), bar_duration_ms(), cache_roots(), check(), _forward_fill_zero(), _in_list(), _load_groups(), _load_signal_blobs() (+20 more)

### Community 147 - "sqlite3"
Cohesion: 0.22
Nodes (3): walkforward_progress(), historical_seconds_per_day(), walkforward_execution_status()

### Community 149 - "Q: Ok, tôi muốn kiểm tra điểm này, vì sao lại có folder này trong bo_workflow, lẽ a phải nằm trong ROBOTs thôi chứ: C:\Users\Administrator\Documents\cAlgo\Sources\Robots\bo_workflow\graphify-out"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Ok, tôi muốn kiểm tra điểm này, vì sao lại có folder này trong bo_workflow, lẽ a phải nằm trong ROBOTs thôi chứ: C:\Users\Administrator\Documents\cAlgo\Sources\Robots\bo_workflow\graphify-out, Source Nodes

## Knowledge Gaps
- **191 isolated node(s):** `cAlgo.Robots`, `Fib0618`, `Fib0786`, `Fib1000`, `Fib1272` (+186 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 887 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **47 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Work-memory lessons

**Preferred sources** — corroborated by past sessions; start here.
- `BacktestBot` (22× useful, score=20.20337825)
- `session_store.py` (7× useful, score=6.415018308) _(code changed — re-verify)_
- `prepare_multi_channel_session()` (4× useful, score=3.69998799) _(code changed — re-verify)_
- `RunSpec` (3× useful, score=2.760872537) _(code changed — re-verify)_
- `planner.py` (3× useful, score=2.741886164) _(code changed — re-verify)_
- `data_access.py` (2× useful, score=1.879748632) _(code changed — re-verify)_
- `multichannel/callbacks.py` (2× useful, score=1.879660834) _(code changed — re-verify)_
- `multichannel/views.py` (2× useful, score=1.879660834) _(code changed — re-verify)_
- `materialize_strategy_csv()` (2× useful, score=1.860747608) _(code changed — re-verify)_
- `facilitator.py` (2× useful, score=1.841262089) _(code changed — re-verify)_

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `RunSpec` connect `RunSpec` to `_specs`, `ProtocolStore`, `load_strategy_profile`, `MonteCarloOosTests`, `FacilitatorBoxTests`, `os`, `ReadoutObjectiveTests`, `test_core_engine.py`, `planner.py`, `ExperimentStore`, `_fake_result`, `EngineProfile`, `cli_runner.py`, `evidence.py`, `signal_trans.py`, `pipeline_lock.py`?**
  _High betweenness centrality (0.043) - this node is a cross-community bridge._
- **Are the 31 inferred relationships involving `RunSpec` (e.g. with `build_argv()` and `_check_period()`) actually correct?**
  _`RunSpec` has 31 INFERRED edges - model-reasoned connections that need verification._
- **What connects `cAlgo.Robots`, `Fib0618`, `Fib0786` to the rest of the system?**
  _191 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `session_store.py` be split into smaller, more focused modules?**
  _Cohesion score 0.052795031055900624 - nodes in this community are weakly interconnected._
- **Why does `load_strategy_profile()` connect `load_strategy_profile` to `run_name`, `require_profile`, `data_access.py`, `ProfileAndSourceContractTests`, `actions.py`, `load_engine_profile`, `test_core_engine.py`, `master_db.py`, `facilitator.py`, `with_signal_overrides`, `planner.py`, `test_dashboard_contracts.py`, `ChannelSourceTests`, `_fake_result`, `EngineProfile`, `walkforward.py`, `test_risk_allocation.py`?**
  _High betweenness centrality (0.027) - this node is a cross-community bridge._
- **Are the 3 inferred relationships involving `ExperimentStore` (e.g. with `_save_to_master_db()` and `RunSpec`) actually correct?**
  _`ExperimentStore` has 3 INFERRED edges - model-reasoned connections that need verification._
- **Should `require_profile` be split into smaller, more focused modules?**
  _Cohesion score 0.12169312169312169 - nodes in this community are weakly interconnected._