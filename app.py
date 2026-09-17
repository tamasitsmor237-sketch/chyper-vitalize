from flask import Flask, render_template, request, send_file, jsonify
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from io import BytesIO
import base64, re

app = Flask(__name__)

@app.get('/')
def index():
    return render_template('index.html')

@app.get('/health')
def health():
    return jsonify(status='ok')

@app.post('/api/pdf')
def pdf():
    data = request.get_json(silent=True) or {}
    image = data.get('image', '')
    if not image.startswith('data:image/') or ',' not in image:
        return jsonify(error='missing image'), 400
    try:
        raw = base64.b64decode(image.split(',', 1)[1])
        img = ImageReader(BytesIO(raw))
        iw, ih = img.getSize()
        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        pw, ph = A4
        scale = min(pw / iw, ph / ih)
        w, h = iw * scale, ih * scale
        c.drawImage(img, (pw-w)/2, (ph-h)/2, width=w, height=h, preserveAspectRatio=True, mask='auto')
        c.showPage()
        c.save()
        buf.seek(0)
        name = re.sub(r'[^A-Za-z0-9_-]+', '_', str(data.get('name') or 'CV')).strip('_') or 'CV'
        return send_file(buf, mimetype='application/pdf', as_attachment=True, download_name=f'Chyper_Vitalize_{name}.pdf')
    except Exception:
        return jsonify(error='pdf generation failed'), 500
