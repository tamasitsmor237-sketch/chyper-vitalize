from flask import Flask, render_template, request, send_file, jsonify
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from io import BytesIO
import base64, re, os
import stripe
from openai import OpenAI

app = Flask(__name__)

@app.get('/')
def index():
    return render_template('index.html')

@app.get('/health')
def health():
    return jsonify(status='ok')

@app.post('/api/create-checkout-session')
def create_checkout_session():
    data = request.get_json(silent=True) or {}
    referral = bool(data.get('referral'))
    amount = 199 if referral else 399

    secret_key = os.environ.get('STRIPE_SECRET_KEY')
    if not secret_key:
        return jsonify(error='Stripe is not configured'), 500

    stripe.api_key = secret_key
    base_url = request.host_url.rstrip('/')

    try:
        session = stripe.checkout.Session.create(
            mode='payment',
            line_items=[{
                'price_data': {
                    'currency': 'eur',
                    'product_data': {'name': 'Chyper Vitalize CV'},
                    'unit_amount': amount,
                },
                'quantity': 1,
            }],
            success_url=base_url + '/?payment=success&session_id={CHECKOUT_SESSION_ID}',
            cancel_url=base_url + '/?payment=cancelled',
            metadata={'referral': 'true' if referral else 'false'},
        )
        return jsonify(url=session.url)
    except Exception:
        return jsonify(error='checkout creation failed'), 500

@app.get('/api/payment-status')
def payment_status():
    session_id = request.args.get('session_id', '')
    secret_key = os.environ.get('STRIPE_SECRET_KEY')
    if not secret_key or not session_id:
        return jsonify(paid=False), 400
    stripe.api_key = secret_key
    try:
        session = stripe.checkout.Session.retrieve(session_id)
        return jsonify(paid=(session.payment_status == 'paid'))
    except Exception:
        return jsonify(paid=False), 400

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


@app.post('/api/ai-interview')
def ai_interview():
    data = request.get_json(silent=True) or {}
    answers = data.get('answers') or {}
    language = str(data.get('language') or 'English')
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        return jsonify(error='AI is not configured'), 500
    try:
        import json
        client = OpenAI(api_key=api_key)
        prompt = '''Create a professional CV draft only from the candidate answers. Return ONLY valid JSON with keys: name, role, email, phone, place, profile, skills, experience, education, languages. skills must be a short comma-separated string. experience and education must each be concise CV-ready text based only on the corresponding candidate answers. languages must contain only the candidate language knowledge and proficiency levels, without inventing any. Write role, profile and skills in the requested language. Never invent facts; use an empty string when missing. Requested language: %s\nCandidate answers: %s''' % (language, json.dumps(answers, ensure_ascii=False))
        response = client.responses.create(model=os.environ.get('OPENAI_MODEL','gpt-5.6-luna'), input=prompt, store=False)
        raw = response.output_text.strip()
        raw = re.sub(r'^```(?:json)?\\s*|\\s*```$', '', raw, flags=re.I)
        cv = json.loads(raw)
        allowed = ['name','role','email','phone','place','profile','skills','experience','education','languages']
        return jsonify({k: str(cv.get(k) or '') for k in allowed})
    except Exception as e:
        return jsonify(error='AI generation failed'), 500
