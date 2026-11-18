import os
import json
import subprocess
import requests
from faster_whisper import WhisperModel

API_KEY = os.environ.get("GROQ_API_KEY")
API_URL = "https://api.groq.com/openai/v1/chat/completions"
VIDEO_URL = os.environ.get("VIDEO_URL")

os.makedirs("source_media", exist_ok=True)
os.makedirs("output_clips", exist_ok=True)

print("[1/5] Downloading VOD source via yt-dlp...")
source_file = "source_media/vod.mp4"
subprocess.run([
    "yt-dlp", "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
    "-o", source_file, VIDEO_URL
], check=True)

print("[2/5] Transcribing audio with Whisper...")
model = WhisperModel("base.en", device="cpu", compute_type="int8")
segments, _ = model.transcribe(source_file, vad_filter=True)

transcript_data = []
for i, seg in enumerate(segments):
    transcript_data.append({
        "id": i,
        "start": round(seg.start, 2),
        "end": round(seg.end, 2),
        "text": seg.text.strip()
    })

print("[3/5] Asking AI to find multiple viral highlights...")
prompt = f"""
Analyze this transcript and find the top 3 most exciting, dramatic, or funny moments suitable for viral Shorts (each 20 to 40 seconds long).
Return ONLY a valid JSON array of objects with keys: "start_time" (float seconds), "end_time" (float seconds), and "hook" (short ALL-CAPS hook text).
Transcript chunk: {json.dumps(transcript_data[:600])}
"""

response = requests.post(
    API_URL,
    headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
    json={
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3
    }
)

clips_to_process = json.loads(response.json()["choices"]["message"]["content"].strip())
print(f"-> AI found {len(clips_to_process)} viral moments to produce.")

print("[4/5] Cutting, cropping 9:16, applying Hormozi captions & rendering...")
for idx, clip in enumerate(clips_to_process, start=1):
    start = clip["start_time"]
    end = clip["end_time"]
    hook = clip["hook"].upper()

    subclip_path = f"output_clips/raw_clip_{idx}.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-ss", str(start), "-to", str(end), "-i", source_file,
        "-c:v", "libx264", "-c:a", "aac", subclip_path
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    srt_path = f"output_clips/clip_{idx}.srt"
    with open(srt_path, "w", encoding="utf-8") as f:
        sub_idx = 1
        for seg in transcript_data:
            if start <= seg["start"] <= end:
                s_adj = seg["start"] - start
                e_adj = seg["end"] - start
                def fmt(s):
                    return f"{int(s//3600):02}:{int((s%3600)//60):02}:{int(s%60):02},{int((s-int(s))*1000):03}"
                f.write(f"{sub_idx}\n{fmt(s_adj)} --> {fmt(e_adj)}\n{seg['text'].upper()}\n\n")
                sub_idx += 1

    srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:")
    
    vf_chain = (
        "split[orig][copy];"
        "[copy]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma=25[bg];"
        "[orig]scale=1080:-2[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,"
        f"drawtext=text='{hook}':fontcolor=yellow:fontsize=64:box=1:boxcolor=black@0.8:boxborderw=16:x=(w-text_w)/2:y=280,"
        f"subtitles='{srt_escaped}':force_style='FontName=DejaVu Sans,FontSize=22,PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=4,Alignment=2,MarginV=420',"
        "scale=1080:1920:flags=lanczos"
    )

    final_output = f"output_clips/viral_short_{idx}_ready.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-i", subclip_path,
        "-vf", vf_chain,
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        final_output
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

print("[5/5] ALL CLIPS PROCESSED SUCCESSFULLY!")
