import os
import json
import subprocess
import requests
from faster_whisper import WhisperModel

API_KEY = os.environ.get("GROQ_API_KEY")
API_URL = "https://api.groq.com/openai/v1/chat/completions"
VIDEO_URL = os.environ.get("VIDEO_URL")

os.makedirs("raw_videos", exist_ok=True)
os.makedirs("output_clips", exist_ok=True)
source_file = "raw_videos/vod.mp4"

print(f"[1/4] Fetching video stream via cloud extractor proxy for: {VIDEO_URL}")

api_endpoints = [
    "https://api.cobalt.tools/api/json",
    "https://co.wuk.sh/api/json"
]

download_success = False
for api in api_endpoints:
    try:
        response = requests.post(
            api,
            json={"url": VIDEO_URL, "videoQuality": "720"},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=20
        )
        data = response.json()
        if "url" in data:
            media_url = data["url"]
            print(f"-> Successfully retrieved stream via {api}")
            stream = requests.get(media_url, stream=True)
            with open(source_file, "wb") as f:
                for chunk in stream.iter_content(chunk_size=8192):
                    f.write(chunk)
            download_success = True
            break
    except Exception as e:
        print(f"Endpoint {api} failed: {e}")
        continue

if not download_success:
    raise Exception("All cloud extraction endpoints failed. Please check your video URL.")

print("[2/4] Transcribing audio & extracting word timestamps...")
model = WhisperModel("base.en", device="cpu", compute_type="int8")
segments, _ = model.transcribe(source_file, vad_filter=True, word_timestamps=True)

transcript_data = []
all_words = []

for i, seg in enumerate(segments):
    transcript_data.append({
        "id": i,
        "start": round(seg.start, 2),
        "end": round(seg.end, 2),
        "text": seg.text.strip()
    })
    if seg.words:
        for w in seg.words:
            all_words.append({
                "word": w.word.strip().upper(),
                "start": round(w.start, 2),
                "end": round(w.end, 2)
            })

print("[3/4] AI analyzing transcript for peak viral moments...")
prompt = f"""
Analyze this video transcript and extract the top 3 most entertaining, high-energy, or dramatic moments for viral YouTube Shorts/Reels (each 20 to 45 seconds long).
If the video is under 60 seconds total, just return 1 clip covering the whole video.
Return ONLY a valid JSON array of objects with exact keys: "start_time" (float seconds), "end_time" (float seconds), and "hook" (short 3-5 word ALL-CAPS hook).
Do not include markdown formatting or backticks.
Transcript: {json.dumps(transcript_data[:600])}
"""

ai_response = requests.post(
    API_URL,
    headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
    json={
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3
    }
)

raw_content = ai_response.json()["choices"][0]["message"]["content"].strip()
if raw_content.startswith("```"):
    raw_content = raw_content.strip("`").replace("json", "").strip()

clips_to_process = json.loads(raw_content)
print(f"-> AI selected {len(clips_to_process)} clips to produce.")

def fmt_ass(seconds):
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    cs = int((seconds - int(seconds)) * 100)
    return f"{hrs}:{mins:02}:{secs:02}.{cs:02}"

print("[4/4] Editing, upscaling, and burning Hormozi captions...")
for idx, clip in enumerate(clips_to_process, start=1):
    start = float(clip["start_time"])
    end = float(clip["end_time"])
    hook = clip["hook"].upper().replace("'", "").replace(":", "")
    print(f"-> Rendering Clip #{idx} ({start}s to {end}s) | Hook: {hook}")

    subclip_path = f"output_clips/temp_raw_{idx}.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-ss", str(start), "-to", str(end), "-i", source_file,
        "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", subclip_path
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    clip_words = [w for w in all_words if start <= w["start"] <= end]
    ass_path = f"output_clips/captions_{idx}.ass"
    
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n")
        f.write("[V4+ Styles]\n")
        f.write("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")
        f.write("Style: Hormozi,DejaVu Sans,85,&H0000FFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,3,2,50,50,480,1\n\n")
        f.write("[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")

        chunk_size = 3
        for i in range(0, len(clip_words), chunk_size):
            chunk = clip_words[i:i+chunk_size]
            c_start = max(0, chunk[0]["start"] - start)
            c_end = max(c_start + 0.2, chunk[-1]["end"] - start)
            
            styled_words = []
            for j, w_obj in enumerate(chunk):
                if j == len(chunk) - 1 and len(chunk) > 1:
                    styled_words.append(f"{{\\c&H00FFFFFF&}}{w_obj['word']}{{\\c&H0000FFFF&}}")
                else:
                    styled_words.append(w_obj["word"])
            
            line_text = " ".join(styled_words)
            f.write(f"Dialogue: 0,{fmt_ass(c_start)},{fmt_ass(c_end)},Hormozi,,0,0,0,,{line_text}\n")

    vf_chain = (
        "split[orig][copy];"
        "[copy]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma=28[bg];"
        "[orig]scale=1080:-2:flags=lanczos,unsharp=5:5:1.0:5:5:0.0[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,"
        f"drawtext=text='{hook}':fontcolor=white:fontsize=68:box=1:boxcolor=black@0.85:boxborderw=20:x=(w-text_w)/2:y=260,"
        f"ass='{ass_path}'"
    )

    final_output = f"output_clips/FINAL_SHORT_{idx}.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-i", subclip_path,
        "-vf", vf_chain,
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-c:v", "libx264", "-preset", "slow", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        final_output
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if os.path.exists(subclip_path): os.remove(subclip_path)
    if os.path.exists(ass_path): os.remove(ass_path)

print("ALL DONE! Ready-to-post Shorts saved in output_clips/")
