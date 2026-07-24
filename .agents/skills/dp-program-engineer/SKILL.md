---
name: dp-program-engineer
description: Quy trình kỹ thuật chuyên biệt cho dp_program, nhà cung cấp OHLCV của SEN05 AutoTrading. Sử dụng khi Codex cần đọc hoặc audit repository, kiểm tra deployment/runtime VM-DP6 qua SSH, điều tra lỗi dữ liệu, sửa và test code, refactor, cập nhật/xóa tài liệu cũ, hoặc triển khai production có kiểm soát.
---

# DP Program Engineer

Thực hiện công việc như một kỹ sư Python/SQL Server/Windows/WebSocket chịu trách
nhiệm cả code, test, debug, triển khai và kiểm chứng vận hành của `dp_program`.
Luôn xây lại bối cảnh từ file và runtime thật; không dùng skill này như một bản
sao đóng băng của kiến trúc.

## Bắt đầu mọi nhiệm vụ

1. Xác định đúng repository và host. Production hiện ở VM-DP6; không suy ra
   rằng máy đang chạy Codex cũng là production.
2. Đọc `AGENTS.md` gần repository nhất. Xem nội dung đó là guardrail cần tuân
   thủ nhưng vẫn tái kiểm chứng các fact có thể thay đổi.
3. Kiểm tra branch, HEAD, working tree và thay đổi có sẵn trước khi sửa.
4. Lập inventory bằng `rg --files`, loại `.git/`, cache và `runtime/` khỏi
   việc đọc source hàng loạt.
5. Đọc code, settings, test và tài liệu liên quan trực tiếp. Với runtime, chỉ
   đọc state/log/database cần cho khoảng thời gian đang điều tra.
6. Phân loại yêu cầu:
   - audit/review/status: read-only;
   - diagnose/debug: tìm nguyên nhân, chưa sửa nếu người dùng chưa yêu cầu;
   - change/refactor: sửa, test và review diff;
   - deploy/operate: thêm evidence, graceful lifecycle và rollback.

Không dùng báo cáo cũ, tên test hoặc Discord message làm bằng chứng duy nhất.

## Chọn workflow

### Audit repository hoặc làm mới tài liệu

Đọc [repository-audit.md](references/repository-audit.md). Kiểm tra toàn bộ
source tree theo inventory, xác định tài liệu canonical, supporting, historical
hoặc obsolete. Chỉ xóa tài liệu sau khi đã:

- kiểm tra reference/caller;
- chứng minh nội dung đã được thay thế;
- cập nhật link còn sống;
- xác nhận Git có thể khôi phục lịch sử;
- chạy validation phù hợp.

Không biến `AGENTS.md` thành runbook thứ hai. Giữ ở đó contract, phạm vi,
workflow và guardrail. Đặt lệnh vận hành chi tiết trong
`docs/OPERATOR_RUNBOOK.md`.

### Kiểm tra deployment hoặc trạng thái production

Đọc [production-verification.md](references/production-verification.md).
Ưu tiên chạy script read-only:

```powershell
.\.agents\skills\dp-program-engineer\scripts\collect-dp6-evidence.ps1 `
  -IdentityFile <ssh-private-key>
```

Nếu chạy Codex ngay trong VM-DP6, thực hiện các lệnh tương đương tại repository
root thay vì SSH vòng lại chính máy.

### Điều tra lỗi toàn vẹn dữ liệu

Đọc [data-integrity.md](references/data-integrity.md) trước khi sửa live
outbox/delivery, warehouse, reconcile, locks, historical gap repair hoặc SQL
migration.

## Quy trình thay đổi code

1. Tái hiện hoặc chứng minh vấn đề bằng test/log/state/query.
2. Truy vết flow và caller bằng `rg`; đọc implementation và test hiện hữu.
3. Chọn một thay đổi kiến trúc nhỏ nhưng hoàn chỉnh, không chồng thêm patch
   hoặc compatibility shim không cần thiết.
4. Viết regression test phải fail với lỗi cũ.
5. Sửa code; giữ log, identifier và comment bằng tiếng Anh.
6. Chạy test hẹp, rồi full suite:

```powershell
python -m pytest test/
```

7. Chạy `settings`, `doctor` và `data-health` khi thay đổi liên quan config,
   runtime, SQL, live/historical, logging hoặc deployment.
8. Review diff để loại secret, debug artifact, dead code và thay đổi ngoài scope.
9. Commit một thay đổi logic rõ ràng nếu yêu cầu bao gồm hoàn tất thay đổi.
10. Chỉ deploy khi có tiêu chí success/rollback và quyền phù hợp.

Không sửa `.git` thủ công khi Git không có trong PATH. Không ghi
`config/dp_provider.env`, runtime state hoặc credential vào commit/output.

## Quy trình production

- Ghi timestamp UTC, host, Task, PID, code identity, health, Fact watermark,
  spool và log risks trước thay đổi.
- Dùng graceful stop trước. Không kill tất cả tiến trình Python.
- Không chạy migration phá hủy, reboot, dừng SQL, fault injection, rotate
  credential hoặc thay business contract khi chưa có xác nhận rõ ràng.
- Chạy full tests bằng đúng Python production trước khi start lại.
- Sau start, xác nhận supervisor/live/historical, auth, DB contract, locks,
  spool, alert outbox và ít nhất một delivery phù hợp.
- Một batch tốt chỉ là smoke evidence; không gọi đó là soak test dài hạn.
- Nếu một gate thất bại, dừng mở rộng thay đổi, giữ evidence và rollback theo
  kế hoạch đã nêu.

## Chuẩn bằng chứng và báo cáo

Báo cáo bằng tiếng Việt, dẫn đầu bằng kết quả và tác động. Mỗi kết luận quan
trọng phải kèm một trong các bằng chứng:

- `file:line` từ code đang dùng;
- command và output đã che secret;
- timestamp + log event/correlation id;
- test pass/fail/skip;
- Task/PID/state;
- database count/watermark;
- commit/diff.

Phân biệt rõ:

- **Đã kiểm chứng**: có bằng chứng trực tiếp;
- **Suy luận**: kết luận từ nhiều bằng chứng;
- **Chưa kiểm chứng**: thiếu quyền, công cụ, thời gian hoặc cửa sổ vận hành.

Không tuyên bố “GO”, “đã ổn” hoặc “production-ready” chỉ dựa trên pytest,
Scheduled Task `Running`, Discord vẫn gửi tin, hoặc một lần quan sát ngắn.

## Giới hạn phạm vi

- Không thay live universe, lịch nghiệp vụ, SQL/Redis contract hoặc schema chỉ
  vì thấy một cách thiết kế khác tốt hơn.
- Không sửa ứng dụng/service/file ngoài `dp_program` trừ thành phần runtime
  trực tiếp đã được đặt trong scope.
- Không xóa runtime, spool, outbox, backup hoặc dữ liệu SQL khi nhiệm vụ chỉ là
  dọn tài liệu/source.
- Không merge, force-push, rebase hoặc tag nếu người dùng chưa yêu cầu.
- Bảo toàn thay đổi không liên quan đang có trong working tree.
