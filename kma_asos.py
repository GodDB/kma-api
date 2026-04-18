#!/usr/bin/env python3
"""ASOS dataset helpers for the KMA downloader app."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from kma_client import (
    KmaNoDataError,
    KmaParseError,
    format_timestamp,
    parse_timestamp,
    raise_if_cancelled,
    request_text,
    split_time_ranges,
)

ASOS_ENDPOINT = "typ01/url/kma_sfctm3.php"
ASOS_MAX_REQUEST_DAYS = 14
ASOS_COLUMNS = [
    "TM",
    "STN",
    "WD",
    "WS",
    "GST_WD",
    "GST_WS",
    "GST_TM",
    "PA",
    "PS",
    "PT",
    "PR",
    "TA",
    "TD",
    "HM",
    "PV",
    "RN",
    "RN_DAY",
    "RN_JUN",
    "RN_INT",
    "SD_HR3",
    "SD_DAY",
    "SD_TOT",
    "WC",
    "WP",
    "WW",
    "CA_TOT",
    "CA_MID",
    "CH_MIN",
    "CT",
    "CT_TOP",
    "CT_MID",
    "CT_LOW",
    "VS",
    "SS",
    "SI",
    "ST_GD",
    "TS",
    "TE_005",
    "TE_01",
    "TE_02",
    "TE_03",
    "ST_SEA",
    "WH",
    "BF",
    "IR",
    "IX",
]
STN_PATTERN = re.compile(r"^\d+(?::\d+)*$")
NO_DATA_MARKERS = (
    "조회된 자료가 없습니다",
    "자료가 없습니다",
    "데이터가 없습니다",
    "no data",
)


@dataclass(slots=True)
class AsosDownloadSummary:
    output_path: Path
    row_count: int
    request_count: int
    data_chunk_count: int
    start_text: str
    end_text: str
    stn_text: str


def normalize_station_input(value: str) -> str:
    station_text = value.strip()
    if not station_text:
        return "0"
    if not STN_PATTERN.fullmatch(station_text):
        raise ValueError("지점번호는 숫자 또는 `:` 로 구분한 숫자 목록이어야 합니다. 예: 0, 108, 108:159")
    return station_text


def split_marker_text(text: str) -> tuple[list[str], list[str], list[str]]:
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


def response_indicates_no_data(text: str) -> bool:
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in NO_DATA_MARKERS)


def parse_asos_rows(text: str) -> list[list[str]]:
    if response_indicates_no_data(text):
        return []

    if "#START7777" not in text:
        raise KmaParseError("ASOS 응답 형식을 인식할 수 없습니다.")

    _, data_lines, _ = split_marker_text(text)
    rows: list[list[str]] = []

    for line_number, line in enumerate(data_lines, start=1):
        parts = line.split()
        if len(parts) != len(ASOS_COLUMNS):
            raise KmaParseError(
                f"ASOS 데이터 파싱에 실패했습니다. "
                f"{line_number}번째 데이터 행 필드 수가 {len(parts)}개입니다."
            )
        rows.append(parts)

    return rows


def generate_default_csv_name(start_text: str, end_text: str, stn_text: str) -> str:
    normalized_stn = normalize_station_input(stn_text).replace(":", "-")
    return f"asos_{normalized_stn}_{start_text}_{end_text}.csv"


def write_asos_csv(rows: list[list[str]], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(ASOS_COLUMNS)
        writer.writerows(rows)
    return output_path


def download_asos_to_csv(
    auth_key: str,
    *,
    start_text: str,
    end_text: str,
    stn_text: str,
    output_path: Path,
    timeout: float = 30.0,
    retries: int = 3,
    retry_delay_seconds: float = 1.0,
    log_callback: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> AsosDownloadSummary:
    start = parse_timestamp(start_text)
    end = parse_timestamp(end_text)
    normalized_stn = normalize_station_input(stn_text)
    ranges = split_time_ranges(start, end, max_days=ASOS_MAX_REQUEST_DAYS)

    all_rows: list[list[str]] = []
    data_chunk_count = 0

    for index, (chunk_start, chunk_end) in enumerate(ranges, start=1):
        raise_if_cancelled(should_cancel)
        chunk_start_text = format_timestamp(chunk_start)
        chunk_end_text = format_timestamp(chunk_end)
        if log_callback:
            log_callback(
                f"[{index}/{len(ranges)}] ASOS 요청: {chunk_start_text} ~ {chunk_end_text} / stn={normalized_stn}"
            )

        response = request_text(
            ASOS_ENDPOINT,
            {
                "tm1": chunk_start_text,
                "tm2": chunk_end_text,
                "stn": normalized_stn,
                "help": "0",
                "disp": "1",
            },
            auth_key,
            timeout=timeout,
            retries=retries,
            retry_delay_seconds=retry_delay_seconds,
            log_callback=log_callback,
            should_cancel=should_cancel,
        )

        raise_if_cancelled(should_cancel)
        rows = parse_asos_rows(response.text)
        if rows:
            all_rows.extend(rows)
            data_chunk_count += 1
            if log_callback:
                log_callback(f"  -> {len(rows)}행 수신")
        elif log_callback:
            log_callback("  -> 해당 구간 데이터 없음")

    if not all_rows:
        raise KmaNoDataError(
            "해당 기간과 지점번호 조건에 데이터가 없습니다. "
            "기간 또는 지점번호(stn)를 다시 확인해 주세요."
        )

    raise_if_cancelled(should_cancel)
    final_path = write_asos_csv(all_rows, output_path)
    return AsosDownloadSummary(
        output_path=final_path,
        row_count=len(all_rows),
        request_count=len(ranges),
        data_chunk_count=data_chunk_count,
        start_text=start_text,
        end_text=end_text,
        stn_text=normalized_stn,
    )
