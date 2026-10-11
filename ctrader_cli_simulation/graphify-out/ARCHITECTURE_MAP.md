# VM-BO20 Robots — code-derived architecture map

Phân tích ngày 2026-10-10. Code hiện tại là nguồn sự thật; Graphify là bản đồ điều hướng.

## Vai trò hệ thống

BO20 là workstation nghiên cứu và backtest. Hệ thống lấy trading signal từ Redis, đóng băng input thành CSV, lập kế hoạch thí nghiệm, chạy cTrader CLI hoặc Plugin API, chuẩn hóa bằng chứng và lưu kết quả để dashboard thực hiện grid, walk-forward, DSR, PBO và Monte Carlo.

Ba cBot nghiệp vụ nằm cùng workspace:

- `Combo/`: CSV-driven pending stop orders với Fibonacci SL/TP.
- `MA Cross/`: CSV-driven market orders với Fibonacci SL/TP.
- `MACD Break/`: CSV-driven market orders, exposure reversal và risk guards.

## Luồng end-to-end

```text
Dashboard / config
  -> planner: Experiment + Window + RunSpec
  -> signal_trans: Redis signal -> immutable CSV
  -> facilitator: fingerprint + claim + budget + dispatch
     -> CLI backtest từng spec
     -> CLI optimize theo OptimizeBox
     -> Plugin API qua filesystem mailbox
  -> evidence normalization
  -> ExperimentStore
  -> grid / walk-forward / DSR / PBO / Monte Carlo readout
  -> dashboard
```

## Module ownership

| Module | Trách nhiệm theo code |
|---|---|
| `dashboard/` | Session, command callbacks, progress polling và visualization. |
| `core_engine/configuration.py` | Strategy, Redis, engine và FTMO profiles; allowlist tham số optimize. |
| `core_engine/planner.py` | Experiment/windows/specs; grid expansion và `OptimizeBox` planning. |
| `core_engine/signal_trans.py` | Redis key/profile, signal retrieval, window filtering và deterministic CSV. |
| `core_engine/facilitator.py` | Orchestrator duy nhất biết các execution backend, claim/budget/retry/cache. |
| `core_engine/cli_runner.py` | CLI probing, backtest, optimize, watchdog, cancellation và result classification. |
| `api_engine/` | Filesystem bridge và cTrader Desktop plugin backend. |
| `core_engine/store.py` | Protocol budget, claims, immutable evidence, cache và exports. |
| `core_engine/output_util/` | Evidence parsing, pipeline locks, selection và readout. |
| `core_engine/optimize/` | Walk-forward, DSR, PBO/CSCV và Monte Carlo. |

## Optimize backend

`optimize_chunk_size=0` tắt đường mới. Khi backend là CLI và chunk size lớn hơn 1:

1. Planner chỉ gom các spec cùng execution context.
2. Những dimensions tạo đúng Cartesian product được chia thành boxes có kích thước giới hạn.
3. Facilitator chạy mỗi box bằng một lần đăng nhập `ctrader-cli optimize`.
4. Specs đã hoàn tất được bỏ qua; box không còn đầy đủ sẽ fallback về backtest từng spec.
5. CLI runner ghép pass về spec bằng bộ tham số, không phụ thuộc thứ tự output.
6. Evidence của từng pass được chuyển về contract `RunResult` và lưu như đường backtest cũ.

`optimize_chunk_size` được xem là execution-only config nên không làm thay đổi experiment lock.

## Graphify outputs

- `graph.html`: interactive graph.
- `CALLFLOW.html`: call-flow theo section.
- `GRAPH_REPORT.md`: topology, god nodes, connections và suggested questions.
- `graph.json`: dữ liệu graph để query trong session mới.
- `CHANGE_REPORT.md`: delta với baseline 2026-10-06.
- `HEALTH_REPORT.txt`: giới hạn toàn vẹn của graph.
