#!/usr/bin/env python3
"""Bulk downloader for Korea Meteorological Administration API Hub time-range APIs."""

from __future__ import annotations

import argparse
import calendar
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from kma_api_test import (
    DEFAULT_BASE_URL,
    build_url,
    decode_body,
    fetch,
    load_params,
    mask_auth_key,
    save_output,
)

TIMESTAMP_FORMAT = "%Y%m%d%H%M"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="긴 기간의 기상청 API 허브 데이터를 구간별로 나눠 다운로드합니다.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "예시:\n"
            "  export KMA_AUTH_KEY=\"발급받은인증키\"\n"
            "  python3 kma_bulk_download.py \\\n"
            "    --endpoint typ01/url/kma_sfctm3.php \\\n"
            "    --start 201501010000 \\\n"
            "    --end 202512312300 \\\n"
            "    --chunk-months 1 \\\n"
            "    --param stn=108 \\\n"
            "    --param help=0 \\\n"
            "    --param disp=1 \\\n"
            "    --output-dir downloads/asos_108_chunks \\\n"
            "    --merged-output downloads/asos_108_2015_2025.txt"
        ),
    )
    parser.add_argument(
        "--endpoint",
        required=True,
        help="`typ01/url/kma_sfctm3.php` 같은 상대 경로 또는 전체 URL",
    )
    parser.add_argument(
        "--auth-key",
        help="기상청 API 허브 authKey. 없으면 KMA_AUTH_KEY 환경변수를 사용합니다.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"상대 경로 엔드포인트에 붙일 기본 URL (기본값: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="고정 쿼리 파라미터. 여러 번 반복해서 사용할 수 있습니다.",
    )
    parser.add_argument(
        "--param-file",
        help="고정 쿼리 파라미터가 담긴 JSON 파일 경로",
    )
    parser.add_argument(
        "--start",
        required=True,
        help="시작 시각 (yyyymmddhhmm)",
    )
    parser.add_argument(
        "--end",
        required=True,
        help="종료 시각 (yyyymmddhhmm)",
    )
    parser.add_argument(
        "--start-param",
        default="tm1",
        help="시작 시각 파라미터 이름 (기본값: tm1)",
    )
    parser.add_argument(
        "--end-param",
        default="tm2",
        help="종료 시각 파라미터 이름 (기본값: tm2)",
    )
    chunk_group = parser.add_mutually_exclusive_group()
    chunk_group.add_argument(
        "--chunk-months",
        type=int,
        default=1,
        help="몇 개월 단위로 나눌지 지정합니다. 기본값은 1개월입니다.",
    )
    chunk_group.add_argument(
        "--chunk-days",
        type=int,
        help="일 단위로 나눌 때 사용합니다.",
    )
    parser.add_argument(
        "--output-dir",
        help="각 구간의 응답 파일을 저장할 디렉터리",
    )
    parser.add_argument(
        "--merged-output",
        help="구간별 텍스트 응답을 하나의 UTF-8 파일로 합쳐 저장할 경로",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="요청 타임아웃(초). 기본값은 30초입니다.",
    )
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=0.0,
        help="요청 사이에 잠시 대기할 초 단위 시간. 기본값은 0입니다.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="요청 실패 시 재시도 횟수. 기본값은 3입니다.",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=5.0,
        help="재시도 전 대기 시간(초). 기본값은 5초입니다.",
    )
    parser.add_argument(
        "--encoding",
        help="응답 디코딩 인코딩을 강제로 지정합니다. 예: utf-8, cp949",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="일부 구간에서 실패해도 다음 구간 다운로드를 계속합니다.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="이미 저장된 정상 청크 파일은 재다운로드하지 않고 재사용합니다.",
    )
    return parser


def parse_timestamp(value: str) -> datetime:
    try:
        return datetime.strptime(value, TIMESTAMP_FORMAT)
    except ValueError as exc:
        raise ValueError(f"`{value}` 는 yyyymmddhhmm 형식이 아닙니다.") from exc


def format_timestamp(value: datetime) -> str:
    return value.strftime(TIMESTAMP_FORMAT)


def add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def iter_ranges(start: datetime, end: datetime, chunk_months: int | None, chunk_days: int | None) -> list[tuple[datetime, datetime]]:
    ranges: list[tuple[datetime, datetime]] = []
    cursor = start

    while cursor <= end:
        if chunk_days is not None:
            next_cursor = cursor + timedelta(days=chunk_days)
        else:
            next_cursor = add_months(cursor, chunk_months or 1)

        chunk_end = min(end, next_cursor - timedelta(minutes=1))
        ranges.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(minutes=1)

    return ranges


def split_kma_text(text: str) -> tuple[list[str], list[str], list[str]]:
    header_lines: list[str] = []
    data_lines: list[str] = []
    footer_lines: list[str] = []
    seen_data = False

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            continue

        if line.startswith("#7777END"):
            footer_lines.append(line)
            continue

        if line.startswith("#") and not seen_data:
            header_lines.append(line)
            continue

        if line.startswith("#"):
            footer_lines.append(line)
            continue

        seen_data = True
        data_lines.append(line)

    return header_lines, data_lines, footer_lines


def is_kma_text(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        return stripped == "#START7777"
    return False


def write_lines(path: Path, lines: list[str], append: bool) -> None:
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8") as file:
        if not lines:
            return
        file.write("\n".join(lines))
        file.write("\n")


def merge_chunk_text(
    merged_output: Path,
    text: str,
    merged_started: bool,
    merged_footer: list[str],
) -> tuple[bool, list[str]]:
    header_lines, data_lines, footer_lines = split_kma_text(text)
    if not merged_started:
        write_lines(merged_output, header_lines, append=False)
        merged_started = True
        if footer_lines:
            merged_footer = footer_lines

    if data_lines:
        write_lines(merged_output, data_lines, append=True)

    return merged_started, merged_footer


def should_retry(status_code: int) -> bool:
    return status_code >= 500 or status_code in {408, 429}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    auth_key = args.auth_key or os.getenv("KMA_AUTH_KEY")
    if not auth_key:
        parser.error("`--auth-key` 또는 `KMA_AUTH_KEY` 환경변수 중 하나는 반드시 필요합니다.")

    if not args.output_dir and not args.merged_output:
        parser.error("`--output-dir` 또는 `--merged-output` 중 하나는 반드시 지정해야 합니다.")
    if args.skip_existing and not args.output_dir:
        parser.error("`--skip-existing` 을 쓰려면 `--output-dir` 이 필요합니다.")

    try:
        start = parse_timestamp(args.start)
        end = parse_timestamp(args.end)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    if start > end:
        print("시작 시각이 종료 시각보다 늦습니다.", file=sys.stderr)
        return 2

    if args.chunk_days is not None and args.chunk_days <= 0:
        print("`--chunk-days` 는 1 이상이어야 합니다.", file=sys.stderr)
        return 2

    if args.chunk_months is not None and args.chunk_months <= 0:
        print("`--chunk-months` 는 1 이상이어야 합니다.", file=sys.stderr)
        return 2
    if args.retries < 0:
        print("`--retries` 는 0 이상이어야 합니다.", file=sys.stderr)
        return 2

    try:
        params = load_params(args.param, args.param_file)
    except Exception as exc:
        print(f"파라미터 로딩 실패: {exc}", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir) if args.output_dir else None
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    merged_output = Path(args.merged_output) if args.merged_output else None
    if merged_output:
        merged_output.parent.mkdir(parents=True, exist_ok=True)
        if merged_output.exists():
            merged_output.unlink()

    ranges = iter_ranges(start, end, args.chunk_months, args.chunk_days)
    print(f"전체 구간 수: {len(ranges)}")

    merged_started = False
    merged_footer = ["#7777END"]
    failure_count = 0

    for index, (chunk_start, chunk_end) in enumerate(ranges, start=1):
        chunk_params = dict(params)
        chunk_params[args.start_param] = format_timestamp(chunk_start)
        chunk_params[args.end_param] = format_timestamp(chunk_end)

        url = build_url(args.base_url, args.endpoint, chunk_params, auth_key)
        chunk_path = None
        if output_dir:
            chunk_path = output_dir / (
                f"{index:03d}_{format_timestamp(chunk_start)}_{format_timestamp(chunk_end)}.txt"
            )

        print(f"\n[{index}/{len(ranges)}] {format_timestamp(chunk_start)} ~ {format_timestamp(chunk_end)}")
        print(f"요청 URL: {mask_auth_key(url)}")

        if args.skip_existing and chunk_path and chunk_path.exists():
            existing_text = chunk_path.read_text(encoding="utf-8")
            if is_kma_text(existing_text):
                print(f"기존 청크 재사용: {chunk_path}")
                if merged_output:
                    merged_started, merged_footer = merge_chunk_text(
                        merged_output,
                        existing_text,
                        merged_started,
                        merged_footer,
                    )
                continue

            print(f"기존 청크 무시: {chunk_path} (정상 KMA 응답이 아님)")

        last_exception = None
        for attempt in range(args.retries + 1):
            try:
                status_code, headers, body = fetch(url, args.timeout)
            except Exception as exc:
                last_exception = exc
                if attempt < args.retries:
                    print(
                        f"요청 실패: {exc} / {args.retry_delay}초 후 재시도 ({attempt + 1}/{args.retries})",
                        file=sys.stderr,
                    )
                    time.sleep(args.retry_delay)
                    continue

                failure_count += 1
                print(f"요청 실패: {exc}", file=sys.stderr)
                if not args.continue_on_error:
                    return 2
                break

            content_type = headers.get("Content-Type", "unknown")
            text, encoding_label = decode_body(content_type, args.encoding, body)
            if should_retry(status_code) and attempt < args.retries:
                print(
                    f"HTTP 상태 {status_code} / {args.retry_delay}초 후 재시도 ({attempt + 1}/{args.retries})",
                    file=sys.stderr,
                )
                time.sleep(args.retry_delay)
                continue

            last_exception = None
            break
        else:
            continue

        if last_exception is not None:
            continue

        print(f"HTTP 상태: {status_code}")
        print(f"Content-Type: {content_type}")
        print(f"응답 크기: {len(body)} bytes")
        if text is not None:
            print(f"디코딩: {encoding_label}")

        if chunk_path:
            save_target = chunk_path if text is not None else chunk_path.with_suffix(".bin")
            chunk_path, save_mode = save_output(str(save_target), body, text)
            print(f"청크 저장: {chunk_path} ({save_mode})")

        if status_code >= 400:
            failure_count += 1
            if not args.continue_on_error:
                return 1
            continue

        if merged_output:
            if text is None:
                print("텍스트 응답이 아니어서 병합 파일을 만들 수 없습니다.", file=sys.stderr)
                return 2

            merged_started, merged_footer = merge_chunk_text(
                merged_output,
                text,
                merged_started,
                merged_footer,
            )

        if args.pause_seconds > 0 and index < len(ranges):
            time.sleep(args.pause_seconds)

    if merged_output and merged_started:
        write_lines(merged_output, merged_footer, append=True)
        print(f"\n병합 저장: {merged_output}")

    if failure_count > 0:
        print(f"\n실패한 구간 수: {failure_count}", file=sys.stderr)
        return 1

    print("\n모든 구간 다운로드가 완료되었습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
