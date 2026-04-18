#!/usr/bin/env python3
"""macOS GUI app for downloading KMA ASOS data as CSV."""

from __future__ import annotations

import queue
import re
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from kma_app_config import ensure_settings_file, get_default_downloads_dir, get_settings_path, load_auth_key
from kma_asos import AsosDownloadSummary, download_asos_to_csv, generate_default_csv_name
from kma_client import KmaCancelledError, KmaConfigurationError, KmaError, KmaNoDataError

APP_TITLE = "KMA ASOS Downloader"
REQUEST_TIMEOUT_SECONDS = 300.0


class KmaAsosApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("860x700")
        self.minsize(760, 620)

        self.dataset_var = tk.StringVar(value="asos")
        self.start_var = tk.StringVar()
        self.end_var = tk.StringVar()
        self.stn_var = tk.StringVar(value="0")
        self.output_var = tk.StringVar()
        self.status_var = tk.StringVar(value="준비됨")
        self.settings_path_var = tk.StringVar(value=str(get_settings_path()))

        self._event_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._download_running = False
        self._output_user_selected = False
        self._cancel_requested = threading.Event()

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        self._build_header()
        self._build_form()
        self._build_log_area()
        self._set_status("준비됨", color="#1f2937")
        self._refresh_default_output_path()
        self.after(150, self._process_events)

    def _build_header(self) -> None:
        container = ttk.Frame(self, padding=(16, 16, 16, 8))
        container.grid(row=0, column=0, sticky="ew")
        container.columnconfigure(0, weight=1)

        ttk.Label(
            container,
            text="기상청 데이터 CSV 다운로더",
            font=("Arial", 18, "bold"),
        ).grid(row=0, column=0, sticky="w")

        ttk.Label(
            container,
            text="ASOS는 지금 바로 다운로드할 수 있고, AWS는 이후 같은 앱에 추가할 수 있도록 준비해둡니다.",
            foreground="#4b5563",
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))

    def _build_form(self) -> None:
        container = ttk.Frame(self, padding=(16, 8, 16, 8))
        container.grid(row=1, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)

        dataset_frame = ttk.LabelFrame(container, text="데이터셋", padding=12)
        dataset_frame.grid(row=0, column=0, sticky="ew")
        ttk.Radiobutton(
            dataset_frame,
            text="종관기상관측 (ASOS)",
            value="asos",
            variable=self.dataset_var,
        ).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            dataset_frame,
            text="방재기상관측 (AWS) - 준비중",
            value="aws",
            variable=self.dataset_var,
            state="disabled",
        ).grid(row=0, column=1, sticky="w", padx=(16, 0))

        input_frame = ttk.LabelFrame(container, text="다운로드 설정", padding=12)
        input_frame.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        input_frame.columnconfigure(1, weight=1)

        ttk.Label(input_frame, text="시작시각 (yyyymmddhhmm)").grid(row=0, column=0, sticky="w")
        start_entry = ttk.Entry(input_frame, textvariable=self.start_var)
        start_entry.grid(row=0, column=1, sticky="ew", padx=(12, 0))

        ttk.Label(input_frame, text="종료시각 (yyyymmddhhmm)").grid(row=1, column=0, sticky="w", pady=(12, 0))
        end_entry = ttk.Entry(input_frame, textvariable=self.end_var)
        end_entry.grid(row=1, column=1, sticky="ew", padx=(12, 0), pady=(12, 0))

        ttk.Label(input_frame, text="지점번호 stn").grid(row=2, column=0, sticky="w", pady=(12, 0))
        stn_entry = ttk.Entry(input_frame, textvariable=self.stn_var)
        stn_entry.grid(row=2, column=1, sticky="ew", padx=(12, 0), pady=(12, 0))

        ttk.Label(
            input_frame,
            text="예: 0, 108, 108:159",
            foreground="#6b7280",
        ).grid(row=3, column=1, sticky="w", padx=(12, 0), pady=(4, 0))

        ttk.Label(input_frame, text="저장할 CSV 파일").grid(row=4, column=0, sticky="w", pady=(12, 0))
        output_entry = ttk.Entry(input_frame, textvariable=self.output_var)
        output_entry.grid(row=4, column=1, sticky="ew", padx=(12, 8), pady=(12, 0))
        ttk.Button(input_frame, text="선택", command=self._choose_output_file).grid(
            row=4, column=2, sticky="ew", pady=(12, 0)
        )

        settings_frame = ttk.LabelFrame(container, text="authKey 설정", padding=12)
        settings_frame.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        settings_frame.columnconfigure(0, weight=1)

        ttk.Label(
            settings_frame,
            text="authKey는 아래 settings.json 파일에서 읽습니다.",
            foreground="#4b5563",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            settings_frame,
            textvariable=self.settings_path_var,
            foreground="#111827",
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Button(settings_frame, text="설정 파일 열기", command=self._open_settings_file).grid(
            row=0, column=1, rowspan=2, sticky="e"
        )

        action_frame = ttk.Frame(container, padding=(0, 12, 0, 0))
        action_frame.grid(row=3, column=0, sticky="ew")
        action_frame.columnconfigure(0, weight=1)

        self.download_button = ttk.Button(action_frame, text="다운로드 시작", command=self._start_download)
        self.download_button.grid(row=0, column=0, sticky="w")
        self.stop_button = ttk.Button(action_frame, text="다운로드 중지", command=self._request_stop, state="disabled")
        self.stop_button.grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Button(action_frame, text="로그 지우기", command=self._clear_log).grid(row=0, column=2, sticky="w", padx=(8, 0))

        self.status_label = tk.Label(action_frame, textvariable=self.status_var, font=("Arial", 11, "bold"))
        self.status_label.grid(row=0, column=3, sticky="e")

        for variable in (self.start_var, self.end_var, self.stn_var):
            variable.trace_add("write", self._on_fields_changed)

        start_entry.focus_set()

    def _build_log_area(self) -> None:
        container = ttk.LabelFrame(self, text="진행 로그", padding=12)
        container.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 16))
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        self.log_text = tk.Text(container, height=18, wrap="word", state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def _on_fields_changed(self, *_args: object) -> None:
        if not self._output_user_selected:
            self._refresh_default_output_path()

    def _suggest_filename(self) -> str:
        start_text = self.start_var.get().strip() or "start"
        end_text = self.end_var.get().strip() or "end"
        stn_text = self.stn_var.get().strip() or "0"
        try:
            return generate_default_csv_name(start_text, end_text, stn_text)
        except ValueError:
            safe_stn = re.sub(r"[^0-9:]+", "-", stn_text).strip("-") or "stn"
            return f"asos_{safe_stn.replace(':', '-')}_{start_text}_{end_text}.csv"

    def _refresh_default_output_path(self) -> None:
        filename = self._suggest_filename()
        self.output_var.set(str(get_default_downloads_dir() / filename))

    def _choose_output_file(self) -> None:
        initial_name = self._suggest_filename()
        selected = filedialog.asksaveasfilename(
            title="저장할 CSV 파일 선택",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialdir=str(get_default_downloads_dir()),
            initialfile=initial_name,
        )
        if selected:
            self._output_user_selected = True
            self.output_var.set(selected)

    def _open_settings_file(self) -> None:
        settings_path = ensure_settings_file()
        self.settings_path_var.set(str(settings_path))
        try:
            subprocess.run(["open", str(settings_path)], check=False)
        except OSError:
            messagebox.showinfo("설정 파일 경로", str(settings_path))

    def _set_status(self, message: str, *, color: str) -> None:
        self.status_var.set(message)
        self.status_label.configure(foreground=color)

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _set_running(self, running: bool) -> None:
        self._download_running = running
        self.download_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")

    def _request_stop(self) -> None:
        if not self._download_running or self._cancel_requested.is_set():
            return

        self._cancel_requested.set()
        self.stop_button.configure(state="disabled")
        self._set_status("중지 요청", color="#b45309")
        self._append_log("다운로드 중지를 요청했습니다. 현재 요청이 끝나는 즉시 작업을 멈춥니다.")

    def _start_download(self) -> None:
        if self._download_running:
            return

        start_text = self.start_var.get().strip()
        end_text = self.end_var.get().strip()
        stn_text = self.stn_var.get().strip() or "0"
        output_text = self.output_var.get().strip()

        if not start_text or not end_text:
            messagebox.showerror("입력 오류", "시작시각과 종료시각을 입력해 주세요.")
            return
        if not output_text:
            self._refresh_default_output_path()
            output_text = self.output_var.get().strip()

        try:
            auth_key = load_auth_key()
        except KmaConfigurationError as exc:
            self._open_settings_file()
            messagebox.showerror("설정 오류", str(exc))
            return

        output_path = Path(output_text).expanduser()
        self._cancel_requested.clear()
        self._append_log("다운로드를 시작합니다.")
        self._set_status("다운로드 중", color="#2563eb")
        self._set_running(True)

        worker = threading.Thread(
            target=self._download_worker,
            args=(auth_key, start_text, end_text, stn_text, output_path),
            daemon=True,
        )
        worker.start()

    def _download_worker(
        self,
        auth_key: str,
        start_text: str,
        end_text: str,
        stn_text: str,
        output_path: Path,
    ) -> None:
        def log(message: str) -> None:
            self._event_queue.put(("log", message))

        try:
            summary = download_asos_to_csv(
                auth_key,
                start_text=start_text,
                end_text=end_text,
                stn_text=stn_text,
                output_path=output_path,
                timeout=REQUEST_TIMEOUT_SECONDS,
                retries=5,
                retry_delay_seconds=1.0,
                log_callback=log,
                should_cancel=self._cancel_requested.is_set,
            )
        except KmaCancelledError as exc:
            self._event_queue.put(("cancelled", str(exc)))
        except KmaNoDataError as exc:
            self._event_queue.put(("no_data", str(exc)))
        except (KmaError, ValueError) as exc:
            self._event_queue.put(("error", str(exc)))
        except Exception as exc:  # noqa: BLE001
            self._event_queue.put(("error", f"예상하지 못한 오류가 발생했습니다: {exc}"))
        else:
            self._event_queue.put(("success", summary))
        finally:
            self._event_queue.put(("done", None))

    def _process_events(self) -> None:
        while True:
            try:
                event_name, payload = self._event_queue.get_nowait()
            except queue.Empty:
                break

            if event_name == "log":
                self._append_log(str(payload))
            elif event_name == "success":
                self._handle_success(payload)  # type: ignore[arg-type]
            elif event_name == "no_data":
                self._handle_no_data(str(payload))
            elif event_name == "error":
                self._handle_error(str(payload))
            elif event_name == "cancelled":
                self._handle_cancelled(str(payload))
            elif event_name == "done":
                self._set_running(False)

        self.after(150, self._process_events)

    def _handle_success(self, summary: AsosDownloadSummary) -> None:
        self._set_status("성공", color="#15803d")
        self._append_log(
            f"완료: {summary.row_count}행 저장 / 요청 구간 {summary.request_count}개 / 데이터 구간 {summary.data_chunk_count}개"
        )
        messagebox.showinfo(
            "다운로드 완료",
            (
                f"CSV 저장이 완료되었습니다.\n\n"
                f"파일: {summary.output_path}\n"
                f"행 수: {summary.row_count}\n"
                f"지점번호: {summary.stn_text}"
            ),
        )

    def _handle_no_data(self, message: str) -> None:
        self._set_status("데이터 없음", color="#b45309")
        self._append_log(message)
        messagebox.showinfo("데이터 없음", message)

    def _handle_error(self, message: str) -> None:
        self._set_status("실패", color="#b91c1c")
        self._append_log(message)
        messagebox.showerror("다운로드 실패", message)

    def _handle_cancelled(self, message: str) -> None:
        self._set_status("중지됨", color="#b45309")
        self._append_log(message)
        messagebox.showinfo("다운로드 중지", message)


def main() -> int:
    ensure_settings_file()
    app = KmaAsosApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
