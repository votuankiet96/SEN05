# Research Notebook Flow

The notebooks form one linked research pipeline:

```text
core_python signals + OHLCV
          |
          v
01 Single Logic Run
   Validate one fixed execution configuration.
          |
          v
02 Stability Map
   Run a bounded coarse grid, then refine around stable candidates.
          |
          v
03 Walk-Forward
   Re-select parameters on each train window and test them OOS.
          |
          v
04 Monte Carlo
   Stress the combined OOS R-series from walk-forward.
          |
          v
05 Compare Versions
   Inspect run lineage and compare snapshots.
```

Each stage saves a snapshot with `run_id`, `run_type`, `parent_run_id`,
configuration, source signal hash, summary metrics, and output paths.

- Notebook 01 is an optional execution audit before optimization.
- Notebook 02 is the parent of notebook 03.
- Notebook 03 is the parent of notebook 04.
- Notebook 05 reads snapshots from every stage.
