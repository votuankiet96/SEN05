"""redis_engine — realtime signal layer for OG, separate from og_core.

Vai trò:
    Nhận sự kiện nến mới từ DP6 qua Redis Streams (không phải pub/sub — xem
    lý do trong tài liệu thiết kế: Streams không mất tin khi OG offline tạm
    thời), gọi lại og_core.engine/og_core.strategies để tính tín hiệu (không
    viết lại logic chiến lược ở đây), lọc tín hiệu nào thực sự mới (dedup),
    rồi publish tín hiệu mới lên Redis Streams cho hệ downstream "OF".

Ranh giới:
    - redis_engine PHỤ THUỘC og_core (import engine/strategies/data), không
      ngược lại — og_core không được biết gì về redis_engine.
    - Dependency riêng của package này (redis) khai báo ở pyproject.toml
      dưới extra "watcher", không lẫn vào dependency mặc định của og_core.
"""
