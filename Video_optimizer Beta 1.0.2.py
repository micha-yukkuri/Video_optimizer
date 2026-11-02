import os
import sys
import json
import time
import hashlib
import threading
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# ============================================================
# FFmpegの検索・初回ダウンロード
# Gyan.dev Release Essentials ZIPを利用
# ============================================================
FFMPEG_ARCHIVE_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
FFMPEG_SHA256_URL = FFMPEG_ARCHIVE_URL + ".sha256"


def format_file_size(size_bytes):
    """バイト数を適切な単位（B / KB / MB / GB / TB）で表示する。"""
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.2f} {unit}"
        size /= 1024


def get_app_dir():
    """PyInstaller化された場合はEXEの場所、通常実行時はpyファイルの場所を返す。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def find_ffmpeg():
    base_dir = get_app_dir()

    # アプリ内の専用フォルダ（自動ダウンロード先）
    ffmpeg_candidates = [
        os.path.join(base_dir, "ffmpeg", "ffmpeg.exe"),
        os.path.join(base_dir, "ffmpeg", "bin", "ffmpeg.exe"),
        os.path.join(base_dir, "ffmpeg.exe"),
        os.path.join(base_dir, "ffmpeg"),
    ]
    ffprobe_candidates = [
        os.path.join(base_dir, "ffmpeg", "ffprobe.exe"),
        os.path.join(base_dir, "ffmpeg", "bin", "ffprobe.exe"),
        os.path.join(base_dir, "ffprobe.exe"),
        os.path.join(base_dir, "ffprobe"),
    ]

    for ff in ffmpeg_candidates:
        if os.path.isfile(ff):
            for fp in ffprobe_candidates:
                if os.path.isfile(fp):
                    return ff, fp

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        return ffmpeg, ffprobe

    common = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffprobe.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffprobe.exe",
        r"C:\Program Files\Git\usr\bin\ffmpeg.exe",
        r"C:\Program Files\Git\usr\bin\ffprobe.exe",
    ]

    for i in range(0, len(common), 2):
        ffmpeg_path = common[i]
        ffprobe_path = common[i + 1] if i + 1 < len(common) else ""
        if os.path.isfile(ffmpeg_path) and os.path.isfile(ffprobe_path):
            return ffmpeg_path, ffprobe_path
        if os.path.isfile(ffmpeg_path):
            alt_probe = os.path.join(os.path.dirname(ffmpeg_path), "ffprobe.exe")
            if os.path.isfile(alt_probe):
                return ffmpeg_path, alt_probe

    raise RuntimeError(
        "ffmpeg / ffprobe が見つかりません。\n"
        "初回起動時のダウンロードをキャンセルした場合は、FFmpegを導入してから再度お試しください。"
    )


def download_ffmpeg_package(progress_callback=None):
    """公式配布ページからEssentials ZIPを取得し、SHA-256検証後に必要ファイルを配置する。"""
    app_dir = get_app_dir()
    install_dir = os.path.join(app_dir, "ffmpeg")
    os.makedirs(app_dir, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="video_optimizer_ffmpeg_") as temp_dir:
        archive_path = os.path.join(temp_dir, "ffmpeg-essentials.zip")
        request = urllib.request.Request(
            FFMPEG_ARCHIVE_URL,
            headers={"User-Agent": "VideoOptimizer/1.0"}
        )
        with urllib.request.urlopen(request, timeout=30) as response, open(archive_path, "wb") as output:
            total = int(response.headers.get("Content-Length", "0") or 0)
            downloaded = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                if progress_callback:
                    progress_callback(downloaded, total)

        # 配布元が公開しているSHA-256と照合
        hash_request = urllib.request.Request(
            FFMPEG_SHA256_URL,
            headers={"User-Agent": "VideoOptimizer/1.0"}
        )
        with urllib.request.urlopen(hash_request, timeout=30) as response:
            hash_text = response.read().decode("utf-8", errors="replace")
        expected_hash = next((part.lower() for part in hash_text.split() if len(part) == 64 and all(c in "0123456789abcdefABCDEF" for c in part)), None)
        if not expected_hash:
            raise RuntimeError("FFmpegのチェックサムを取得できませんでした。安全のためインストールを中止しました。")

        digest = hashlib.sha256()
        with open(archive_path, "rb") as archive_file:
            for chunk in iter(lambda: archive_file.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest().lower() != expected_hash:
            raise RuntimeError("ダウンロードしたFFmpeg ZIPの整合性を確認できませんでした。再度お試しください。")

        staging_dir = os.path.join(temp_dir, "install")
        os.makedirs(staging_dir, exist_ok=True)
        found = set()
        with zipfile.ZipFile(archive_path, "r") as archive:
            for info in archive.infolist():
                normalized = info.filename.replace("\\", "/")
                lower_name = normalized.lower()
                basename = os.path.basename(normalized)
                if basename.lower() in ("ffmpeg.exe", "ffprobe.exe") and "/bin/" in lower_name:
                    target = os.path.join(staging_dir, basename.lower())
                    with archive.open(info, "r") as source, open(target, "wb") as destination:
                        shutil.copyfileobj(source, destination)
                    found.add(basename.lower())
                elif basename.lower() in ("license.txt", "readme.txt"):
                    # 配布物に付属するライセンス・ビルド情報も残す
                    target = os.path.join(staging_dir, basename)
                    with archive.open(info, "r") as source, open(target, "wb") as destination:
                        shutil.copyfileobj(source, destination)

        if found != {"ffmpeg.exe", "ffprobe.exe"}:
            raise RuntimeError("ZIP内にffmpeg.exeまたはffprobe.exeが見つかりませんでした。")

        # 既存フォルダがある場合も、完成した一式と入れ替える
        backup_dir = install_dir + "_old"
        if os.path.exists(backup_dir):
            shutil.rmtree(backup_dir, ignore_errors=True)
        if os.path.exists(install_dir):
            os.replace(install_dir, backup_dir)
        try:
            os.replace(staging_dir, install_dir)
        except Exception:
            if os.path.exists(backup_dir) and not os.path.exists(install_dir):
                os.replace(backup_dir, install_dir)
            raise
        if os.path.exists(backup_dir):
            shutil.rmtree(backup_dir, ignore_errors=True)


def ffprobe(path):
    _, ffprobe_path = find_ffmpeg()
    cmd = [
        ffprobe_path,
        "-v", "error",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        path
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe 失敗: {result.stderr}")
    return json.loads(result.stdout)

def get_video_metadata(path):
    data = ffprobe(path)
    format_data = data.get("format", {})
    streams = data.get("streams", [])

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video_stream:
        raise RuntimeError("動画ストリームが見つかりません。")

    duration = float(format_data.get("duration", 0))
    bit_rate = int(format_data.get("bit_rate", 0) or 0)
    width = int(video_stream.get("width", 0))
    height = int(video_stream.get("height", 0))

    return {
        "duration": duration,
        "bit_rate": bit_rate,
        "width": width,
        "height": height,
    }

def estimate_bitrate(path):
    size_bytes = os.path.getsize(path)
    duration = get_video_metadata(path)["duration"]
    if duration <= 0:
        raise RuntimeError("動画の長さが取得できません。")
    return int((size_bytes * 8) / duration)

def choose_crf(width, height):
    max_dim = max(width, height)
    if max_dim >= 3840:
        return 23
    elif max_dim >= 2560:
        return 22
    elif max_dim >= 1920:
        return 21
    else:
        return 20

def should_use_h265(width, height):
    return max(width, height) >= 3840

def build_filter(width, height):
    max_dim = max(width, height)
    if max_dim > 2560:
        limit = 2560
    elif max_dim > 1920:
        limit = 1920
    else:
        limit = 0

    if limit:
        return (
            f"scale='min({limit},iw)':'min({limit},ih)':"
            "force_original_aspect_ratio=decrease,"
            "unsharp=5:5:0.7:3:3:0.5"
        )
    return "unsharp=5:5:0.7:3:3:0.5"

def optimize_video(input_path, output_path, reduction_ratio=0.35, progress_callback=None):
    if not os.path.exists(input_path):
        raise FileNotFoundError(input_path)

    ffmpeg_path, _ = find_ffmpeg()
    metadata = get_video_metadata(input_path)
    original_bps = metadata["bit_rate"] if metadata["bit_rate"] > 0 else estimate_bitrate(input_path)
    target_bps = max(900_000, int(original_bps * (1.0 - reduction_ratio)))

    codec = "libx265" if should_use_h265(metadata["width"], metadata["height"]) else "libx264"
    preset = "medium"
    crf = choose_crf(metadata["width"], metadata["height"])
    filters = build_filter(metadata["width"], metadata["height"])

    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    cmd = [
        ffmpeg_path,
        "-y",
        "-progress", "pipe:1",
        "-nostats",
        "-v", "error",
        "-i", input_path,
        "-vf", filters,
        "-c:v", codec,
        "-preset", preset,
        "-crf", str(crf),
        "-maxrate", str(target_bps),
        "-bufsize", str(max(target_bps * 2, 2_000_000)),
        "-c:a", "aac",
        "-movflags", "+faststart",
        "-pix_fmt", "yuv420p",
        output_path
    ]

    # FFmpegの進捗情報を読み取り、動画の再生時間を基準に割合を計算
    duration = max(float(metadata.get("duration", 0) or 0), 0.001)
    error_file = tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=error_file,
        text=True, encoding="utf-8", errors="replace", bufsize=1
    )
    last_percent = -1
    try:
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("out_time_ms="):
                try:
                    elapsed_seconds = int(line.split("=", 1)[1]) / 1_000_000
                    percent = max(0.0, min(99.0, elapsed_seconds / duration * 100))
                    if progress_callback and int(percent) != last_percent:
                        last_percent = int(percent)
                        progress_callback(percent)
                except (ValueError, ZeroDivisionError):
                    pass
            elif line == "progress=end" and progress_callback:
                progress_callback(100.0)

        return_code = proc.wait()
        error_file.seek(0)
        error_text = error_file.read()
        if return_code != 0:
            raise RuntimeError(f"圧縮失敗: {error_text[-5000:]}")
    finally:
        error_file.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    return {
        "original_size": os.path.getsize(input_path),
        "compressed_size": os.path.getsize(output_path),
    }

class VideoOptimizerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("動画圧縮ツール")
        self.root.geometry("760x760")
        self.root.configure(bg="#f7f4ff")
        self.root.resizable(True, True)
        self.root.option_add("*Font", "Arial 10")

        outer = tk.Frame(self.root, bg="#f7f4ff", padx=18, pady=18)
        outer.pack(fill="both", expand=True)

        panel = tk.Frame(outer, bg="#ffffff", bd=0, highlightthickness=0)
        panel.pack(fill="both", expand=True)

        title_bar = tk.Frame(panel, bg="#cfe7ff", height=90, padx=20, pady=20)
        title_bar.pack(fill="x")
        title_bar.pack_propagate(False)

        title = tk.Label(
            title_bar,
            text="動画圧縮ツール",
            bg="#cfe7ff",
            fg="#374151",
            font=("Arial", 22, "bold"),
            anchor="w"
        )
        title.pack(fill="x")

        body = tk.Frame(panel, bg="#ffffff", padx=20, pady=16)
        body.pack(fill="both", expand=True)

        field1 = tk.Frame(body, bg="#ffffff")
        field1.pack(fill="x", pady=(0, 14))

        tk.Label(field1, text="動画ファイル", bg="#ffffff", fg="#4a5568", font=("Arial", 10, "bold")).pack(anchor="w")

        row1 = tk.Frame(field1, bg="#ffffff")
        row1.pack(fill="x", pady=(6, 0))

        self.input_entry = tk.Entry(row1, width=60, font=("Arial", 10), bg="#fdfdff", fg="#1f2937", bd=2, relief="flat")
        self.input_entry.pack(side="left", fill="x", expand=True, ipady=9, padx=(8, 12))
        self.input_entry.configure(highlightbackground="#d6d9ef", highlightcolor="#d6d9ef", highlightthickness=1)

        self.browse_btn = tk.Button(
            row1,
            text="参照",
            command=self.select_video_file,
            bg="#b9d9ff",
            fg="#1f2937",
            font=("Arial", 10, "bold"),
            relief="flat",
            bd=0,
            padx=18,
            pady=9,
            cursor="hand2"
        )
        self.browse_btn.pack(side="left")

        field2 = tk.Frame(body, bg="#ffffff")
        field2.pack(fill="x", pady=(0, 18))

        tk.Label(field2, text="出力先フォルダ", bg="#ffffff", fg="#4a5568", font=("Arial", 10, "bold")).pack(anchor="w")

        row2 = tk.Frame(field2, bg="#ffffff")
        row2.pack(fill="x", pady=(6, 0))

        self.output_entry = tk.Entry(row2, width=60, font=("Arial", 10), bg="#fdfdff", fg="#1f2937", bd=2, relief="flat")
        self.output_entry.pack(side="left", fill="x", expand=True, ipady=9, padx=(8, 12))
        self.output_entry.configure(highlightbackground="#d6d9ef", highlightcolor="#d6d9ef", highlightthickness=1)

        self.output_btn = tk.Button(
            row2,
            text="参照",
            command=self.select_output_dir,
            bg="#ffd6e8",
            fg="#1f2937",
            font=("Arial", 10, "bold"),
            relief="flat",
            bd=0,
            padx=18,
            pady=9,
            cursor="hand2"
        )
        self.output_btn.pack(side="left")

        goal = tk.Frame(body, bg="#fef4d9", padx=14, pady=12, bd=0)
        goal.pack(fill="x", pady=(0, 16))
        tk.Label(goal, text="目標: 元サイズから約35%削減（画質劣化を抑えながら）", bg="#fef4d9", fg="#6b5c2b", font=("Arial", 10, "bold")).pack(anchor="w")

        self.run_btn = tk.Button(
            body,
            text="圧縮開始",
            command=self.start_compress,
            bg="#9fe3c8",
            fg="#1f2937",
            font=("Arial", 12, "bold"),
            relief="flat",
            bd=0,
            padx=18,
            pady=12,
            width=18,
            cursor="hand2"
        )
        self.run_btn.pack(fill="x", pady=(4, 14))

        status_box = tk.Frame(body, bg="#f4f5ff", padx=14, pady=10)
        status_box.pack(fill="both", expand=True)

        self.status_var = tk.StringVar(value="待機中")
        tk.Label(status_box, textvariable=self.status_var, bg="#f4f5ff", fg="#48556a", justify="left", anchor="w", wraplength=600).pack(anchor="w")

        self.compress_progress = ttk.Progressbar(
            status_box, orient="horizontal", mode="determinate",
            maximum=100, length=600
        )
        self.compress_progress.pack(fill="x", pady=(12, 3))
        self.compress_percent_var = tk.StringVar(value="0%")
        tk.Label(
            status_box, textvariable=self.compress_percent_var,
            bg="#f4f5ff", fg="#48556a", anchor="e"
        ).pack(fill="x")

        # 圧縮結果の4項目は待機中も常に表示しておく
        metrics_frame = tk.Frame(status_box, bg="#f4f5ff")
        metrics_frame.pack(fill="x", pady=(8, 0))

        self.metric_vars = {
            "original": tk.StringVar(value="--"),
            "compressed": tk.StringVar(value="--"),
            "duration": tk.StringVar(value="--:--"),
            "reduction": tk.StringVar(value="--%"),
        }
        metric_items = [
            ("元のサイズ", "original"),
            ("圧縮後のサイズ", "compressed"),
            ("動画の長さ", "duration"),
            ("圧縮率", "reduction"),
        ]
        for index, (label_text, key) in enumerate(metric_items):
            card = tk.Frame(
                metrics_frame, bg="#ffffff", padx=12, pady=9,
                highlightthickness=1, highlightbackground="#e0e5f2"
            )
            card.grid(row=index // 2, column=index % 2, sticky="nsew", padx=5, pady=5)
            tk.Label(
                card, text=label_text, bg="#ffffff", fg="#68758a",
                font=("Arial", 9, "bold")
            ).pack(anchor="w")
            tk.Label(
                card, textvariable=self.metric_vars[key], bg="#ffffff",
                fg="#27364b", font=("Arial", 14, "bold")
            ).pack(anchor="w", pady=(3, 0))
        metrics_frame.grid_columnconfigure(0, weight=1)
        metrics_frame.grid_columnconfigure(1, weight=1)
        metrics_frame.grid_rowconfigure(0, weight=1)
        metrics_frame.grid_rowconfigure(1, weight=1)

        # FFmpegが見つからない場合だけ、初回ダウンロードを確認する
        self.root.after(500, self.check_ffmpeg_on_startup)

    def check_ffmpeg_on_startup(self):
        try:
            find_ffmpeg()
            return
        except Exception:
            pass

        answer = messagebox.askyesno(
            "FFmpegのダウンロード",
            "動画圧縮機能を利用するにはFFmpegが必要です。\n\n"
            "FFmpegとFFprobeをダウンロードしますか？\n"
            "（初回のみ。ダウンロードには時間がかかる場合があります。）"
        )
        if answer:
            self.start_ffmpeg_download()
        else:
            self.status_var.set("FFmpeg未導入：必要になったときにメニューからではなく、アプリを再起動して導入できます。")

    def start_ffmpeg_download(self):
        self.download_window = tk.Toplevel(self.root)
        self.download_window.title("FFmpegをダウンロード中")
        self.download_window.geometry("440x150")
        self.download_window.resizable(False, False)
        self.download_window.transient(self.root)
        self.download_window.grab_set()

        tk.Label(
            self.download_window,
            text="FFmpegとFFprobeをダウンロードしています...",
            font=("Arial", 10, "bold")
        ).pack(pady=(20, 10))
        self.download_progress = ttk.Progressbar(
            self.download_window, orient="horizontal", mode="determinate", maximum=100, length=380
        )
        self.download_progress.pack(pady=5)
        self.download_status = tk.Label(self.download_window, text="接続しています...", font=("Arial", 9))
        self.download_status.pack(pady=3)
        self.download_percent = tk.Label(
            self.download_window, text="0%", font=("Arial", 11, "bold")
        )
        self.download_percent.pack(pady=(0, 5))
        self.download_window.protocol("WM_DELETE_WINDOW", lambda: None)

        threading.Thread(target=self.download_ffmpeg_worker, daemon=True).start()

    def download_ffmpeg_worker(self):
        try:
            def report_progress(downloaded, total):
                self.root.after(0, self.update_download_progress, downloaded, total)

            download_ffmpeg_package(report_progress)
            self.root.after(0, self.finish_ffmpeg_download, None)
        except Exception as error:
            self.root.after(0, self.finish_ffmpeg_download, str(error))

    def update_download_progress(self, downloaded, total):
        if not hasattr(self, "download_window") or not self.download_window.winfo_exists():
            return
        if total > 0:
            percent = downloaded / total * 100
            self.download_progress.configure(value=percent)
            self.download_status.config(
                text=f"{format_file_size(downloaded)} / {format_file_size(total)}"
            )
            self.download_percent.config(text=f"{percent:.1f}%")
        else:
            self.download_status.config(text=f"{format_file_size(downloaded)} ダウンロード済み")
            self.download_percent.config(text="進捗を計算中...")

    def finish_ffmpeg_download(self, error_message):
        if hasattr(self, "download_window") and self.download_window.winfo_exists():
            self.download_window.grab_release()
            self.download_window.destroy()

        if error_message:
            self.status_var.set("FFmpegのダウンロードに失敗しました。")
            messagebox.showerror(
                "ダウンロード失敗",
                "FFmpegを導入できませんでした。\n\n"
                f"詳細：{error_message}\n\n"
                "インターネット接続を確認して、もう一度アプリを起動してください。"
            )
            return

        self.status_var.set("FFmpegとFFprobeの準備が完了しました。")
        messagebox.showinfo("準備完了", "FFmpegとFFprobeのインストールが完了しました！\nこれで動画を圧縮できます。")

    def select_video_file(self):
        filetypes = [
            ("動画ファイル", "*.mp4 *.mov *.mkv *.avi *.wmv *.webm *.m4v *.mts *.ts *.mpg *.mpeg *.3gp"),
            ("すべてのファイル", "*.*")
        ]
        path = filedialog.askopenfilename(title="動画ファイルを選択", filetypes=filetypes)
        if path:
            self.input_entry.delete(0, tk.END)
            self.input_entry.insert(0, path)

    def select_output_dir(self):
        directory = filedialog.askdirectory(title="圧縮後の保存先フォルダを選択")
        if directory:
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, directory)

    def start_compress(self):
        input_file = self.input_entry.get().strip()
        output_dir = self.output_entry.get().strip()

        if not input_file:
            messagebox.showwarning("入力未選択", "動画ファイルを選択してください。")
            return

        if not os.path.exists(input_file):
            messagebox.showwarning("ファイル不在", "選択した動画ファイルが存在しません。")
            return

        if not output_dir:
            output_dir = os.path.dirname(input_file)
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, output_dir)

        if not os.path.exists(output_dir):
            messagebox.showwarning("出力先未設定", "保存先フォルダを選択してください。")
            return

        original_name, ext = os.path.splitext(os.path.basename(input_file))
        output_path = os.path.join(output_dir, f"{original_name}_optimized{ext}")

        self.run_btn.config(state="disabled")
        self.compress_progress.configure(value=0)
        self.compress_percent_var.set("0%")
        self.status_var.set("圧縮処理を開始しています...")
        threading.Thread(target=self.compress_worker, args=(input_file, output_path), daemon=True).start()

    def update_compress_progress(self, percent):
        self.compress_progress.configure(value=percent)
        self.compress_percent_var.set(f"{percent:.1f}%")

    def compress_worker(self, input_file, output_path):
        try:
            def report_progress(percent):
                self.root.after(0, self.update_compress_progress, percent)

            result = optimize_video(
                input_file, output_path, reduction_ratio=0.35,
                progress_callback=report_progress
            )
            original_mb = result["original_size"] / (1024 * 1024)
            compressed_mb = result["compressed_size"] / (1024 * 1024)
            reduction = (1 - (result["compressed_size"] / result["original_size"])) * 100
            duration_seconds = get_video_metadata(input_file)["duration"]
            minutes, seconds = divmod(int(duration_seconds), 60)
            duration_text = f"{minutes:02d}:{seconds:02d}"

            def show_result():
                self.metric_vars["original"].set(format_file_size(result["original_size"]))
                self.metric_vars["compressed"].set(format_file_size(result["compressed_size"]))
                self.metric_vars["duration"].set(duration_text)
                self.metric_vars["reduction"].set(f"{reduction:.1f}%")
                self.status_var.set(f"圧縮が完了しました。\n保存先: {output_path}")

            self.root.after(0, show_result)
            self.root.after(0, lambda: self.run_btn.config(state="normal"))
            self.root.after(0, lambda: messagebox.showinfo("完了", f"圧縮が完了しました。\n保存先: {output_path}"))

        except Exception as e:
            error_message = str(e)
            self.root.after(0, lambda error_message=error_message: self.status_var.set(f"エラー: {error_message}"))
            self.root.after(0, lambda: self.run_btn.config(state="normal"))
            self.root.after(0, lambda error_message=error_message: messagebox.showerror("エラー", error_message))

if __name__ == "__main__":
    root = tk.Tk()
    app = VideoOptimizerApp(root)
    root.mainloop()