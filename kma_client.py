#!/usr/bin/env python3
"""Shared client utilities for the Korea Meteorological Administration API Hub."""

from __future__ import annotations

import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

DEFAULT_BASE_URL = "https://apihub.kma.go.kr/api"
TIMESTAMP_FORMAT = "%Y%m%d%H%M"
TEXT_CONTENT_HINTS = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml+xml",
    "application/csv",
    "text/csv",
)
RETRYABLE_STATUS_CODES = {408, 429}
CERTIFICATE_CANDIDATES = (
    "/opt/homebrew/etc/openssl@3/cert.pem",
    "/etc/ssl/cert.pem",
)


class KmaError(Exception):
    """Base error for KMA helper modules."""


class KmaConfigurationError(KmaError):
    """Raised when local application configuration is missing or invalid."""


class KmaRequestError(KmaError):
    """Raised when a request fails after all retries."""

    def __init__(self, message: str, *, status_code: int | None = None, url: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url


class KmaNoDataError(KmaError):
    """Raised when the requested range has no data rows."""


class KmaParseError(KmaError):
    """Raised when a text response cannot be parsed as expected."""


class KmaCancelledError(KmaError):
    """Raised when the user stops an in-progress download."""


@dataclass(slots=True)
class KmaTextResponse:
    status_code: int
    content_type: str
    body_size: int
    text: str
    encoding: str
    url: str


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
            "User-Agent": "kma-downloader/1.0",
            "Accept": "*/*",
        },
    )
    ssl_context = build_ssl_context()

    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl_context) as response:
            return response.status, dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read()


def build_ssl_context() -> ssl.SSLContext:
    env_cert_file = os.environ.get("SSL_CERT_FILE")
    if env_cert_file:
        return ssl.create_default_context(cafile=env_cert_file)

    try:
        import certifi  # type: ignore
    except ImportError:
        certifi = None

    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())

    for cert_path in CERTIFICATE_CANDIDATES:
        if os.path.exists(cert_path):
            return ssl.create_default_context(cafile=cert_path)

    return ssl.create_default_context()


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


def parse_timestamp(value: str) -> datetime:
    try:
        return datetime.strptime(value, TIMESTAMP_FORMAT)
    except ValueError as exc:
        raise ValueError(f"`{value}` 는 yyyymmddhhmm 형식이 아닙니다.") from exc


def format_timestamp(value: datetime) -> str:
    return value.strftime(TIMESTAMP_FORMAT)


def split_time_ranges(start: datetime, end: datetime, *, max_days: int = 31) -> list[tuple[datetime, datetime]]:
    if start > end:
        raise ValueError("시작 시각이 종료 시각보다 늦습니다.")
    if max_days <= 0:
        raise ValueError("max_days 는 1 이상이어야 합니다.")

    ranges: list[tuple[datetime, datetime]] = []
    cursor = start

    while cursor <= end:
        next_cursor = cursor + timedelta(days=max_days)
        chunk_end = min(end, next_cursor - timedelta(minutes=1))
        ranges.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(minutes=1)

    return ranges


def is_retryable_status(status_code: int) -> bool:
    return status_code >= 500 or status_code in RETRYABLE_STATUS_CODES


def raise_if_cancelled(should_cancel: Callable[[], bool] | None) -> None:
    if should_cancel and should_cancel():
        raise KmaCancelledError("사용자가 다운로드 중지를 요청했습니다.")


def sleep_with_cancel(
    seconds: float,
    should_cancel: Callable[[], bool] | None,
    *,
    poll_interval: float = 0.1,
) -> None:
    deadline = time.monotonic() + max(seconds, 0.0)
    while time.monotonic() < deadline:
        raise_if_cancelled(should_cancel)
        time.sleep(min(poll_interval, max(deadline - time.monotonic(), 0.0)))
    raise_if_cancelled(should_cancel)


def extract_error_message(text: str | None, status_code: int | None) -> str:
    if text:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None

        if isinstance(payload, dict):
            result = payload.get("result")
            if isinstance(result, dict):
                message = result.get("message")
                if isinstance(message, str) and message.strip():
                    return message.strip()

        stripped = text.strip()
        if stripped:
            first_line = stripped.splitlines()[0].strip()
            if first_line:
                return first_line

    if status_code is None:
        return "요청에 실패했습니다."
    return f"HTTP {status_code} 요청에 실패했습니다."


def request_text(
    endpoint: str,
    params: dict[str, str],
    auth_key: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 30.0,
    retries: int = 3,
    retry_delay_seconds: float = 1.0,
    forced_encoding: str | None = None,
    log_callback: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> KmaTextResponse:
    url = build_url(base_url, endpoint, params, auth_key)
    last_error: KmaRequestError | None = None

    for attempt in range(retries + 1):
        raise_if_cancelled(should_cancel)
        try:
            status_code, headers, body = fetch(url, timeout)
        except Exception as exc:  # noqa: BLE001
            message = f"요청 중 오류가 발생했습니다: {exc}"
            last_error = KmaRequestError(message, url=url)
            if attempt < retries:
                if log_callback:
                    log_callback(
                        f"요청 오류로 재시도합니다 ({attempt + 1}/{retries}) - {message}"
                    )
                sleep_with_cancel(retry_delay_seconds, should_cancel)
                continue
            if log_callback:
                log_callback(f"요청 실패 - {message}")
            raise last_error from exc

        content_type = headers.get("Content-Type", "unknown")
        text, encoding_label = decode_body(content_type, forced_encoding, body)
        if text is None:
            message = "텍스트 응답이 아니어서 처리할 수 없습니다."
            if log_callback:
                log_callback(f"요청 실패 - {message}")
            raise KmaRequestError(message, status_code=status_code, url=url)

        if status_code >= 400:
            message = extract_error_message(text, status_code)
            last_error = KmaRequestError(message, status_code=status_code, url=url)
            if attempt < retries and is_retryable_status(status_code):
                if log_callback:
                    log_callback(
                        f"HTTP {status_code} 응답으로 재시도합니다 ({attempt + 1}/{retries}) - {message}"
                    )
                sleep_with_cancel(retry_delay_seconds, should_cancel)
                continue
            if log_callback:
                log_callback(f"API 요청 실패 - HTTP {status_code}: {message}")
            raise last_error

        return KmaTextResponse(
            status_code=status_code,
            content_type=content_type,
            body_size=len(body),
            text=text,
            encoding=encoding_label or "unknown",
            url=url,
        )

    if last_error is None:
        raise KmaRequestError("알 수 없는 이유로 요청에 실패했습니다.", url=url)
    raise last_error
