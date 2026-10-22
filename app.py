import os
import json
import uuid
import secrets
from pathlib import Path
from datetime import datetime, timezone

from flask import (
    Flask,
    request,
    jsonify,
    send_from_directory,
    render_template_string,
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
JOBS_FILE = BASE_DIR / "jobs.json"

UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}

# Render Environment में अलग और मजबूत PIN तथा Agent Token सेट करें।
UPLOAD_PIN = os.getenv("PRINT_UPLOAD_PIN", "")
AGENT_TOKEN = os.getenv("PRINT_AGENT_TOKEN", "")

PAGE = """
<!doctype html>
<html lang="hi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Joswal Smart Print</title>
<style>
* { box-sizing: border-box; }
body {
  margin: 0; padding: 16px; background: #f3f6fb;
  font-family: Arial, sans-serif; color: #172033;
}
.card {
  max-width: 620px; margin: 20px auto; padding: 24px;
  background: white; border-radius: 16px;
  box-shadow: 0 3px 18px #0001;
}
h1 { color: #174ea6; }
label { display: block; margin-top: 16px; font-weight: bold; }
input, select, button {
  width: 100%; padding: 12px; margin-top: 7px;
  border: 1px solid #ccd4e0; border-radius: 8px;
  font-size: 16px;
}
button { background: #174ea6; color: white; border: 0; cursor: pointer; }
button:disabled { opacity: .6; }
#message { overflow-wrap: anywhere; }
.note { color: #667085; font-size: 13px; line-height: 1.5; }
</style>
</head>
<body>
<div class="card">
  <h1>Joswal Smart Print</h1>
  <p>PDF और फोटो अपलोड करें</p>

  <form id="uploadForm">
    <label>निजी अपलोड PIN</label>
    <input name="pin" type="password" required autocomplete="off">

    <label>फाइल चुनें (PDF/JPG/PNG)</label>
    <input type="file" name="file"
           accept=".pdf,.jpg,.jpeg,.png" required>

    <label>प्रिंट का प्रकार</label>
    <select name="print_type">
      <option value="bw">Black &amp; White</option>
      <option value="color">Colour</option>
      <option value="id">ID Card</option>
      <option value="photo">Passport Photo</option>
    </select>

    <label>कॉपी की संख्या (1–500)</label>
    <input type="number" name="copies"
           min="1" max="500" value="1" required>

    <button id="submitBtn" type="submit">
      अपलोड करें और कतार में भेजें
    </button>
  </form>

  <p id="message"></p>
  <p class="note">
    भुगतान इस वेबसाइट पर नहीं है। PIN सेट होने तक अपलोड बंद रहेगा।
    ID Card और Passport Photo के विशेष लेआउट अभी लागू नहीं हैं।
    Render Free पर रीस्टार्ट होने से अपलोड की फाइलें खो सकती हैं।
  </p>
</div>

<script>
const form = document.getElementById("uploadForm");
const message = document.getElementById("message");
const button = document.getElementById("submitBtn");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  button.disabled = true;
  message.textContent = "अपलोड हो रहा है...";

  try {
    const response = await fetch("/api/upload", {
      method: "POST",
      body: new FormData(form)
    });
    const result = await response.json();

    if (!response.ok) {
      throw new Error(result.error || "अपलोड विफल");
    }

    message.textContent = result.message;
    form.reset();
  } catch (error) {
    message.textContent = "त्रुटि: " + error.message;
  } finally {
    button.disabled = false;
  }
});
</script>
</body>
</html>
"""


def read_jobs():
    try:
        data = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def write_jobs(jobs):
    temp_file = JOBS_FILE.with_suffix(".tmp")
    temp_file.write_text(
        json.dumps(jobs, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_file.replace(JOBS_FILE)


def agent_authorized():
    supplied = request.headers.get("X-Print-Agent-Token", "")
    return (
        bool(AGENT_TOKEN)
        and secrets.compare_digest(supplied, AGENT_TOKEN)
    )


@app.get("/")
def home():
    return render_template_string(PAGE)


@app.get("/health")
def health():
    return jsonify(status="ok", app="JoswalSmartPrint-Free")


@app.post("/api/upload")
def upload_file():
    # PIN सेट नहीं है तो कोई अपलोड स्वीकार नहीं होगा।
    if not UPLOAD_PIN:
        return jsonify(
            error="Admin setup बाकी है। Render में PRINT_UPLOAD_PIN सेट करें।"
        ), 503

    supplied_pin = request.form.get("pin", "")
    if not secrets.compare_digest(supplied_pin, UPLOAD_PIN):
        return jsonify(error="PIN गलत है।"), 403

    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        return jsonify(error="पहले फाइल चुनें।"), 400

    extension = Path(uploaded.filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        return jsonify(
            error="केवल PDF, JPG, JPEG और PNG फाइल स्वीकार हैं।"
        ), 400

    try:
        copies = int(request.form.get("copies", "1"))
    except ValueError:
        return jsonify(error="कॉपी की संख्या गलत है।"), 400

    if not 1 <= copies <= 500:
        return jsonify(error="कॉपी 1 से 500 के बीच रखें।"), 400

    print_type = request.form.get("print_type", "color").lower()
    if print_type not in {"bw", "color", "id", "photo"}:
        return jsonify(error="प्रिंट प्रकार गलत है।"), 400

    order_id = uuid.uuid4().hex
    filename = order_id + extension

    uploaded.save(UPLOAD_DIR / filename)

    job = {
        "order_id": order_id,
        "filename": filename,
        "original_name": Path(uploaded.filename).name[:180],
        "file_ext": extension,
        "copies": copies,
        "mode": "bw" if print_type == "bw" else "color",
        "job_type": print_type,
        "status": "queued",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    jobs = read_jobs()
    jobs.append(job)
    write_jobs(jobs)

    return jsonify(
        message="फाइल कतार में भेज दी गई। Job ID: " + order_id,
        order_id=order_id,
    ), 201


@app.get("/api/print-jobs")
def get_print_jobs():
    if not agent_authorized():
        return jsonify(error="Unauthorized agent"), 401

    jobs = read_jobs()
    queued = [
        dict(job) for job in jobs
        if job.get("status") == "queued"
    ]

    queued_ids = {job["order_id"] for job in queued}
    for job in jobs:
        if job.get("order_id") in queued_ids:
            job["status"] = "processing"

    if queued_ids:
        write_jobs(jobs)

    return jsonify(jobs=queued)


@app.get("/api/print-jobs/<order_id>/file")
def download_print_job(order_id):
    if not agent_authorized():
        return jsonify(error="Unauthorized agent"), 401

    job = next(
        (item for item in read_jobs()
         if item.get("order_id") == order_id),
        None,
    )

    if not job or job.get("status") not in {"queued", "processing"}:
        return jsonify(error="Job not found"), 404

    return send_from_directory(
        UPLOAD_DIR,
        job["filename"],
        as_attachment=True,
    )


@app.post("/api/print-jobs/<order_id>/complete")
def complete_print_job(order_id):
    if not agent_authorized():
        return jsonify(error="Unauthorized agent"), 401

    data = request.get_json(silent=True) or {}
    status = data.get("status")

    if status not in {"printed", "error"}:
        return jsonify(error="Invalid status"), 400

    jobs = read_jobs()
    job = next(
        (item for item in jobs
         if item.get("order_id") == order_id),
        None,
    )

    if not job:
        return jsonify(error="Job not found"), 404

    job["status"] = status
    job["completed_at"] = datetime.now(timezone.utc).isoformat()

    if status == "error":
        job["error"] = str(data.get("message", "Print failed"))[:500]

    write_jobs(jobs)
    return jsonify(ok=True)


@app.errorhandler(413)
def file_too_large(_error):
    return jsonify(error="फाइल 20 MB से बड़ी है।"), 413


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
    )
