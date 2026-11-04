# Procurement Agent

AI Agent xử lý công việc mua hàng nội bộ của doanh nghiệp, trong một công ty giả lập. Xây dựng bằng LangGraph và MCP.

> **Trạng thái:** đã xong công ty giả lập, chưa có agent. Các phần dưới đây mô tả hệ thống đang hướng tới. Đây là dự án portfolio, không phải sản phẩm thương mại.

## Vấn đề

Mỗi khoản mua trong công ty đều phải qua nhiều bước: làm rõ yêu cầu, kiểm tra ngân sách, so sánh báo giá, trình đúng người duyệt, rồi tạo đơn hàng. Từng bước không khó, nhưng việc thì nhiều, thông tin nằm rải rác trong email và PDF, thời gian chủ yếu là chờ, và rất dễ sót quy định.

Phần mềm truyền thống khó xử lý những yêu cầu như "cần 10 màn hình như mấy cái cũ" hay một file PDF lạ, nên con người vẫn phải đọc, hỏi lại và nhập liệu.

[Xem ví dụ chi tiết về quy trình và các khó khăn](docs/PROBLEM.md)

## Giải pháp

Người dùng giao mục tiêu bằng tiếng Việt. Agent tự lập kế hoạch, chọn tool và xử lý phát sinh, không đi theo luồng cố định. Ví dụ:

- "Xử lý yêu cầu mua 10 màn hình của phòng IT"
- "Vì sao yêu cầu #12 bị treo 5 ngày?"

Agent lập hồ sơ và tạo bản nháp đơn hàng. Agent không có tool để phê duyệt, thanh toán hay gửi email ra ngoài công ty, nên các việc đó vẫn thuộc về con người.

## Trọng tâm kỹ thuật

- **Ranh giới cứng:** quy tắc và phép tính nằm trong code, không dựa vào prompt.
- **Chống prompt injection:** email và PDF đi qua một LLM cách ly và không được kích hoạt hành động quan trọng.
- **Con người duyệt:** agent dừng chờ quyết định và tiếp tục được cả khi chương trình đã tắt.
- **Đo được:** 80 kịch bản chấm bằng cách so sánh thay đổi trong database, đối chiếu với một workflow cố định.

**Công nghệ:** Python 3.12, LangGraph, MCP, Claude API, SQLite, Langfuse, FastAPI, Streamlit, pytest.

## Hạn chế

Agent chạy trên dữ liệu giả lập, không kết nối Gmail hay ERP thật, nên kết quả eval không đồng nghĩa với hiệu quả ở doanh nghiệp thật.

## Tài liệu

- [Vấn đề thực tế](docs/PROBLEM.md)
- [Thiết kế, kế hoạch và nhật ký quyết định](docs/AGENT_DESIGN.md)
