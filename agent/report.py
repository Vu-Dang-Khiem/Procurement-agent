"""Kiểm tra báo cáo cuối của agent trước khi đồ thị dừng."""

from agent.tool_specs import MAX_SUMMARY_LENGTH, REPORT_STATUSES, REPORT_TOOL
from mcp_servers.common import error


def check_report(calls: list[dict], pending_approvals: list[int], pending_replies: list[int]) -> tuple[dict | None, dict]:
    """Trả về (báo cáo hợp lệ hoặc None, kết quả gửi lại cho agent theo hợp đồng tool)."""
    if len(calls) != 1:
        return None, error(
            "validation",
            f"{REPORT_TOOL} phải là lời gọi duy nhất trong lượt",
            "Hãy gọi các tool khác trước, rồi nộp báo cáo ở lượt sau",
        )
    args = calls[0]["args"]
    status = args.get("status")
    summary = str(args.get("summary", "")).strip()
    if status not in REPORT_STATUSES:
        return None, error("validation", f"status {status!r} không hợp lệ", f"Chọn một trong: {', '.join(REPORT_STATUSES)}")
    if not summary or len(summary) > MAX_SUMMARY_LENGTH:
        return None, error("validation", f"summary phải có nội dung và không quá {MAX_SUMMARY_LENGTH} ký tự")
    if status == "waiting_approval" and not pending_approvals:
        return None, error(
            "validation",
            "Không có yêu cầu nào đang chờ duyệt trong nhiệm vụ này",
            "Chỉ dùng waiting_approval sau khi submit_for_approval thành công",
        )
    if status == "waiting_reply" and not pending_replies:
        return None, error(
            "validation",
            "Không có câu hỏi nào đang chờ trả lời trong nhiệm vụ này",
            "Chỉ dùng waiting_reply sau khi ask_requester thành công",
        )
    report = {"status": status, "summary": summary, "request_ids": list(args.get("request_ids") or [])}
    return report, {"ok": True, "data": {"accepted": True}, "truncated": False}
