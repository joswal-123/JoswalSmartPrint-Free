import os
import json
import uuid
import secrets
from pathlib import Path
from datetime import datetime, timezone, timedelta

from flask import Flask, request, jsonify, send_from_directory, render_template_string
from pypdf import PdfReader

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
JOBS_FILE = BASE_DIR / "jobs.json"
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
UPLOAD_PIN = os.getenv("PRINT_UPLOAD_PIN", "")
AGENT_TOKEN = os.getenv("PRINT_AGENT_TOKEN", "")
PROCESSING_TIMEOUT_MINUTES = 30

PAGE = """
<!doctype html>
<html lang="hi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Joswal Smart Print</title>
<style>
*{box-sizing:border-box}
body{margin:0;padding:16px;background:#f3f6fb;font-family:Arial,sans-serif;color:#172033}
.card{max-width:620px;margin:20px auto;padding:24px;background:white;border-radius:16px;box-shadow:0 3px 18px #0001}
h1{color:#174ea6}
label{display:block;margin-top:16px;font-weight:bold}
input,select,button{width:100%;padding:12px;margin-top:7px;border:1px solid #ccd4e0;border-radius:8px;font-size:16px}
button{background:#174ea6;color:white;border:0;cursor:pointer}
button:disabled{opacity:.6}
.note{color:#667085;font-size:13px;line-height:1.5}
#message{overflow-wrap:anywhere}
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
<input id="fileInput" type="file" name="file" accept=".pdf,.jpg,.jpeg,.png" required>

<label>प्रिंट का प्रकार</label>
<select name="print_type" id="printType">
<option value="bw">Black &amp; White</option>
<option value="color">Colour</option>
<option value="id">ID Card</option>
<option value="photo">Passport Photo</option>
</select>

<label>PDF पेज चुनें</label>
<select id="pageMode" name="page_mode">
<option value="all">सभी पेज</option>
<option value="custom">चुने हुए पेज / रेंज</option>
</select>
<input id="pageRange" name="page_range" type="text"
 placeholder="उदाहरण: 1-3, 7, 10-12" style="display:none">
<p class="note">उदाहरण: 2,5,8 या 3-7 या 1-3,7,10-12। PDF के असली पेज नंबर लिखें।</p>

<label>कॉपी की संख्या (1–500)</label>
<input type="number" name="copies" min="1" max="500" value="1" required>

<button id="submitBtn" type="submit">अपलोड करें और कतार में भेजें</button>
</form>
<p id="message"></p>
<p class="note">
ID Card और Passport Photo का विशेष लेआउट अभी उपलब्ध नहीं है।
Render Free पर रीस्टार्ट होने से फाइलें खो सकती हैं। यह वेबसाइट भुगतान नहीं लेती।
</p>
</div>
<script>
const form=document.getElementById("uploadForm");
const message=document.getElementById("message");
const button=document.getElementById("submitBtn");
const fileInput=document.getElementById("fileInput");
const pageMode=document.getElementById("pageMode");
const pageRange=document.getElementById("pageRange");
const printType=document.getElementById("printType");

function updatePageControls(){
  const isPdf=fileInput.files.length && fileInput.files[0].name.toLowerCase().endsWith(".pdf");
  pageMode.disabled=!isPdf;
  pageRange.style.display=(isPdf && pageMode.value==="custom")?"block":"none";
  if(!isPdf){pageMode.value="all";pageRange.value="";}
}
fileInput.addEventListener("change",updatePageControls);
pageMode.addEventListener("change",updatePageControls);

form.addEventListener("submit",async(event)=>{
 event.preventDefault();
 button.disabled=true;
 message.textContent="अपलोड हो रहा है...";
 try{
   const response=await fetch("/api/upload",{method:"POST",body:new FormData(form)});
   const result=await response.json();
   if(!response.ok)throw new Error(result.error||"अपलोड विफल");
   message.textContent=result.message;
   form.reset();
   updatePageControls();
 }catch(error){message.textContent="त्रुटि: "+error.message;}
 finally{button.disabled=false;}
});
</script>
</body>
</html>
"""


def now():
    return datetime.now(timezone.utc)


def read_jobs():
    try:
        data = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def write_jobs(jobs):
    temp = JOBS_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(JOBS_FILE)


def agent_authorized():
    supplied = request.headers.get("X-Print-Agent-Token", "")
    return bool(AGENT_TOKEN) and secrets.compare_digest(supplied, AGENT_TOKEN)


def find_job(jobs, order_id):
    return next((j for j in jobs if j.get("order_id") == order_id), None)


def recover_stale_jobs(jobs):
    cutoff = now() - timedelta(minutes=PROCESSING_TIMEOUT_MINUTES)
    for job in jobs:
        if job.get("status") != "processing":
            continue
        try:
            started = datetime.fromisoformat(job["processing_started_at"])
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            if started < cutoff:
                job["status"] = "queued"
                job.pop("processing_started_at", None)
        except (KeyError, ValueError, TypeError):
            pass


def parse_page_range(value, total_pages):
    value = (value or "").strip()
    if not value:
        raise ValueError("PDF पेज नंबर लिखें।")

    selected = set()
    parts = value.split(",")

    if len(parts) > 100:
        raise ValueError("पेज चयन बहुत लंबा है।")

    for part in parts:
        part = part.strip()
        if not part:
            raise ValueError("पेज चयन में खाली हिस्सा है।")

        if "-" in part:
            bits = part.split("-")
            if len(bits) != 2 or not all(b.strip().isdigit() for b in bits):
                raise ValueError("रेंज ऐसे लिखें: 2-5, 8")
            start, end = (int(bits[0]), int(bits[1]))
            if start > end:
                raise ValueError("रेंज में पहला पेज आखिरी पेज से बड़ा नहीं होना चाहिए।")
        else:
            if not part.isdigit():
                raise ValueError("पेज नंबर केवल अंकों में लिखें।")
            start = end = int(part)

        if start < 1 or end > total_pages:
            raise ValueError(f"इस PDF में कुल {total_pages} पेज हैं। सही पेज नंबर चुनें।")

        if end - start > 5000:
            raise ValueError("एक रेंज में बहुत अधिक पेज हैं।")

        selected.update(range(start, end + 1))

    if not selected:
        raise ValueError("कम से कम एक पेज चुनें।")

    # Normalize ranges into SumatraPDF-compatible syntax.
    ordered = sorted(selected)
    ranges = []
    start = previous = ordered[0]

    for page in ordered[1:]:
        if page == previous + 1:
            previous = page
        else:
            ranges.append(str(start) if start == previous else f"{start}-{previous}")
            start = previous = page

    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(ranges)


@app.get("/")
def home():
    return render_template_string(PAGE)


@app.get("/health")
def health():
    return jsonify(status="ok", app="JoswalSmartPrint-Free")


@app.post("/api/upload")
def upload_file():
    if not UPLOAD_PIN:
        return jsonify(error="Render में PRINT_UPLOAD_PIN सेट करें।"), 503

    if not secrets.compare_digest(
    request.form.get("pin", "").encode("utf-8"),
    UPLOAD_PIN.encode("utf-8")
):
        return jsonify(error="PIN गलत है।"), 403

    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        return jsonify(error="पहले फाइल चुनें।"), 400

    ext = Path(uploaded.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify(error="केवल PDF, JPG, JPEG और PNG स्वीकार हैं।"), 400

    try:
        copies = int(request.form.get("copies", "1"))
    except ValueError:
        return jsonify(error="कॉपी संख्या गलत है।"), 400
    if not 1 <= copies <= 500:
        return jsonify(error="कॉपी 1 से 500 के बीच रखें।"), 400

    print_type = request.form.get("print_type", "color").lower()
    if print_type not in {"bw", "color", "id", "photo"}:
        return jsonify(error="प्रिंट प्रकार गलत है।"), 400

    if print_type in {"id", "photo"}:
        return jsonify(error="ID Card और Passport Photo का विशेष लेआउट अभी तैयार नहीं है।"), 400

    page_mode = request.form.get("page_mode", "all")
    page_range = ""

    if ext == ".pdf":
        try:
            # Read uploaded PDF once for page-count validation.
            uploaded.stream.seek(0)
            reader = PdfReader(uploaded.stream, strict=True)
            total_pages = len(reader.pages)
            if total_pages < 1:
                raise ValueError("PDF में कोई पेज नहीं है।")

            if page_mode == "custom":
                page_range = parse_page_range(
                    request.form.get("page_range", ""),
                    total_pages,
                )
            elif page_mode != "all":
                raise ValueError("पेज चयन गलत है।")
        except Exception as exc:
            return jsonify(error=f"PDF या पेज चयन की जाँच नहीं हुई: {str(exc)[:250]}"), 400
        finally:
            uploaded.stream.seek(0)
    elif page_mode == "custom":
        return jsonify(error="पेज चयन केवल PDF के लिए उपलब्ध है।"), 400

    order_id = uuid.uuid4().hex
    filename = order_id + ext
    destination = UPLOAD_DIR / filename
    uploaded.save(destination)

    job = {
        "order_id": order_id,
        "filename": filename,
        "original_name": Path(uploaded.filename).name[:180],
        "file_ext": ext,
        "copies": copies,
        "mode": print_type,
        "job_type": print_type,
        "page_range": page_range,
        "status": "queued",
        "created_at": now().isoformat(),
    }

    try:
        jobs = read_jobs()
        jobs.append(job)
        write_jobs(jobs)
    except OSError:
        destination.unlink(missing_ok=True)
        app.logger.exception("Job save failed")
        return jsonify(error="जॉब सेव नहीं हुई।"), 500

    return jsonify(
        message=f"फाइल कतार में भेज दी गई। Job ID: {order_id}",
        order_id=order_id,
    ), 201


@app.get("/api/print-jobs")
def get_print_jobs():
    if not agent_authorized():
        return jsonify(error="Unauthorized agent"), 401

    jobs = read_jobs()
    recover_stale_jobs(jobs)

    job = next((j for j in jobs if j.get("status") == "queued"), None)
    if job:
        job["status"] = "processing"
        job["processing_started_at"] = now().isoformat()
        job["attempts"] = int(job.get("attempts", 0)) + 1
        write_jobs(jobs)
        return jsonify(jobs=[dict(job)])

    write_jobs(jobs)
    return jsonify(jobs=[])


@app.get("/api/print-jobs/<order_id>/file")
def download_print_job(order_id):
    if not agent_authorized():
        return jsonify(error="Unauthorized agent"), 401

    job = find_job(read_jobs(), order_id)
    if not job or job.get("status") != "processing":
        return jsonify(error="Job not found or not processing"), 404

    filename = job.get("filename", "")
    if Path(filename).name != filename or not (UPLOAD_DIR / filename).is_file():
        return jsonify(error="Uploaded file unavailable"), 404

    return send_from_directory(UPLOAD_DIR, filename, as_attachment=True)


@app.post("/api/print-jobs/<order_id>/complete")
def complete_print_job(order_id):
    if not agent_authorized():
        return jsonify(error="Unauthorized agent"), 401

    data = request.get_json(silent=True) or {}
    status = data.get("status")
    if status not in {"printed", "error"}:
        return jsonify(error="Invalid status"), 400

    jobs = read_jobs()
    job = find_job(jobs, order_id)
    if not job:
        return jsonify(error="Job not found"), 404
    if job.get("status") != "processing":
        return jsonify(error="Job is not processing"), 409

    job["status"] = status
    job["completed_at"] = now().isoformat()
    job.pop("processing_started_at", None)

    if status == "error":
        job["error"] = str(data.get("message", "Print failed"))[:500]

    write_jobs(jobs)
    return jsonify(ok=True, order_id=order_id, status=status)


@app.errorhandler(413)
def file_too_large(_error):
    return jsonify(error="फाइल 20 MB से बड़ी है।"), 413


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
)
