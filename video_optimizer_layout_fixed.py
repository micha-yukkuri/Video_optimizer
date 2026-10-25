import os
import json
import threading
import shutil
import subprocess
import tempfile
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# ============================================================
# 最終版（ボタン確実表示）
# ffmpeg.exe / ffprobe.exe を同じフォルダに置いて実行
# ============================================================

def find_ffmpeg():
    base_dir = os.path.dirname(os.path.abspath(__file__))

    ffmpeg_candidates = [
        os.path.join(base_dir, "ffmpeg.exe"),
        os.path.join(base_dir, "ffmpeg"),
    ]
    ffprobe_candidates = [
        os.path.join(base_dir, "ffprobe.exe"),
        os.path.join(base_dir, "ffprobe"),
    ]

    for ff in ffmpeg_candidates:
        if os.path.exists(ff):
            for fp in ffprobe_candidates:
                if os.path.exists(fp):
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
        if os.path.exists(ffmpeg_path):
            if ffprobe_path and os.path.exists(ffprobe_path):
                return ffmpeg_path, ffprobe_path
            alt_probe = os.path.join(os.path.dirname(ffmpeg_path), "ffprobe.exe")
            if os.path.exists(alt_probe):
                return ffmpeg_path, alt_probe

    raise RuntimeError(
        "ffmpeg / ffprobe が見つかりません。\n"
        "ffmpeg をインストールして、ffmpeg.exe と ffprobe.exe をこのアプリと同じフォルダに置いてください。\n"
        "または PATH に追加してください。"
    )

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
        "-progress", "pipe:1",
        "-nostats",
        output_path
    ]

    # FFmpegの進捗を標準出力から読み取り、標準エラーは一時ファイルに退避する。
    # これにより、長時間の圧縮中も進捗を更新しつつパイプ詰まりを防ぐ。
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as error_file:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=error_file,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1
        )
        if proc.stdout is not None:
            for line in proc.stdout:
                line = line.strip()
                if not line or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key == "out_time_us":
                    try:
                        elapsed = int(value) / 1_000_000
                        percent = min(99.0, max(0.0, elapsed / metadata["duration"] * 100)) if metadata["duration"] > 0 else 0.0
                        if progress_callback:
                            progress_callback(percent)
                    except (ValueError, ZeroDivisionError):
                        pass
                # progress=end は FFmpeg プロセスの終了を示すだけで、
                # 出力動画が元と同じ長さであることは保証しない。
                # 100% 表示は出力検証が成功してから行う。
            proc.stdout.close()
        return_code = proc.wait()
        if return_code != 0:
            error_file.seek(0)
            error_text = error_file.read().strip()
            raise RuntimeError(f"圧縮失敗: {error_text or f'FFmpeg終了コード {return_code}'}")

    # 圧縮後のファイルが生成され、元動画とほぼ同じ長さかを必ず検証する。
    # FFmpeg が終了コード 0 を返しても、短い出力を成功扱いしない。
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("圧縮出力が作成されなかったか、ファイルサイズが 0 です。")

    output_metadata = get_video_metadata(output_path)
    input_duration = metadata["duration"]
    output_duration = output_metadata["duration"]
    if input_duration > 0 and output_duration > 0:
        duration_ratio = output_duration / input_duration
        if duration_ratio < 0.95:
            raise RuntimeError(
                "圧縮後の動画が元動画より大幅に短いため、成功扱いにしません。\n"
                f"元動画: {input_duration:.2f} 秒 / 出力: {output_duration:.2f} 秒\n"
                "元動画は保持されています。FFmpeg のログと元動画の情報を確認してください。"
            )

    if progress_callback:
        progress_callback(100.0)

    return {
        "original_size": os.path.getsize(input_path),
        "compressed_size": os.path.getsize(output_path),
        "original_duration": metadata["duration"],
        "compressed_duration": output_metadata["duration"],
    }

class VideoOptimizerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("動画圧縮ツール")
        self.root.geometry("800x760")
        self.root.minsize(740, 700)
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

        body = tk.Frame(panel, bg="#ffffff", padx=20, pady=18)
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

        status_box = tk.Frame(body, bg="#f4f5ff", padx=14, pady=12)
        status_box.pack(fill="both", expand=True)

        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress_text_var = tk.StringVar(value="進捗: 0%")
        progress_header = tk.Frame(status_box, bg="#f4f5ff")
        progress_header.pack(fill="x", pady=(0, 6))
        tk.Label(progress_header, text="圧縮の進捗", bg="#f4f5ff", fg="#48556a", font=("Arial", 10, "bold")).pack(side="left")
        tk.Label(progress_header, textvariable=self.progress_text_var, bg="#f4f5ff", fg="#48556a", font=("Arial", 10, "bold")).pack(side="right")
        self.progress_bar = ttk.Progressbar(status_box, orient="horizontal", mode="determinate", maximum=100, variable=self.progress_var, length=600)
        self.progress_bar.pack(fill="x", pady=(0, 10))

        self.status_var = tk.StringVar(value="待機中")
        self.status_label = tk.Label(
            status_box, textvariable=self.status_var, bg="#f4f5ff", fg="#48556a",
            justify="left", anchor="w", wraplength=700
        )
        self.status_label.pack(anchor="w", fill="x")

        # 圧縮完了時の結果を2×2のカードで表示する
        self.result_grid = tk.Frame(status_box, bg="#f4f5ff")
        self.result_grid.columnconfigure(0, weight=1, uniform="result")
        self.result_grid.columnconfigure(1, weight=1, uniform="result")
        self.result_vars = [tk.StringVar(value="—") for _ in range(4)]
        result_cards = [
            ("元のサイズ", 0, 0),
            ("圧縮後のサイズ", 0, 1),
            ("動画の長さ", 1, 0),
            ("容量の削減率", 1, 1),
        ]
        for i, (label_text, row, col) in enumerate(result_cards):
            card = tk.Frame(self.result_grid, bg="#ffffff", padx=12, pady=9,
                            highlightthickness=1, highlightbackground="#e0e5f2")
            card.grid(row=row, column=col, sticky="nsew", padx=4, pady=4)
            tk.Label(card, text=label_text, bg="#ffffff", fg="#64748b",
                     font=("Arial", 9, "bold"), anchor="w").pack(fill="x")
            tk.Label(card, textvariable=self.result_vars[i], bg="#ffffff", fg="#263449",
                     font=("Arial", 12, "bold"), anchor="w", justify="left",
                     wraplength=290).pack(fill="x", pady=(4, 0))

        self.result_path_var = tk.StringVar(value="")
        self.result_path_label = tk.Label(
            status_box, textvariable=self.result_path_var, bg="#f4f5ff", fg="#48556a",
            justify="left", anchor="w", wraplength=700
        )

        # ウィンドウ幅に合わせて折り返し幅を調整し、結果テキストの見切れを防ぐ。
        def adjust_wraplength(event=None):
            available = max(320, self.root.winfo_width() - 130)
            self.status_label.configure(wraplength=available)
            self.result_path_label.configure(wraplength=available)
            for card in self.result_grid.winfo_children():
                for child in card.winfo_children():
                    if isinstance(child, tk.Label) and child.cget("textvariable"):
                        child.configure(wraplength=max(180, (available - 50) // 2))

        self.root.bind("<Configure>", adjust_wraplength)

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
        self.progress_var.set(0.0)
        self.progress_text_var.set("進捗: 0%")
        self.result_grid.pack_forget()
        self.result_path_label.pack_forget()
        self.status_label.pack(anchor="w", fill="x")
        self.status_var.set("動画情報を確認して圧縮を開始しています...")
        threading.Thread(target=self.compress_worker, args=(input_file, output_path), daemon=True).start()

    def update_progress(self, percent):
        # このメソッドはTkinterのメインスレッドから呼び出す。
        self.progress_var.set(percent)
        self.progress_text_var.set(f"進捗: {percent:.0f}%")
        self.status_var.set("圧縮中です... 完了までしばらくお待ちください。")

    def show_compression_result(self, original_mb, compressed_mb,
                                original_duration, compressed_duration,
                                reduction, output_path):
        self.status_var.set("圧縮が完了しました")
        self.status_label.pack_forget()
        self.result_vars[0].set(f"{original_mb:.2f} MB")
        self.result_vars[1].set(f"{compressed_mb:.2f} MB")
        self.result_vars[2].set(f"{original_duration:.1f} 秒 → {compressed_duration:.1f} 秒")
        self.result_vars[3].set(f"{reduction:.1f}% 削減")
        self.result_grid.pack(fill="x", pady=(8, 0))
        self.result_path_var.set(f"保存先: {output_path}")
        self.result_path_label.pack(anchor="w", fill="x", pady=(8, 0))

    def compress_worker(self, input_file, output_path):
        try:
            result = optimize_video(
                input_file,
                output_path,
                reduction_ratio=0.35,
                progress_callback=lambda percent: self.root.after(0, self.update_progress, percent)
            )
            original_mb = result["original_size"] / (1024 * 1024)
            compressed_mb = result["compressed_size"] / (1024 * 1024)
            reduction = (1 - (result["compressed_size"] / result["original_size"])) * 100

            original_duration = result["original_duration"]
            compressed_duration = result["compressed_duration"]
            self.root.after(0, self.show_compression_result,
                            original_mb, compressed_mb, original_duration,
                            compressed_duration, reduction, output_path)
            self.root.after(0, lambda: self.run_btn.config(state="normal"))
            self.root.after(0, lambda: messagebox.showinfo("完了", f"圧縮が完了しました。\n保存先: {output_path}"))

        except Exception as e:
            error_message = str(e)
            self.root.after(0, lambda error_message=error_message: self.status_var.set(f"エラー: {error_message}"))
            self.root.after(0, lambda: self.progress_text_var.set("進捗: エラー"))
            self.root.after(0, lambda: self.run_btn.config(state="normal"))
            self.root.after(0, lambda error_message=error_message: messagebox.showerror("エラー", error_message))

if __name__ == "__main__":
    root = tk.Tk()
    app = VideoOptimizerApp(root)
    root.mainloop()