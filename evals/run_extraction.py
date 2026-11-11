"""Chạy LLM cách ly bằng model thật trên email và PDF mẫu, rồi chấm theo đáp án. Lệnh này TỐN TIỀN API.

Chạy: python -m evals.run_extraction --limit 2
Dừng lại khi chạm --max-usd. Kết quả được ghi vào evals/reports/.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from agent.model import estimate_cost
from evals.extraction_answers import EMAIL_ANSWERS, QUOTE_ANSWERS
from evals.grade_extraction import EMAIL_ITEM_FIELDS, QUOTE_FIELDS, Grade, Summary, grade_email, grade_quote
from quarantine.extract import extract_email, extract_quote, load_catalog
from quarantine.model import ClaudeModel, model_id_for
from rules.errors import RuleError
from simulator.emails import EMAILS
from simulator.quotes import DATA_DIR, source_file
from simulator.seed import build_database

REPORT_DIR = Path(__file__).with_name("reports")


def cost_of(model: ClaudeModel) -> float:
    return estimate_cost(model.model_id, model.usage.input_tokens, model.usage.output_tokens)


def _email_samples(model, catalog):
    for email in EMAILS:
        answer = EMAIL_ANSWERS[email.id]
        yield (
            email.id,
            lambda email=email: extract_email(model, catalog, email.sender, email.received_at, email.subject, email.body),
            lambda output, answer=answer: grade_email(output, answer),
            1 + 1 + len(answer["items"]) * len(EMAIL_ITEM_FIELDS),
            answer["suspicious_content"],
        )


def _quote_samples(model, catalog):
    for quotation_id, answer in QUOTE_ANSWERS.items():
        yield (
            f"q{quotation_id:03d}",
            lambda quotation_id=quotation_id: extract_quote(model, catalog, DATA_DIR / source_file(quotation_id)),
            lambda output, answer=answer: grade_quote(output, answer),
            len(QUOTE_FIELDS),
            answer["suspicious_content"],
        )


def _run_group(name, samples, model, limit, max_usd, records):
    summary = Summary()
    for sample_id, extract, grade_output, fields_total, flag_expected in samples:
        if limit is not None and len(records) >= limit:
            break
        if cost_of(model) >= max_usd:
            print(f"Dừng vì đã chạm mức {max_usd} USD")
            break
        try:
            output = extract()
        except RuleError as error:
            summary.add_failure(fields_total, flag_expected)
            records.append({"id": sample_id, "error": error.message})
            print(f"{sample_id} LỖI {error.message}", flush=True)
            continue
        grade: Grade = grade_output(output)
        summary.add(grade)
        records.append({"id": sample_id, "output": output, "mismatches": grade.mismatches, "flag": grade.flag_got})
        status = "ĐÚNG" if grade.correct else "SAI "
        print(f"{sample_id} {status} {'; '.join(grade.mismatches)}", flush=True)
        if grade.flag_expected != grade.flag_got:
            print(f"     cờ nghi ngờ: {grade.flag_got}, đáp án {grade.flag_expected}", flush=True)
    print(
        f"{name}: {summary.samples_correct}/{summary.samples} mẫu đúng hoàn toàn; "
        f"sai {summary.fields_wrong}/{summary.fields_total} trường; "
        f"cờ nghi ngờ: bỏ sót {summary.flags_missed}/{summary.flags_expected}, báo nhầm {summary.flags_false}\n"
    )
    return summary


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="Chỉ chạy N mẫu đầu tiên")
    parser.add_argument("--max-usd", type=float, default=0.25, help="Dừng khi chi phí chạm mức này")
    parser.add_argument("--model", default=None, help="Mặc định lấy vai trò quarantine trong config/models.yaml")
    args = parser.parse_args()

    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Chưa có ANTHROPIC_API_KEY. Hãy chép .env.example thành .env rồi điền key.")

    model = ClaudeModel(args.model or model_id_for("quarantine"))
    conn = build_database()
    catalog = load_catalog(conn)
    conn.close()

    records: list[dict] = []
    emails = _run_group("Email", _email_samples(model, catalog), model, args.limit, args.max_usd, records)
    quotes = _run_group("Báo giá", _quote_samples(model, catalog), model, args.limit, args.max_usd, records)

    usage = model.usage
    print(f"{usage.calls} lần gọi, {usage.input_tokens} token vào, {usage.output_tokens} token ra, {cost_of(model):.4f} USD")
    REPORT_DIR.mkdir(exist_ok=True)
    report = REPORT_DIR / f"extraction_{model.model_id}.json"
    report.write_text(
        json.dumps(
            {
                "model": model.model_id,
                "usage": vars(usage),
                "cost_usd": round(cost_of(model), 4),
                "emails": vars(emails),
                "quotes": vars(quotes),
                "records": records,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(f"Đã ghi {report}")


if __name__ == "__main__":
    main()
