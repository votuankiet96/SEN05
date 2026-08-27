# Audit repository và làm mới tài liệu

## Mục tiêu

Xây lại mô tả hệ thống từ code và deployment hiện tại, giảm tài liệu trùng lặp,
và xóa tài liệu legacy mà không làm mất nguồn sự thật đang cần.

## 1. Chụp baseline

Tại repository root:

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
git log --oneline -10
rg --files -g '!runtime/**' -g '!.git/**' -g '!**/__pycache__/**'
```

Nếu Git không khả dụng, chưa xóa hoặc đổi tên tài liệu. Báo blocker thay vì
sửa metadata `.git`.

Ghi riêng:

- file tracked/untracked/modified;
- source/package layout;
- entrypoints;
- test inventory;
- scripts SQL/Windows;
- config template và settings owners;
- docs inventory;
- runtime inventory ở mức thư mục, không đọc hàng loạt binary/cache/spool.

## 2. Tái dựng kiến trúc từ code

Đọc tối thiểu:

- `pyproject.toml`, `README.md`;
- `src/core_engine/__main__.py`, CLI và entrypoint;
- `settings/system.py`, `settings/instruments.py`,
  `settings/operational.py`, `settings/internal.py`;
- composition roots live, historical và supervisor;
- TradingView auth/protocol;
- warehouse writer/reconcile và coordination locks;
- logkit/notify/health;
- test tương ứng với các flow trên.

Dùng import/caller thật để xác nhận owner và chiều phụ thuộc. Không tin cây thư
mục hoặc số dòng được ghi trong report cũ.

## 3. Kiểm định deployment/runtime

Đối chiếu code với:

- physical repository root và junction;
- Scheduled Task action/WorkingDirectory/account/triggers/restart policy;
- Python executable và package resolution;
- `settings --json`;
- `doctor --json`, `status --json`, `data-health --json`;
- process tree;
- bốn canonical logs;
- spool/outbox;
- DB contract và Fact watermark.

Đọc [production-verification.md](production-verification.md) cho evidence
matrix. Trạng thái runtime là snapshot có timestamp, không ghi nó thành fact
vĩnh viễn trong tài liệu kiến trúc.

## 4. Phân loại tài liệu

Gán mỗi file một trạng thái:

| Trạng thái | Ý nghĩa | Hành động |
|---|---|---|
| Canonical | Nguồn sự thật hiện hành | Cập nhật, tránh trùng lặp |
| Supporting | Giải thích sâu cho một chủ đề còn sống | Giữ nếu được canonical doc dẫn tới |
| Historical | Bằng chứng audit/decision cũ | Chuyển sang Git history hoặc archive có chủ đích |
| Obsolete | Sai, superseded, không còn reference | Xóa sau khi kiểm chứng |

Bộ canonical mục tiêu nên nhỏ:

- `README.md`: mục đích, setup và lệnh phổ biến;
- `AGENTS.md`: contract, workflow và guardrail cho coding agent;
- `docs/ARCHITECTURE.md`: cấu trúc và data/control flow hiện tại;
- `docs/OPERATOR_RUNBOOK.md`: thao tác production chi tiết;
- `docs/LOGGING_ARCHITECTURE.md`: logging/alerting hiện tại;
- `docs/ENGINEERING_DECISIONS.md`: chỉ tạo nếu cần lưu các quyết định bền vững.

Các file có tên audit/report/proposal/discussion/refactor là ứng viên historical
hoặc obsolete, không mặc định là canonical. Kiểm tra từng file và mọi reference
trước khi xóa.

## 5. Nguyên tắc cập nhật

- Mỗi fact chỉ có một owner; file khác link tới owner thay vì copy.
- Không đóng cứng PID, row count, freshness, test count hoặc “current status”.
- Đường dẫn deployment phải phân biệt physical root với junction/working root.
- Scheduled Task chi tiết nằm trong runbook; `AGENTS.md` chỉ giữ identity và
  guardrail cần cho agent.
- Xóa số dòng và số lượng file dễ lỗi thời nếu chúng không phục vụ kiểm chứng.
- Tất cả command phải chạy được với layout hiện hành.
- Tài liệu operator dùng ngôn ngữ dễ hiểu; code/log/identifier giữ tiếng Anh.

## 6. Xóa legacy có kiểm soát

Trước mỗi nhóm xóa:

```powershell
rg -n "<filename-or-heading>" . -g '!runtime/**' -g '!.git/**'
git log --all -- <path>
```

Sau khi xóa:

- cập nhật mọi link;
- chạy `rg` tìm tên/path/claim cũ;
- chạy test phù hợp, tối thiểu full pytest nếu tài liệu mô tả command/entrypoint;
- review `git diff --stat` và `git diff`;
- commit docs cleanup riêng với danh sách file đã supersede.

Không xóa SQL migration đã deploy, runbook đang dùng, config template, test
evidence bắt buộc hoặc runtime data trong workflow dọn tài liệu.

## 7. Bàn giao

Báo cáo:

- baseline branch/HEAD/working tree;
- sơ đồ kiến trúc đã tái kiểm chứng;
- snapshot deployment/runtime có timestamp;
- bảng keep/update/delete cho từng doc;
- claim cũ đã sửa;
- file đã xóa và nơi thông tin còn sống;
- test/validation;
- rủi ro hoặc quyết định còn thiếu.
