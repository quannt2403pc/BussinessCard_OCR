"""Client Management API của CLIProxy (auth-url, auth-files, get-auth-status, oauth-session).

Chủ sở hữu: Q | Task: 2.2 | xem Task.md

Hai ràng buộc đã kiểm chứng bằng container thật, đọc trước khi viết (Plan.md mục 2.4):

- **Không retry khi 401/403.** Sai management key 5 lần là CLIProxy ban IP 30 phút, mà cả
  container `api` dùng chung một IP → tự khoá mình. Chỉ retry lỗi mạng và 5xx. (I-05)
- **Trạng thái kết nối đọc từ `auth-files`, không phải `get-auth-status`.** Cái sau thiếu
  `state` thì trả `{"status":"ok"}` kể cả khi chưa đăng nhập bao giờ; nó chỉ dùng để poll
  trong lúc chờ người dùng đồng ý. (I-02)
"""

# TODO(Q, task 2.2): chưa triển khai.
