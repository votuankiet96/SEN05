# Hướng dẫn làm việc với `dp_program`

File này áp dụng cho toàn bộ repository `dp_program`, nhà cung cấp dữ liệu
OHLCV cho SEN05 AutoTrading. Mục tiêu là giúp mọi coding agent làm đúng phạm
vi, bảo toàn dữ liệu và báo cáo bằng chứng vận hành rõ ràng.

## Mục tiêu hệ thống

`dp_program`:

- lấy dữ liệu OHLCV từ TradingView;
- chạy live 24/7 và historical theo lịch;
- validate, ghi staging SQL Server và nạp `DWH.Fact_OHLCV`;
- dùng SQL Server làm nguồn dữ liệu bền vững;
- giám sát health, log và cảnh báo Discord;
- có Redis/OG snapshot tùy chọn nhưng không dùng Redis làm nguồn sự thật.

Ưu tiên cao nhất là không làm mất, lặp hoặc làm sai dữ liệu giao dịch.

## Phạm vi production

- Production host: VM-DP6.
- Physical repository root: `C:\Users\Administrator\Desktop\dp_program`.
- Scheduled Task identity: `\SEN05\SEN05 DP Program 24x7`.
- Scheduled Task command family: Python `-m core_engine run --live`.
- Scheduled Task WorkingDirectory có thể là junction `C:\Share\dp_program`;
  phải tự xác minh trước khi dùng làm bằng chứng.
- Đây là Scheduled Task deployment, không mặc định là Windows Service/NSSM.

Chỉ làm việc trong repository này và các thành phần trực tiếp phục vụ nó:
Scheduled Task nêu trên, process Python của `dp_program`, runtime bên trong
repository và database `SEN05_AutoTrading` khi cần kiểm chứng read-only.

Không sửa ứng dụng, service, database, schema, credential hoặc OS setting không
liên quan. Không reboot VM-DP6, không dừng SQL Server, không đổi contract nghiệp
vụ nếu chưa được operator xác nhận rõ.

## Nguồn sự thật

Khi thông tin mâu thuẫn, ưu tiên theo thứ tự:

1. Code và test hiện tại trong repository.
2. Runtime thật trên VM-DP6 có timestamp: Task, process, state, log, DB.
3. `settings/system.py` và `settings/instruments.py`.
4. `config/dp_provider.env` đã che secret.
5. Canonical docs: `README.md`, `docs/ARCHITECTURE.md`,
   `docs/OPERATOR_RUNBOOK.md`, `docs/LOGGING_ARCHITECTURE.md`,
   `docs/ENGINEERING_DECISIONS.md`.
6. Git history cho báo cáo/audit/proposal cũ.
7. Chat history hoặc memory chỉ là gợi ý, không phải bằng chứng.

Không tin số dòng, số file, số test, PID, watermark, trạng thái `Running` hoặc
claim "current" trong tài liệu cũ nếu chưa grep/chạy lệnh lại.

## Contract không tự đổi

- Tổng universe: 37 symbols, 15 direct timeframes.
- Live: 11 symbols thuộc `Indice`, `Metal`, `Crypto`.
- FOREX chỉ chạy historical.
- Live batch 5 phút và chỉ lưu nến đã đóng.
- Historical schedule mặc định: `11:00,22:00` UTC.
- SQL Server `SEN05_AutoTrading` là durable source of truth.
- `DWH.usp_LoadDirect` phải đúng contract version mà code yêu cầu.
- `SEN.ActiveTask` phải có OwnerId/Fence lock fencing.
- Production storage contract là `sql`.
- Redis/OG, nếu được bật bởi release đã review, chỉ là eventual-consistent
  candle snapshot.
- Không tự mở rộng `DWH.Dim_Date`, đổi live universe, đổi lịch nghiệp vụ,
  rotate credential/webhook, merge main, push hoặc tag.

Nếu yêu cầu đòi đổi contract, dừng ở phần đó và trình bày tác động dữ liệu,
migration, rollback và quyết định cần Kiệt xác nhận.

## Workflow bắt buộc

Trước mọi thay đổi:

1. Đọc `AGENTS.md`, skill/references liên quan và code thật.
2. Chạy baseline Git: `git status --short`, `git branch --show-current`,
   `git rev-parse HEAD`, `git log --oneline -10`.
3. Bảo toàn mọi thay đổi có sẵn của người dùng.
4. Dùng `rg`/caller thật để kiểm chứng trước khi xóa hoặc đổi tên.
5. Với runtime, thu evidence read-only trước; không restart engine cho thay đổi
   chỉ là tài liệu.

Nếu Git không khả dụng, không sửa `.git`, không xóa tài liệu, và báo rõ blocker.

## Testing tối thiểu

Từ repository root:

```powershell
python -m pytest test/
python -m core_engine settings --json
python -m core_engine doctor --json
python -m core_engine data-health --json
```

Thêm `python -m core_engine status --json` và log queries khi cần bằng chứng
runtime. Unit test pass không chứng minh production đang giao dữ liệu; phải
đối chiếu batch, Fact watermark, spool, locks, logs và alert outbox.

## Deployment guardrails

Trước thao tác có thể gây gián đoạn, báo operator:

- commit/diff sẽ triển khai;
- lý do và rủi ro;
- có dừng Scheduled Task hay không;
- tiêu chí thành công;
- rollback.

Không dùng `Stop-Process -Name python`, không kill hàng loạt. Nếu phải dừng,
dùng CLI graceful trước và chỉ force đúng PID sau khi đã xác minh process tree.
Không restart engine cho thay đổi tài liệu hoặc skill repo-scoped.

## Khu vực rủi ro cao

Đặc biệt cẩn trọng khi sửa:

- `core/live/outbox.py`;
- `core/live/delivery.py`;
- `shared/warehouse/writer.py`;
- `shared/warehouse/maintenance.py`;
- `shared/warehouse/reconcile.py`;
- `util/coordination/locks.py`;
- migration trong `scripts/sql/`.

Spool/outbox chỉ ack sau khi Fact commit đã được xác nhận. Lock phải giữ
OwnerId/Fence semantics. Không purge staging chỉ vì ETL đã được gọi; phải chứng
minh Fact có dữ liệu tương ứng.

## Logging và bảo mật

Chỉ có bốn active text logs:

- `runtime/logs/live.log`;
- `runtime/logs/historical.log`;
- `runtime/logs/system.log`;
- `runtime/logs/alerts.log`.

Mọi module log qua `core_engine.util.logkit`; không tự tạo `FileHandler` hoặc
log nghiệp vụ riêng. Không in, commit hoặc paste `config/dp_provider.env`,
token, cookie, password, webhook, backup hoặc runtime spool/outbox.

## Git và tài liệu

- Không dùng `git reset --hard`, discard, rebase, merge, tag hoặc push nếu chưa
  được yêu cầu.
- Mỗi commit nên là một thay đổi logic rõ ràng.
- Không tạo folder archive cho report cũ chỉ để giữ lịch sử; Git history là
  nơi lưu audit/proposal/discussion đã superseded.
- Không xóa file vì tên có vẻ cũ; phải grep reference, xác minh nội dung đã
  chuyển sang canonical docs và review diff.

## Báo cáo cho operator

Báo cáo bằng tiếng Việt, dẫn đầu bằng kết quả và tác động vận hành. Mỗi kết
luận quan trọng cần có bằng chứng: command output, file:line, timestamp log,
test result, Task/PID/state, DB measurement hoặc commit/diff.

Phân biệt rõ:

- Đã kiểm chứng.
- Suy luận từ bằng chứng.
- Chưa kiểm chứng hoặc còn rủi ro.
