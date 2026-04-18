#!/usr/bin/env python3
"""Simple CLI for testing Korea Meteorological Administration API Hub endpoints."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_BASE_URL = "https://apihub.kma.go.kr/api"
TEXT_CONTENT_HINTS = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml+xml",
    "application/csv",
    "text/csv",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="기상청 API 허브 엔드포인트를 호출하고 응답을 미리보기합니다.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "예시:\n"
            "  python3 kma_api_test.py \\\n"
            "    --endpoint typ01/url/kma_sfctm3.php \\\n"
            "    --param tm1=202308010000 \\\n"
            "    --param tm2=202308012359 \\\n"
            "    --param stn=0 \\\n"
            "    --param help=0 \\\n"
            "    --output asos.txt\n\n"
            "  KMA_AUTH_KEY=발급받은인증키 python3 kma_api_test.py \\\n"
            "    --endpoint https://apihub.kma.go.kr/api/typ01/url/amos.php \\\n"
            "    --param tm=202211301200 \\\n"
            "    --param dtm=60 \\\n"
            "    --param stn=0 \\\n"
            "    --param help=1"
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
        help="쿼리 파라미터. 여러 번 반복해서 사용할 수 있습니다.",
    )
    parser.add_argument(
        "--param-file",
        help="쿼리 파라미터가 담긴 JSON 파일 경로. 예: {\"tm1\":\"202308010000\"}",
    )
    parser.add_argument(
        "--output",
        help="응답을 저장할 파일 경로. 텍스트 응답은 UTF-8로 저장합니다.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="요청 타임아웃(초). 기본값은 30초입니다.",
    )
    parser.add_argument(
        "--preview-lines",
        type=int,
        default=20,
        help="터미널에 보여줄 응답 미리보기 줄 수. 0이면 미리보기를 끕니다.",
    )
    parser.add_argument(
        "--encoding",
        help="응답 디코딩 인코딩을 강제로 지정합니다. 예: utf-8, cp949",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="실제 호출 없이 최종 URL만 확인합니다.",
    )
    return parser


def parse_key_value(raw_item: str) -> tuple[str, str]:
    if "=" not in raw_item:
        raise ValueError(f"`{raw_item}` 형식이 잘못되었습니다. KEY=VALUE 형태로 입력하세요.")

    key, value = raw_item.split("=", 1)
    key = key.strip()
    value = value.strip()

    if not key:
        raise ValueError(f"`{raw_item}` 에서 KEY가 비어 있습니다.")

    return key, value


def load_params(param_items: list[str], param_file: str | None) -> dict[str, str]:
    params: dict[str, str] = {}

    if param_file:
        path = Path(param_file)
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, dict):
            raise ValueError("`--param-file` 은 JSON 객체 형태여야 합니다.")

        params.update({str(key): str(value) for key, value in data.items() if value is not None})

    for item in param_items:
        key, value = parse_key_value(item)
        params[key] = value

    return params


def build_url(base_url: str, endpoint: str, params: dict[str, str], auth_key: str) -> str:
    query_params = dict(params)
    query_params["authKey"] = auth_key

    if endpoint.startswith(("http://", "https://")):
        parsed = urllib.parse.urlparse(endpoint)
        existing = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        existing.update(query_params)
        query = urllib.parse.urlencode(existing)
        return urllib.parse.urlunparse(parsed._replace(query=query))

    full_url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    query = urllib.parse.urlencode(query_params)
    return f"{full_url}?{query}"


def mask_auth_key(url: str) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if len(key) <= 8:
            masked = "*" * len(key)
        else:
            masked = f"{key[:4]}...{key[-4:]}"
        return f"authKey={masked}"

    return re.sub(r"authKey=([^&]+)", replace, url)


def fetch(url: str, timeout: float) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "kma-api-test/1.0",
            "Accept": "*/*",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read()


def is_text_response(content_type: str, body: bytes) -> bool:
    lower_content_type = content_type.lower()
    if any(hint in lower_content_type for hint in TEXT_CONTENT_HINTS):
        return True

    sample = body[:1024]
    return b"\x00" not in sample


def detect_encoding(content_type: str, forced_encoding: str | None, body: bytes) -> tuple[str, str]:
    candidates: list[str] = []

    if forced_encoding:
        candidates.append(forced_encoding)

    match = re.search(r"charset=([^\s;]+)", content_type, flags=re.IGNORECASE)
    if match:
        candidates.append(match.group(1).strip("\"'"))

    candidates.extend(["utf-8", "cp949", "euc-kr"])

    seen: set[str] = set()
    for encoding in candidates:
        normalized = encoding.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        try:
            body.decode(encoding)
            return encoding, encoding
        except UnicodeDecodeError:
            continue

    return "utf-8", "utf-8 (errors=replace)"


def decode_body(content_type: str, forced_encoding: str | None, body: bytes) -> tuple[str | None, str | None]:
    if not is_text_response(content_type, body) and not forced_encoding:
        return None, None

    encoding, label = detect_encoding(content_type, forced_encoding, body)
    return body.decode(encoding, errors="replace"), label


def save_output(path_string: str, body: bytes, text: str | None) -> tuple[Path, str]:
    path = Path(path_string)
    path.parent.mkdir(parents=True, exist_ok=True)
    if text is not None:
        path.write_text(text, encoding="utf-8")
        return path, "utf-8 text"

    path.write_bytes(body)
    return path, "raw bytes"


def print_preview(text: str, preview_lines: int) -> None:
    if preview_lines <= 0:
        return

    lines = text.splitlines()
    if not lines:
        print("\n[응답 미리보기]")
        print("(비어 있음)")
        return

    print("\n[응답 미리보기]")
    for line in lines[:preview_lines]:
        print(line)

    if len(lines) > preview_lines:
        print(f"... ({len(lines) - preview_lines}줄 더 있음)")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    auth_key = args.auth_key or os.getenv("KMA_AUTH_KEY")
    if not auth_key:
        parser.error("`--auth-key` 또는 `KMA_AUTH_KEY` 환경변수 중 하나는 반드시 필요합니다.")

    try:
        params = load_params(args.param, args.param_file)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"파라미터 로딩 실패: {exc}", file=sys.stderr)
        return 2

    url = build_url(args.base_url, args.endpoint, params, auth_key)
    print(f"요청 URL: {mask_auth_key(url)}")

    if args.dry_run:
        return 0

    try:
        status_code, headers, body = fetch(url, args.timeout)
    except urllib.error.URLError as exc:
        print(f"요청 실패: {exc}", file=sys.stderr)
        return 2

    content_type = headers.get("Content-Type", "unknown")
    print(f"HTTP 상태: {status_code}")
    print(f"Content-Type: {content_type}")
    print(f"응답 크기: {len(body)} bytes")

    text, encoding_label = decode_body(content_type, args.encoding, body)
    if args.output:
        saved_path, saved_mode = save_output(args.output, body, text)
        print(f"응답 저장: {saved_path} ({saved_mode})")

    if text is not None:
        print(f"디코딩: {encoding_label}")
        print_preview(text, args.preview_lines)
    elif args.preview_lines > 0:
        print("\n[응답 미리보기]")
        print("텍스트 응답이 아닌 것으로 보여 미리보기를 생략했습니다. `--output`으로 저장해 확인하세요.")

    return 0 if status_code < 400 else 1


if __name__ == "__main__":
    raise SystemExit(main())
