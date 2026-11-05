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
import ctypes
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

def extract_audio(input_path, output_path, progress_callback=None):
    """動画ファイルから音声トラックのみを抽出して保存する。"""
    if not os.path.exists(input_path):
        raise FileNotFoundError(input_path)

    ffmpeg_path, _ = find_ffmpeg()
    probe_data = ffprobe(input_path)
    audio_stream = next(
        (stream for stream in probe_data.get("streams", [])
         if stream.get("codec_type") == "audio"),
        None
    )
    if not audio_stream:
        raise RuntimeError("この動画には音声トラックがありません。")

    duration = float(probe_data.get("format", {}).get("duration", 0) or 0)
    if duration <= 0:
        duration = float(audio_stream.get("duration", 0) or 0)
    duration = max(duration, 0.001)

    audio_format = os.path.splitext(output_path)[1].lower()
    cmd = [
        ffmpeg_path, "-y", "-progress", "pipe:1", "-nostats", "-v", "error",
        "-i", input_path, "-map", "0:a:0", "-vn",
    ]

    if audio_format == ".mp3":
        cmd += ["-c:a", "libmp3lame", "-b:a", "192k"]
    elif audio_format == ".wav":
        cmd += ["-c:a", "pcm_s16le"]
    elif audio_format == ".m4a":
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    elif audio_format == ".flac":
        cmd += ["-c:a", "flac"]
    else:
        raise ValueError(f"未対応の音声形式です: {audio_format}")

    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)
    cmd.append(output_path)

    error_file = tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=error_file,
        text=True, encoding="utf-8", errors="replace", bufsize=1
    )
    last_percent = -1
    started = time.monotonic()
    try:
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("out_time_ms="):
                try:
                    elapsed_seconds = int(line.split("=", 1)[1]) / 1_000_000
                    percent = max(0.0, min(99.0, elapsed_seconds / duration * 100))
                    if progress_callback and int(percent) != last_percent:
                        last_percent = int(percent)
                        elapsed = time.monotonic() - started
                        remaining = elapsed * (100.0 - percent) / percent if percent > 0.5 else None
                        progress_callback(percent, remaining)
                except (ValueError, ZeroDivisionError):
                    pass
            elif line == "progress=end" and progress_callback:
                progress_callback(100.0, 0)

        return_code = proc.wait()
        error_file.seek(0)
        error_text = error_file.read()
        if return_code != 0:
            raise RuntimeError(f"音声抽出に失敗しました: {error_text[-5000:]}")
    finally:
        error_file.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    return {
        "original_size": os.path.getsize(input_path),
        "compressed_size": os.path.getsize(output_path),
    }


def optimize_video(input_path, output_path, reduction_ratio=0.35, progress_callback=None, preserve_quality=False):
    if not os.path.exists(input_path):
        raise FileNotFoundError(input_path)

    output_format = os.path.splitext(output_path)[1].lower()
    if output_format in (".mp3", ".wav", ".m4a", ".flac"):
        return extract_audio(input_path, output_path, progress_callback)

    ffmpeg_path, _ = find_ffmpeg()
    metadata = get_video_metadata(input_path)
    original_bps = metadata["bit_rate"] if metadata["bit_rate"] > 0 else estimate_bitrate(input_path)
    target_bps = max(900_000, int(original_bps * (1.0 - reduction_ratio)))

    output_format = os.path.splitext(output_path)[1].lower()
    crf = choose_crf(metadata["width"], metadata["height"])
    filters = build_filter(metadata["width"], metadata["height"])

    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    cmd = [
        ffmpeg_path, "-y", "-progress", "pipe:1", "-nostats", "-v", "error",
        "-i", input_path, "-vf", filters,
    ]
    if output_format == ".webm":
        # WebMではVP9/Opusを使用（H.264/AACはWebMコンテナ非対応）
        vp9_crf = 18 if preserve_quality else max(28, crf + 5)
        cmd += ["-c:v", "libvpx-vp9", "-deadline", "good", "-cpu-used", "2",
                "-crf", str(vp9_crf), "-b:v", "0",
                "-c:a", "libopus", "-b:a", "128k"]
    elif output_format == ".avi":
        cmd += ["-c:v", "mpeg4", "-q:v", "2" if preserve_quality else "5",
                "-c:a", "libmp3lame", "-b:a", "192k"]
    else:
        codec = "libx265" if should_use_h265(metadata["width"], metadata["height"]) else "libx264"
        selected_crf = 18 if preserve_quality else crf
        cmd += ["-c:v", codec, "-preset", "medium", "-crf", str(selected_crf)]
        if not preserve_quality:
            cmd += ["-maxrate", str(target_bps),
                    "-bufsize", str(max(target_bps * 2, 2_000_000))]
        cmd += ["-c:a", "aac"]
        if output_format in (".mp4", ".mov"):
            cmd += ["-movflags", "+faststart"]
    cmd += ["-pix_fmt", "yuv420p", output_path]

    # FFmpegの進捗情報を読み取り、動画の再生時間を基準に割合を計算
    duration = max(float(metadata.get("duration", 0) or 0), 0.001)
    error_file = tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=error_file,
        text=True, encoding="utf-8", errors="replace", bufsize=1
    )
    last_percent = -1
    compression_started = time.monotonic()
    try:
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("out_time_ms="):
                try:
                    elapsed_seconds = int(line.split("=", 1)[1]) / 1_000_000
                    percent = max(0.0, min(99.0, elapsed_seconds / duration * 100))
                    if progress_callback and int(percent) != last_percent:
                        last_percent = int(percent)
                        elapsed = time.monotonic() - compression_started
                        remaining = elapsed * (100.0 - percent) / percent if percent > 0.5 else None
                        progress_callback(percent, remaining)
                except (ValueError, ZeroDivisionError):
                    pass
            elif line == "progress=end" and progress_callback:
                progress_callback(100.0, 0)

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
        self.root.geometry("1180x820")
        self.root.minsize(1050, 760)
        self.root.configure(bg="#f7f4ff")
        self.root.resizable(True, True)
        self.root.option_add("*Font", "Arial 10")

        outer = tk.Frame(self.root, bg="#f7f4ff", padx=20, pady=20)
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

        body = tk.Frame(panel, bg="#ffffff", padx=22, pady=20)
        body.pack(fill="both", expand=True)

        # 左側：既存の動画圧縮UI / 右側：システム使用率モニター
        left_body = tk.Frame(body, bg="#ffffff")
        left_body.pack(side="left", fill="both", expand=True)
        sidebar = tk.Frame(body, bg="#f4f5ff", width=420, padx=14, pady=14)
        sidebar.pack(side="right", fill="y", padx=(16, 0))
        sidebar.pack_propagate(False)

        tk.Label(sidebar, text="システムモニター", bg="#f4f5ff", fg="#374151",
                 font=("Arial", 17, "bold")).pack(anchor="w", pady=(0, 14))

        cards = tk.Frame(sidebar, bg="#f4f5ff")
        cards.pack(fill="both", expand=True)
        cards.grid_columnconfigure(0, weight=1, uniform="monitor")
        cards.grid_columnconfigure(1, weight=1, uniform="monitor")
        cards.grid_rowconfigure(0, weight=1, uniform="monitor")
        cards.grid_rowconfigure(1, weight=1, uniform="monitor")

        self.cpu_canvas, self.cpu_percent_label, _ = self.create_monitor_card(
            cards, "CPU使用率", "#70a7f5", 0, 0)
        self.storage_canvas, self.storage_percent_label, self.storage_detail_label = self.create_monitor_card(
            cards, "ストレージ", "#e99ac2", 0, 1)
        self.memory_canvas, self.memory_percent_label, self.memory_detail_label = self.create_monitor_card(
            cards, "メモリ使用率", "#82c9a5", 1, 0)
        self.cpu_name_label = self.create_cpu_name_card(cards, 1, 1)

        self._cpu_previous = self.read_cpu_times()
        self.root.after(500, self.update_system_monitor)

        # 参照欄は2行に分け、パスとファイル名／フォルダ名を表示する
        self.input_file_path = ""
        self.output_dir_path = ""

        reference_frame = tk.Frame(left_body, bg="#ffffff")
        reference_frame.pack(fill="x", pady=(0, 16))
        reference_frame.grid_columnconfigure(1, weight=1)

        tk.Label(reference_frame, text="動画ファイル", bg="#ffffff", fg="#4a5568",
                 font=("Arial", 9, "bold")).grid(row=0, column=0, sticky="w", padx=(0, 12), pady=7)
        self.input_entry = tk.Entry(
            reference_frame, font=("Arial", 9), bg="#fdfdff", fg="#1f2937",
            bd=1, relief="flat"
        )
        self.input_entry.grid(row=0, column=1, sticky="ew", ipady=7, padx=(0, 10), pady=7)
        self.input_entry.configure(highlightbackground="#d6d9ef", highlightcolor="#d6d9ef", highlightthickness=1)
        self.browse_btn = tk.Button(
            reference_frame, text="参照", command=self.select_video_file,
            bg="#b9d9ff", fg="#1f2937", font=("Arial", 9, "bold"),
            relief="flat", bd=0, padx=12, pady=6, cursor="hand2"
        )
        self.browse_btn.grid(row=0, column=2, pady=4)

        tk.Label(reference_frame, text="保存先フォルダ", bg="#ffffff", fg="#4a5568",
                 font=("Arial", 9, "bold")).grid(row=1, column=0, sticky="w", padx=(0, 12), pady=7)
        self.output_entry = tk.Entry(
            reference_frame, font=("Arial", 9), bg="#fdfdff", fg="#1f2937",
            bd=1, relief="flat"
        )
        self.output_entry.grid(row=1, column=1, sticky="ew", ipady=7, padx=(0, 10), pady=7)
        self.output_entry.configure(highlightbackground="#d6d9ef", highlightcolor="#d6d9ef", highlightthickness=1)
        self.output_btn = tk.Button(
            reference_frame, text="参照", command=self.select_output_dir,
            bg="#ffd6e8", fg="#1f2937", font=("Arial", 9, "bold"),
            relief="flat", bd=0, padx=12, pady=6, cursor="hand2"
        )
        self.output_btn.grid(row=1, column=2, pady=4)

        # 動画プレビューと出力設定を左右2列で配置
        preview_settings = tk.Frame(left_body, bg="#ffffff")
        preview_settings.pack(fill="x", pady=(0, 16))
        preview_settings.grid_columnconfigure(0, weight=1, uniform="preview_settings")
        preview_settings.grid_columnconfigure(1, weight=1, uniform="preview_settings")

        tk.Label(
            preview_settings, text="動画", bg="#ffffff", fg="#4a5568",
            font=("Arial", 10, "bold"), anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=(0, 14), pady=(0, 8))
        tk.Label(
            preview_settings, text="出力形式", bg="#ffffff", fg="#4a5568",
            font=("Arial", 10, "bold"), anchor="w"
        ).grid(row=0, column=1, sticky="w", padx=(14, 0), pady=(0, 8))

        self.preview_frame = tk.Frame(
            preview_settings, bg="#f4f5ff", padx=12, pady=12,
            highlightbackground="#e2e6f2", highlightthickness=1
        )
        self.preview_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 14))
        self.preview_image_label = tk.Label(
            self.preview_frame, text="動画プレビュー", width=28, height=7,
            bg="#e8edf7", fg="#718096", font=("Arial", 9)
        )
        self.preview_image_label.pack(fill="both", expand=True)

        output_settings = tk.Frame(
            preview_settings, bg="#f4f5ff", padx=14, pady=14,
            highlightbackground="#e2e6f2", highlightthickness=1
        )
        output_settings.grid(row=1, column=1, sticky="nsew", padx=(14, 0))
        self.output_format_var = tk.StringVar(value="mp4")
        self.output_format_combo = ttk.Combobox(
            output_settings, textvariable=self.output_format_var,
            values=("mp4", "mkv", "mov", "webm", "avi", "mp3", "m4a", "wav", "flac"),
            state="readonly", width=12
        )
        self.output_format_combo.pack(anchor="w", pady=(4, 18))
        tk.Label(
            output_settings, text="※ MP3 / M4A / WAV / FLACは音声抽出",
            bg="#f4f5ff", fg="#7b8494", font=("Arial", 8), anchor="w"
        ).pack(anchor="w", pady=(0, 16))

        self.preserve_quality_var = tk.BooleanVar(value=False)
        self.preserve_quality_check = tk.Checkbutton(
            output_settings,
            text="画質を維持して圧縮する",
            variable=self.preserve_quality_var,
            bg="#f4f5ff", fg="#48556a",
            activebackground="#f4f5ff", activeforeground="#48556a",
            font=("Arial", 9), justify="left", anchor="w",
            wraplength=280
        )
        self.preserve_quality_check.pack(anchor="w", fill="x", pady=(4, 4))

        self._video_info_request = 0
        self._preview_photo = None
        self._preview_temp_path = None
        self.input_entry.bind("<Return>", lambda _event: self.load_video_info(self.input_file_path))

        self.run_btn = tk.Button(
            left_body,
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
        self.run_btn.pack(fill="x", pady=(16, 14))

        status_box = tk.Frame(left_body, bg="#f4f5ff", padx=18, pady=15)
        status_box.pack(fill="x", pady=(0, 4))

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
        self.eta_var = tk.StringVar(value="残り時間: --")
        tk.Label(status_box, textvariable=self.eta_var, bg="#f4f5ff", fg="#68758a",
                 anchor="e", font=("Arial", 9)).pack(fill="x", pady=(2, 0))

        # 圧縮結果の4項目は待機中も常に表示しておく
        metrics_frame = tk.Frame(left_body, bg="#f4f5ff")
        metrics_frame.pack(fill="x", pady=(14, 0), side="bottom")

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
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 5, 5), pady=5)
            tk.Label(
                card, text=label_text, bg="#ffffff", fg="#68758a",
                font=("Arial", 9, "bold")
            ).pack(anchor="w")
            tk.Label(
                card, textvariable=self.metric_vars[key], bg="#ffffff",
                fg="#27364b", font=("Arial", 14, "bold")
            ).pack(anchor="w", pady=(3, 0))
        for metric_column in range(4):
            metrics_frame.grid_columnconfigure(metric_column, weight=1)
        metrics_frame.grid_rowconfigure(0, weight=1)

        # FFmpegが見つからない場合だけ、初回ダウンロードを確認する
        self.root.after(500, self.check_ffmpeg_on_startup)

    def create_monitor_card(self, parent, title, color, row, column):
        """2×2システムモニター用カードを作成する。"""
        card = tk.Frame(parent, bg="#ffffff", padx=10, pady=10,
                        highlightthickness=1, highlightbackground="#dce3f2")
        card.grid(row=row, column=column, sticky="nsew", padx=5, pady=5)
        tk.Label(card, text=title, bg="#ffffff", fg="#68758a",
                 font=("Arial", 11, "bold")).pack(anchor="w")
        canvas = tk.Canvas(card, width=150, height=145, bg="#ffffff",
                           highlightthickness=0)
        canvas.pack(pady=(3, 0))
        canvas._chart_color = color
        self.draw_usage_chart(canvas, None)
        percent_label = tk.Label(card, text="取得中...", bg="#ffffff",
                                 fg="#48556a", font=("Arial", 10, "bold"))
        percent_label.pack(pady=(0, 5))
        detail_label = tk.Label(card, text="", bg="#ffffff", fg="#68758a",
                                font=("Arial", 8), justify="center",
                                wraplength=175)
        detail_label.pack()
        return canvas, percent_label, detail_label

    def create_cpu_name_card(self, parent, row, column):
        card = tk.Frame(parent, bg="#ffffff", padx=12, pady=10,
                        highlightthickness=1, highlightbackground="#dce3f2")
        card.grid(row=row, column=column, sticky="nsew", padx=5, pady=5)
        tk.Label(card, text="CPU名", bg="#ffffff", fg="#68758a",
                 font=("Arial", 11, "bold")).pack(anchor="w")
        label = tk.Label(card, text=self.get_cpu_name(), bg="#ffffff",
                         fg="#27364b", font=("Arial", 11), justify="left",
                         anchor="nw", wraplength=175)
        label.pack(fill="both", expand=True, anchor="nw", pady=(18, 0))
        return label

    @staticmethod
    def get_cpu_name():
        """WindowsレジストリからCPUの正式名称を取得する。"""
        try:
            import winreg
            key_path = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                return str(value).strip()
        except Exception:
            try:
                import platform
                return platform.processor() or "CPU名を取得できません"
            except Exception:
                return "CPU名を取得できません"

    @staticmethod
    def read_memory_status():
        """Windows GlobalMemoryStatusExから物理メモリ使用量を取得する。"""
        if os.name == "nt":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            status = MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                used = status.ullTotalPhys - status.ullAvailPhys
                return used, status.ullTotalPhys, status.dwMemoryLoad
        try:
            import psutil
            memory = psutil.virtual_memory()
            return memory.used, memory.total, memory.percent
        except Exception:
            return None

    @staticmethod
    def draw_usage_chart(canvas, percent):
        canvas.delete("all")
        color = canvas._chart_color
        canvas.create_oval(25, 10, 125, 110, outline="#e9edf5", width=13)
        if percent is not None:
            canvas.create_arc(25, 10, 125, 110, start=90,
                              extent=-360 * max(0, min(100, percent)) / 100,
                              style="arc", outline=color, width=13)
            value_text = f"{percent:.0f}%"
        else:
            value_text = "--%"
        canvas.create_text(75, 60, text=value_text, fill="#27364b",
                           font=("Arial", 19, "bold"))
        canvas.create_text(75, 132, text="使用中", fill="#8993a5",
                           font=("Arial", 10))

    @staticmethod
    def read_cpu_times():
        """WindowsのGetSystemTimes、またはpsutilからCPU累積時間を取得。"""
        if os.name == "nt":
            class FILETIME(ctypes.Structure):
                _fields_ = [("dwLowDateTime", ctypes.c_uint32),
                            ("dwHighDateTime", ctypes.c_uint32)]
            idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
            if ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle),
                    ctypes.byref(kernel), ctypes.byref(user)):
                def to_int(value):
                    return (value.dwHighDateTime << 32) | value.dwLowDateTime
                return (to_int(idle), to_int(kernel), to_int(user))
        try:
            import psutil
            times = psutil.cpu_times()
            return (times.idle, times.user + times.system, 0)
        except Exception:
            return None

    def update_system_monitor(self):
        """CPU・ストレージ・メモリを約1秒ごとに更新する。"""
        try:
            current = self.read_cpu_times()
            cpu_percent = None
            if current is not None and self._cpu_previous is not None:
                idle_delta = current[0] - self._cpu_previous[0]
                total_delta = sum(current) - sum(self._cpu_previous)
                if total_delta > 0:
                    cpu_percent = max(0.0, min(100.0,
                        (1.0 - idle_delta / total_delta) * 100.0))
            self._cpu_previous = current
            self.draw_usage_chart(self.cpu_canvas, cpu_percent)
            self.cpu_percent_label.config(
                text=f"CPU：{cpu_percent:.1f}%" if cpu_percent is not None
                else "CPU：取得できません")

            drive = os.environ.get("SystemDrive", "C:")
            usage = shutil.disk_usage(drive + os.sep)
            storage_percent = usage.used / usage.total * 100 if usage.total else 0
            self.draw_usage_chart(self.storage_canvas, storage_percent)
            self.storage_percent_label.config(text=f"使用率：{storage_percent:.1f}%")
            self.storage_detail_label.config(
                text=f"空き {format_file_size(usage.free)} / 全体 {format_file_size(usage.total)}")

            memory = self.read_memory_status()
            if memory:
                used, total, memory_percent = memory
                self.draw_usage_chart(self.memory_canvas, memory_percent)
                self.memory_percent_label.config(text=f"使用率：{memory_percent:.1f}%")
                self.memory_detail_label.config(
                    text=f"使用中 {format_file_size(used)} / 合計 {format_file_size(total)}")
            else:
                self.draw_usage_chart(self.memory_canvas, None)
                self.memory_percent_label.config(text="メモリ：取得できません")
                self.memory_detail_label.config(text="")
        except Exception:
            pass
        finally:
            if self.root.winfo_exists():
                self.root.after(1000, self.update_system_monitor)

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
            self.input_file_path = path
            self.input_entry.delete(0, tk.END)
            self.input_entry.insert(0, f"{os.path.dirname(path)}  |  {os.path.basename(path)}")
            self.load_video_info(path)

    def load_video_info(self, path):
        """選択した動画のサムネイルだけを非同期で生成する。"""
        self._video_info_request += 1
        request_id = self._video_info_request
        self._preview_photo = None

        if not path or not os.path.isfile(path):
            self.preview_image_label.configure(image="", text="動画プレビュー")
            return

        self.preview_image_label.configure(image="", text="サムネイルを生成中...")

        def worker():
            thumbnail_path = None
            try:
                metadata = get_video_metadata(path)
                duration = max(float(metadata.get("duration", 0) or 0), 0)
                seek_seconds = max(0, min(duration * 0.1, max(duration - 0.5, 0)))
                ffmpeg_path, _ = find_ffmpeg()
                if not ffmpeg_path:
                    raise RuntimeError("FFmpegが見つかりません。")
                thumbnail_path = os.path.join(
                    tempfile.gettempdir(),
                    f"video_optimizer_preview_{os.getpid()}_{request_id}.png"
                )
                command = [
                    ffmpeg_path, "-y", "-ss", str(seek_seconds), "-i", path,
                    "-frames:v", "1", "-vf",
                    "scale=480:270:force_original_aspect_ratio=decrease,"
                    "pad=480:270:(ow-iw)/2:(oh-ih)/2",
                    "-f", "image2", thumbnail_path
                ]
                result = subprocess.run(
                    command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace", timeout=30
                )
                if result.returncode != 0 or not os.path.isfile(thumbnail_path):
                    raise RuntimeError("サムネイルを生成できませんでした。")
            except Exception:
                thumbnail_path = None

            def update_preview():
                if request_id != self._video_info_request or not self.root.winfo_exists():
                    if thumbnail_path and os.path.exists(thumbnail_path):
                        try:
                            os.remove(thumbnail_path)
                        except OSError:
                            pass
                    return
                if thumbnail_path:
                    try:
                        photo = tk.PhotoImage(file=thumbnail_path)
                        self.preview_image_label.configure(image=photo, text="")
                        self._preview_photo = photo
                        old_path = self._preview_temp_path
                        self._preview_temp_path = thumbnail_path
                        if old_path and old_path != thumbnail_path and os.path.exists(old_path):
                            try:
                                os.remove(old_path)
                            except OSError:
                                pass
                    except Exception:
                        self.preview_image_label.configure(image="", text="プレビューを表示できません")
                else:
                    self.preview_image_label.configure(image="", text="プレビューなし")

            self.root.after(0, update_preview)

        threading.Thread(target=worker, daemon=True).start()


    def select_output_dir(self):
        directory = filedialog.askdirectory(title="圧縮後の保存先フォルダを選択")
        if directory:
            self.output_dir_path = directory
            self.output_entry.delete(0, tk.END)
            normalized = os.path.normpath(directory)
            self.output_entry.insert(0, f"{os.path.dirname(normalized)}  |  {os.path.basename(normalized)}")

    def start_compress(self):
        input_file = self.input_file_path
        output_dir = self.output_dir_path

        if not input_file:
            messagebox.showwarning("入力未選択", "動画ファイルを選択してください。")
            return

        if not os.path.exists(input_file):
            messagebox.showwarning("ファイル不在", "選択した動画ファイルが存在しません。")
            return

        if not output_dir:
            output_dir = os.path.dirname(input_file)
            self.output_dir_path = output_dir
            self.output_entry.delete(0, tk.END)
            normalized = os.path.normpath(output_dir)
            self.output_entry.insert(0, f"{os.path.dirname(normalized)}  |  {os.path.basename(normalized)}")

        if not os.path.exists(output_dir):
            messagebox.showwarning("出力先未設定", "保存先フォルダを選択してください。")
            return

        original_name = os.path.splitext(os.path.basename(input_file))[0]
        selected_format = self.output_format_var.get().lower()
        output_path = os.path.join(output_dir, f"{original_name}_optimized.{selected_format}")
        if os.path.abspath(input_file) == os.path.abspath(output_path):
            messagebox.showwarning("保存先エラー", "入力動画と出力先が同じファイルです。")
            return

        self.run_btn.config(state="disabled")
        self.compress_progress.configure(value=0)
        self.compress_percent_var.set("0%")
        self.eta_var.set("残り時間: 計測中...")
        self.status_var.set("圧縮処理を開始しています...")
        threading.Thread(target=self.compress_worker, args=(input_file, output_path, self.preserve_quality_var.get()), daemon=True).start()

    def update_compress_progress(self, percent, remaining_seconds):
        self.compress_progress.configure(value=percent)
        self.compress_percent_var.set(f"{percent:.1f}%")
        if remaining_seconds is None:
            eta_text = "残り時間: 計測中..."
        elif remaining_seconds <= 0:
            eta_text = "残り時間: 完了"
        else:
            seconds = int(remaining_seconds + 0.5)
            hours, rem = divmod(seconds, 3600)
            minutes, secs = divmod(rem, 60)
            if hours:
                eta_text = f"残り時間: 約{hours}時間{minutes}分"
            elif minutes:
                eta_text = f"残り時間: 約{minutes}分{secs}秒"
            else:
                eta_text = f"残り時間: 約{secs}秒"
        self.eta_var.set(eta_text)

    def compress_worker(self, input_file, output_path, preserve_quality=False):
        try:
            def report_progress(percent, remaining_seconds):
                self.root.after(0, self.update_compress_progress, percent, remaining_seconds)

            result = optimize_video(
                input_file, output_path, reduction_ratio=0.35,
                progress_callback=report_progress,
                preserve_quality=preserve_quality
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
                self.eta_var.set("残り時間: 完了")

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