```python
import os
import uuid
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png"}


@app.route("/")
def home():
    return """
    <!DOCTYPE html>
    <html lang="hi">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Joswal Smart Print Free</title>
      <style>
        body {
          font-family: Arial, sans-serif;
          max-width: 650px;
          margin: 30px auto;
          padding: 20px;
          background: #f3f6fb;
        }
        .card {
          background: white;
          padding: 24px;
          border-radius: 14px;
          box-shadow: 0 3px 15px #0001;
        }
        h1 { color: #174ea6; }
        label { display: block; margin-top: 16px; }
        input, select, button {
          width: 100%;
          padding: 12px;
          margin-top: 7px;
          box-sizing: border-box;
        }
        button {
          background: #174ea6;
          color: white;
          border: 0;
          border-radius: 8px;
          cursor: pointer;
        }
        #message { overflow-wrap: anywhere; }
      </style>
    </head>
    <body>
      <div class="card">
        <h1>Joswal Smart Print</h1>
        <p>PDF और फोटो अपलोड करें</p>
        <form id="uploadForm">
          <label>फाइल चुनें (PDF/JPG/PNG)</label>
          <input type="file" name="file" accept=".pdf,.jpg,.jpeg,.png" required>

          <label>प्रिंट का प्रकार</label>
          <select name="print_type">
            <option value="bw">Black & White</option>
            <option value="color">Colour</option>
            <option value="id">ID Card</option>
            <option value="photo">Passport Photo</option>
          </select>

          <label>कॉपी की संख्या</label>
          <input type="number" name="copies" min="1" max="500" value="1" required>

          <button type="submit">फाइल अपलोड करें</button>
        </form>
        <p id="message"></p>
      </div>

      <script>
        document.getElementById("uploadForm").addEventListener("submit", async function(e) {
          e.preventDefault();
          const message = document.getElementById("message");
          message.textContent = "अपलोड हो रहा है...";
          try {
            const response = await fetch("/api/upload", {
              method: "POST",
              body: new FormData(this)
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || "अपलोड विफल");
            message.textContent = result.message;
          } catch (error) {
            message.textContent = "त्रुटि: " + error.message;
          }
        });
      </script>
    </body>
    </html>
    """


@app.route("/api/upload", methods=["POST"])
def upload_file():
    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        return jsonify(error="पहले फाइल चुनें।"), 400

    extension = uploaded.filename.rsplit(".", 1)[-1].lower()
    if "." not in uploaded.filename or extension not in ALLOWED_EXTENSIONS:
        return jsonify(error="केवल PDF, JPG, JPEG और PNG फाइल स्वीकार हैं।"), 400

    copies = request.form.get("copies", "1")
    if not copies.isdigit() or not 1 <= int(copies) <= 500:
        return jsonify(error="कॉपी की संख्या 1 से 500 के बीच रखें।"), 400

    token = uuid.uuid4().hex
    safe_name = f"{token}.{extension}"
    uploaded.save(UPLOAD_DIR / safe_name)

    return jsonify(
        message="फाइल अपलोड हो गई। अभी पेमेंट और ऑटो-प्रिंट सक्रिय नहीं हैं।",
        file_id=token,
        copies=int(copies),
        print_type=request.form.get("print_type", "bw")
    ), 201


@app.route("/api/file/<token>")
def get_file(token):
    for path in UPLOAD_DIR.iterdir():
        if path.is_file() and path.stem == token:
            return send_from_directory(UPLOAD_DIR, path.name)
    return jsonify(error="फाइल नहीं मिली।"), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
```
