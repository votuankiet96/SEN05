---
name: check-code
description: Kiểm tra chất lượng code bằng Ruff lint và format, chạy tests nếu có, hướng dẫn tạo Pull Request. TRIGGER khi user nói: "check code", "kiểm tra code", "lint", "format code", "tạo PR", "tạo pull request", "commit", "push code", "lỗi code", "code có ổn không", "review code", "xong code rồi", "sửa xong rồi", "check lỗi", "create PR", "make PR", "push changes".
---

# Skill: /check-code

Kiểm tra chất lượng code và hướng dẫn tạo Pull Request.

## Khi nào dùng

Sau khi AI viết code xong, trước khi lưu lại thay đổi.

## Các bước thực hiện

### Bước 1: Chạy Ruff lint (tự động sửa lỗi nhỏ)

```bash
cd /d/Project/SEN05
python -m ruff check . --fix
```

- Đếm số lỗi đã tự sửa và số lỗi còn lại
- Nếu còn lỗi không tự sửa được → liệt kê và giải thích ngắn gọn bằng tiếng Việt, rồi tự fix

### Bước 2: Chạy Ruff format (tự động format code)

```bash
python -m ruff format .
```

- Ghi nhận số files đã format lại

### Bước 3: Chạy tests (nếu có)

```bash
python -m pytest tests/ -v --tb=short 2>/dev/null || echo "Chưa có tests hoặc tests thất bại"
```

- Nếu có tests → chạy và báo kết quả
- Nếu chưa có tests → bỏ qua, ghi nhận

### Bước 4: Tóm tắt thay đổi

```bash
git diff --stat
git diff --name-only
```

- Liệt kê files đã thay đổi, số dòng thêm/xóa

### Bước 5: Hướng dẫn tạo PR

Kiểm tra trạng thái git:
- Nếu đang ở `main` → tạo branch mới với tên mô tả (ví dụ: `fix/sharpe-calculation`, `feat/data-check`)
- Nếu đã ở branch riêng → tiếp tục trên branch đó

Sau đó:
1. `git add` các files đã thay đổi (CHỈ files liên quan, không add .env hay credentials)
2. `git commit` với message mô tả rõ ràng bằng tiếng Việt hoặc tiếng Anh
3. `git push -u origin <branch-name>`
4. Tạo PR bằng `gh pr create` với title ngắn gọn và body mô tả thay đổi

### Bước 6: Báo cáo kết quả

Trình bày kết quả bằng tiếng Việt, format đơn giản:

```
=== KIỂM TRA CODE ===

1. Lint:      ✅/❌ (X lỗi đã sửa, Y lỗi còn lại)
2. Format:    ✅ (X files đã format)
3. Tests:     ✅/⚠️/❌ (kết quả hoặc "chưa có tests")
4. Thay đổi:  X files

=== TẠO PR ===
✅ Branch: <tên-branch>
✅ Commit: "<message>"
✅ PR: <link PR>

→ Vào link PR để review và merge khi sẵn sàng
```

## Lưu ý quan trọng

- KHÔNG commit trực tiếp vào main
- KHÔNG add files `.env`, credentials, hay files chứa secrets
- KHÔNG push force
- Nếu ruff phát hiện lỗi không tự fix được → sửa code trước, rồi chạy lại
