"""redis_engine — realtime watcher/notify layer for OG, separate from og_core.

Vai trò (khung sườn, logic bên trong chưa viết):
    Theo dõi bar mới đóng (qua Redis pub/sub "bar_ready:{symbol}:{tf}" từ
    DP6, có fallback poll), gọi lại og_core.engine/og_core.strategies để
    tính tín hiệu (không tính toán lại logic chiến lược ở đây), lọc tín hiệu
    nào thực sự mới (dedup), rồi giao tin cậy cho hệ downstream "OF" qua
    Redis Streams (XADD).

Ranh giới:
    - redis_engine PHỤ THUỘC og_core (import engine/strategies/data), không
      ngược lại — og_core không được biết gì về redis_engine.
    - Mọi dependency riêng của package này (redis, requests cho Telegram/
      Discord...) khai báo ở pyproject.toml dưới extra "watcher", không lẫn
      vào dependency mặc định của og_core.
"""
