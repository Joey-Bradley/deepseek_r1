import streamlit as st
from dotenv import load_dotenv
import os
import requests
import markdown
from xhtml2pdf import pisa
import io
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from datetime import datetime
import re
import time

# ─── Configuration ────────────────────────────────
load_dotenv()

# DeepSeek API
try:
    DEEPSEEK_API_KEY = st.secrets["DEEPSEEK_API_KEY"]
except (FileNotFoundError, KeyError, ValueError, st.errors.StreamlitSecretNotFoundError):
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

# Simple, strict email check: no whitespace/control chars, one @, a dot after it.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Basic per-session cost/abuse control
MIN_SECONDS_BETWEEN_REQUESTS = 15
MAX_REQUESTS_PER_SESSION = 20

# Email SMTP Settings
try:
    SMTP_HOST = st.secrets.get("SMTP_HOST") or os.getenv("SMTP_HOST", "smtp.porkbun.com")
    SMTP_PORT = int(st.secrets.get("SMTP_PORT") or os.getenv("SMTP_PORT", 587))
    SMTP_USER = st.secrets.get("SMTP_USER") or os.getenv("SMTP_USER")
    SMTP_PASS = st.secrets.get("SMTP_PASS") or os.getenv("SMTP_PASS")
    SMTP_FROM_NAME = st.secrets.get("SMTP_FROM_NAME") or os.getenv("SMTP_FROM_NAME", "Car Code Decoder")
except Exception:
    SMTP_HOST = os.getenv("SMTP_HOST", "smtp.porkbun.com")
    SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
    SMTP_USER = os.getenv("SMTP_USER")
    SMTP_PASS = os.getenv("SMTP_PASS")
    SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "Car Code Decoder")

# Access code (given to paying customers after purchase)
try:
    ACCESS_CODE = st.secrets.get("ACCESS_CODE") or os.getenv("ACCESS_CODE")
except Exception:
    ACCESS_CODE = os.getenv("ACCESS_CODE")

# ─── Session State ────────────────────────────────
if "output_text" not in st.session_state:
    st.session_state.output_text = None
if "last_code" not in st.session_state:
    st.session_state.last_code = None
if "last_vehicle" not in st.session_state:
    st.session_state.last_vehicle = None
if "request_count" not in st.session_state:
    st.session_state.request_count = 0
if "last_request_time" not in st.session_state:
    st.session_state.last_request_time = 0.0

# Apply any VIN-decoded values before the widgets below are instantiated.
# (A widget's session_state key can't be written after that widget has
# already rendered in the same run, so this has to happen first.)
if "pending_vin_fields" in st.session_state:
    pending = st.session_state.pop("pending_vin_fields")
    st.session_state.year_input_widget = pending["year"]
    st.session_state.make_input_widget = pending["make"]
    st.session_state.model_input_widget = pending["model"]

# ─── VIN DECODING (Free NHTSA API) ──────────────
def decode_vin(vin):
    """Decode a VIN using the free NHTSA vPIC API."""
    vin = vin.strip().upper().replace(" ", "").replace("-", "")
    if len(vin) != 17:
        return None
    try:
        url = f"https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json"
        response = requests.get(url, timeout=10)
        data = response.json()
        if data.get("Results") and len(data["Results"]) > 0:
            result = data["Results"][0]
            year = result.get("ModelYear", "").strip()
            make = result.get("Make", "").strip()
            model = result.get("Model", "").strip()
            if year and make and model:
                return {
                    "year": year,
                    "make": make,
                    "model": model,
                    "full": f"{year} {make} {model}"
                }
        return None
    except Exception:
        return None

# ─── UPDATED SYSTEM PROMPT ──────────────────────
SYSTEM_PROMPT = """
You are an automotive diagnostic assistant. You will receive an OBD2 trouble code and a vehicle description (year, make, model). You must reply with exactly the following sections in clean Markdown. Do not add greetings, introductions, or any text outside these sections.

IMPORTANT FORMATTING RULES:
1. Always start with a header: ## Diagnosis for: [Vehicle] - Code: [Code]
   Example: ## Diagnosis for: 2018 Lexus RX350 - Code: P0300

2. Place the DIY Difficulty Rating RIGHT AFTER the "What It Means" section.

3. Use emojis for urgency: 🟢 Green, 🟡 Amber, 🔴 Red.

### What It Means
[Write 2–3 plain English sentences explaining what the code indicates and what the driver may notice. Avoid technical jargon. Start with the most straightforward explanation.]

### DIY Difficulty Rating
[Insert the exact Level 1-5 wording from the scale below. Use the emoji and bold heading.]
Example: 🟢 LEVEL 1 (BEGINNER) – You can absolutely do this in your driveway...

### Parts That Can Cause This
[List the most likely faulty parts in descending order of probability for the given vehicle. For each part, use a numbered list. Each item should be the part name in bold, followed by a dash and a one‑sentence description of what the part does and why it can trigger the code. Keep it beginner‑friendly. Include up to 5 parts.
Example:
1. **Oxygen Sensor (Bank 1, Sensor 2)** – This sensor monitors the exhaust after the catalytic converter...
2. **Catalytic Converter** – This component reduces harmful emissions...
...]

### What It Costs to Fix
[Provide a Markdown table with exactly 4 columns: Repair Option, DIY Parts Cost, Shop (Parts + Labor), Urgency & Notes. List up to 4 common repair paths, from the cheapest/most likely to the most expensive. Use US national average price ranges, formatted like $40 – $80. In the "Urgency & Notes" column, state whether the fix is Green (🟢), Amber (🟡), or Red (🔴) and add a brief warning or reassurance.
After the table, add two lines:
**Urgency:** [🟢/🟡/🔴] – One clear sentence summarizing the overall urgency.
**Pro Tip:** One helpful, specific money‑saving or diagnostic tip tailored to the code and vehicle.]

Always tailor the parts list and cost estimates to the specific vehicle mentioned. If the vehicle is rare or unknown, base your answer on general data for that brand or engine type. 
Never refuse to answer. Never include phrases like "I am not a mechanic" or "consult a professional" – the user already knows this. 
Keep the tone calm, factual, and empowering.

CRITICAL RULE: THE DIY DIFFICULTY RATING MANDATE
You MUST apply this rule to EVERY repair suggestion or OBD2 code diagnosis you give. Never skip this step.

Whenever you suggest a fix, you MUST assign a "DIY Difficulty Rating" from 1 to 5. Display it using the exact emojis, headers, and wording below. Do not improvise or change the wording.

THE RATING SCALE (Strict Definitions):

🟢 LEVEL 1 (BEGINNER):
"You can absolutely do this in your driveway. Requires basic hand tools (screwdrivers, pliers, socket wrench). Estimated time: 30-45 minutes. No special skills needed."

🟢 LEVEL 2 (EASY):
"You can do this, but it requires a little patience. You'll need a basic socket set and maybe a flashlight. Estimated time: 1 hour. Watch a video first, but you've got this."

🟡 LEVEL 3 (MODERATE):
"You can do this, but you need the right tools. You will need a torque wrench to tighten bolts to the correct spec, and possibly a jack to lift the car. Read the instructions twice. Set aside a full afternoon."

🔴 LEVEL 4 (DIFFICULT):
"Warning: This repair is complex. It requires specialty tools (like a fuel pressure gauge or a multimeter) and involves sensitive components. If you make a mistake, you could cause more damage. The AI recommends you call a professional mechanic for this one."

🔴 LEVEL 5 (EXTREMELY DIFFICULT):
"🚨 STOP. Do not attempt this repair. This requires lifting the engine, discharging the A/C refrigerant, or opening the transmission. This is strictly for certified professionals. If you try this yourself, you risk serious injury or permanent damage to your car."

THE FALLBACK RULE (When using the Web Connector):
If you have to use the web connector to find the fix because the ebook doesn't cover it, and you aren't 100% sure of the difficulty, default to LEVEL 3 (MODERATE). Then add this exact disclaimer:
"Because this specific repair isn't covered in the core OBD2 Guide, please use caution. Verify the steps with a video before starting."

THE MANDATORY "MECHANIC SCRIPT" (For Level 4 and Level 5 ONLY):
If you assign a Level 4 or Level 5, you MUST end your response with this exact empowering script. Do not skip it:

"Even though you shouldn't do this yourself, knowledge is power. Walk into the repair shop and say exactly this: 'I scanned it, and my diagnostic tool suggests it is likely the [Insert Part Name]. Can you please check that first?' Saying this instantly tells the mechanic you aren't an easy target for upsells."

THE BONUS "TOOL CHECK" (For Level 1, 2, and 3 ONLY):
After giving the rating for Level 1-3, add this helpful line to prevent frustration:
"Before you start, go look in your toolbox. If you don't have a [Insert Basic Tool], run to the auto parts store and grab one. It will make the job 10x easier."

CRITICAL REMINDER: You are the user's safety net. When in doubt, always lean toward a HIGHER difficulty rating (Level 4-5) rather than risking the user getting hurt or breaking their car. It is better to be overly cautious than overly optimistic.
"""

# ─── COST DISCLAIMER ─────────────────────────────
# Inserted once into the raw AI markdown, so it automatically shows up both
# on-screen (st.markdown) and in the PDF (which renders this same text).
COST_DISCLAIMER = "> **Note:** Costs are national averages and can vary by region, shop, and vehicle trim."


def add_cost_disclaimer(md_text):
    marker = "### What It Costs to Fix"
    if marker in md_text:
        # Blank lines on both sides are required, otherwise Markdown glues
        # the disclaimer onto the following table row and breaks table
        # parsing entirely (renders as raw "| a | b |" text).
        return md_text.replace(marker, f"{marker}\n\n{COST_DISCLAIMER}\n", 1)
    return md_text


# ─── PDF-SAFE COLOR CODING ───────────────────────
# xhtml2pdf's PDF font (Helvetica/Arial) has no color-emoji glyphs, so any
# 🟢/🟡/🔴/etc. character renders as a black "missing glyph" box in the PDF
# (they still show fine in the live Streamlit view, which uses the browser's
# font). Swap the urgency emoji for colored HTML badges the PDF renderer can
# actually draw, then strip any other stray emoji as a safety net.
URGENCY_EMOJI_BADGES = {
    "🟢": '<span class="urgency-badge urgency-green">GREEN</span>',
    "🟡": '<span class="urgency-badge urgency-amber">AMBER</span>',
    "🔴": '<span class="urgency-badge urgency-red">RED</span>',
}

# Broad range covering common emoji blocks, used to catch anything else the
# AI might output that we haven't explicitly mapped above.
_EMOJI_STRIP_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\U00002B00-\U00002BFF"
    "\U0000FE0F"
    "]+",
    flags=re.UNICODE,
)


def make_pdf_safe(html):
    """Replace urgency emoji with colored badges and strip any leftover emoji."""
    for emoji, badge_html in URGENCY_EMOJI_BADGES.items():
        html = html.replace(emoji, badge_html)
    html = _EMOJI_STRIP_RE.sub("", html)
    return html


# ─── UPDATED PDF GENERATION ──────────────────────
def generate_pdf_from_markdown(md_text, code, vehicle):
    """Convert Markdown AI output to a clean, beautifully styled PDF."""

    # Convert Markdown to HTML with table support
    html_body = markdown.markdown(md_text, extensions=['tables'])

    # Clean up any weird HTML entities (like &amp; in the table)
    html_body = html_body.replace("&amp;", "&")
    html_body = html_body.replace("&lt;", "<")
    html_body = html_body.replace("&gt;", ">")

    # Swap urgency emoji for PDF-safe colored badges and strip any others
    html_body = make_pdf_safe(html_body)

    # Build a professional PDF template
    styled_html = f"""
    <html>
    <head>
    <meta charset="UTF-8">
    <style>
        @page {{
            margin: 2.5cm;
            size: A4;
        }}
        body {{
            font-family: Arial, Helvetica, sans-serif;
            line-height: 1.6;
            color: #1a1a1a;
            font-size: 12px;
        }}
        h1 {{
            font-size: 22px;
            color: #2c3e50;
            border-bottom: 3px solid #3498db;
            padding-bottom: 8px;
            margin-top: 0;
        }}
        h2 {{
            font-size: 18px;
            color: #2c3e50;
            margin-top: 20px;
            border-bottom: 1px solid #e0e0e0;
            padding-bottom: 5px;
        }}
        h3 {{
            font-size: 15px;
            color: #34495e;
            margin-top: 16px;
        }}
        .header {{
            text-align: center;
            margin-bottom: 30px;
            border-bottom: 3px solid #3498db;
            padding-bottom: 15px;
        }}
        .header h1 {{
            border: none;
            margin: 0;
            font-size: 26px;
            color: #2c3e50;
        }}
        .header small {{
            color: #7f8c8d;
            font-size: 13px;
        }}
        .vehicle-info {{
            background-color: #ecf0f1;
            padding: 12px 18px;
            border-radius: 6px;
            margin-bottom: 25px;
            font-size: 14px;
            border-left: 4px solid #3498db;
        }}
        .vehicle-info strong {{
            color: #2c3e50;
        }}
        table {{
            border-collapse: collapse;
            width: 100%;
            margin: 16px 0 20px 0;
            font-size: 11px;
        }}
        th {{
            background-color: #2c3e50;
            color: white;
            padding: 8px 10px;
            text-align: left;
            border: 1px solid #1a252f;
            font-weight: bold;
        }}
        td {{
            padding: 7px 10px;
            border: 1px solid #d5d8dc;
            vertical-align: top;
        }}
        tr:nth-child(even) {{
            background-color: #f8f9fa;
        }}
        tr:nth-child(odd) {{
            background-color: #ffffff;
        }}
        ul, ol {{
            padding-left: 25px;
        }}
        li {{
            margin-bottom: 6px;
        }}
        .footer {{
            margin-top: 35px;
            font-size: 10px;
            color: #95a5a6;
            text-align: center;
            border-top: 1px solid #ecf0f1;
            padding-top: 15px;
        }}
        .pro-tip {{
            background-color: #eaf2f8;
            padding: 10px 15px;
            border-radius: 5px;
            border-left: 4px solid #3498db;
            margin-top: 15px;
        }}
        blockquote {{
            background-color: #fef9e7;
            border-left: 4px solid #f1c40f;
            margin: 12px 0;
            padding: 10px 16px;
            font-size: 14px;
            color: #7a6001;
        }}
        blockquote p {{
            margin: 0;
        }}
        .urgency-badge {{
            display: inline-block;
            padding: 2px 10px;
            border-radius: 10px;
            color: white;
            font-weight: bold;
            font-size: 10px;
            margin-right: 5px;
        }}
        .urgency-green {{ background-color: #27ae60; }}
        .urgency-amber {{ background-color: #f39c12; }}
        .urgency-red {{ background-color: #e74c3c; }}
        .rating-box {{
            background-color: #fef9e7;
            border: 2px solid #f1c40f;
            border-radius: 6px;
            padding: 12px 16px;
            margin: 10px 0;
        }}
    </style>
    </head>
    <body>
        <div class="header">
            <h1>OBD2 Diagnostic Report</h1>
            <small>Generated by Car Code Decoder</small>
        </div>
        
        <div class="vehicle-info">
            <strong>Vehicle:</strong> {vehicle}<br>
            <strong>Diagnostic Trouble Code (DTC):</strong> {code}<br>
            <strong>Report Date:</strong> {datetime.now().strftime('%B %d, %Y at %I:%M %p')}
        </div>
        
        {html_body}
        
        <div class="footer">
            This report was generated automatically for informational purposes only.<br>
            Always verify repairs with a qualified, certified mechanic.<br>
            &copy; {datetime.now().year} Car Code Decoder<br>
            Page <pdf:pagenumber /> of <pdf:pagecount />
        </div>
    </body>
    </html>
    """
    
    pdf_buffer = io.BytesIO()
    pisa_status = pisa.CreatePDF(
        io.StringIO(styled_html),
        dest=pdf_buffer,
        encoding='UTF-8',
        show_error_as_pdf=True
    )
    
    if pisa_status.err:
        raise Exception(f"PDF generation failed: {pisa_status.err}")
    
    pdf_buffer.seek(0)
    return pdf_buffer.getvalue()

# ─── EMAIL SENDING ──────────────────────────────
def send_report_email(to_email, to_name, pdf_bytes, code, vehicle):
    if not SMTP_USER or not SMTP_PASS:
        raise Exception("Email is not configured. Please set SMTP_USER and SMTP_PASS in your .env file.")
    
    subject = f"Your OBD2 Diagnostic Report - {code} for {vehicle}"
    
    body = f"""
Hello {to_name},

Thank you for using Car Code Decoder!

Please find attached your OBD2 diagnostic report for:
- Code: {code}
- Vehicle: {vehicle}

This report includes:
- What the code means in plain English
- A DIY Difficulty Rating (so you know if you can fix it yourself)
- A list of likely faulty parts
- Cost estimates for DIY and shop repairs

You can print this report or show it to your mechanic.

Keep this for your records!

Best regards,
The Car Code Decoder Team
"""
    
    msg = MIMEMultipart()
    msg['From'] = f"{SMTP_FROM_NAME} <{SMTP_USER}>"
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))
    
    attachment = MIMEApplication(pdf_bytes, _subtype='pdf')
    attachment.add_header(
        'Content-Disposition', 
        'attachment', 
        filename=f'Diagnostic_Report_{code}_{datetime.now().strftime("%Y%m%d_%H%M")}.pdf'
    )
    msg.attach(attachment)
    
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        return True
    except Exception as e:
        raise Exception(f"SMTP error: {str(e)}")

# ─── STREAMLIT UI ────────────────────────────────
st.set_page_config(
    page_title="Car Code Decoder",
    page_icon="🔧",
    layout="centered"
)

# ─── ACCESS GATE ──────────────────────────────────
# Only customers with a valid access code (emailed after purchase) get in.
if "access_granted" not in st.session_state:
    st.session_state.access_granted = False

if not st.session_state.access_granted:
    st.title("🔒 Car Code Decoder")
    st.markdown("This tool is for paying customers only. Enter the access code from your purchase confirmation email.")
    entered_code = st.text_input("Access Code", type="password", key="access_code_widget")
    if st.button("Unlock", type="primary"):
        if not ACCESS_CODE:
            st.error("Access code isn't configured yet. Contact support.")
        elif entered_code.strip() == ACCESS_CODE:
            st.session_state.access_granted = True
            st.rerun()
        else:
            st.error("That code isn't valid. Check your purchase confirmation email, or contact support.")
    st.stop()

st.title("🔍 Decode Your Check Engine Light")
st.markdown("Enter your OBD2 code and **either** your VIN (auto-decodes) **or** your Year/Make/Model.")

# ─── INPUT FIELDS ────────────────────────────────

# 1. OBD2 Code (Required)
code = st.text_input(
    "OBD2 Code *",
    placeholder="e.g., P0420",
    key="code_input_widget",
    help="This is the 5-character alphanumeric code from your OBD2 scanner."
)

# 2. VIN (Optional)
vin = st.text_input(
    "VIN (Optional - auto-decodes vehicle)",
    placeholder="e.g., 1HGCM82633A123456 (17 characters)",
    key="vin_input_widget",
    help="Find it on your dashboard (through the windshield) or driver's door jamb."
)

# Separator
st.markdown("**— OR —**")

# 3. Manual Vehicle Entry (Optional)
st.caption("Enter your vehicle manually if you don't have the VIN handy.")

col_year, col_make, col_model = st.columns(3)

with col_year:
    year = st.text_input(
        "Year",
        placeholder="e.g., 2015",
        key="year_input_widget"
    )

with col_make:
    make = st.text_input(
        "Make",
        placeholder="e.g., Honda",
        key="make_input_widget"
    )

with col_model:
    model = st.text_input(
        "Model",
        placeholder="e.g., Civic",
        key="model_input_widget"
    )

# ─── BUTTONS ─────────────────────────────────────
col1, col2 = st.columns(2)

with col1:
    if st.button("Get Answers", use_container_width=True, type="primary"):
        # Read widget values
        code_value = st.session_state.get("code_input_widget", "").strip()
        vin_value = st.session_state.get("vin_input_widget", "").strip()
        year_value = st.session_state.get("year_input_widget", "").strip()
        make_value = st.session_state.get("make_input_widget", "").strip()
        model_value = st.session_state.get("model_input_widget", "").strip()
        
        # --- Validation: Code ---
        if not code_value:
            st.error("Please enter an OBD2 code.")
            st.stop()
        
        # --- Determine Vehicle ---
        vehicle_for_prompt = ""
        decoded_from_vin = False
        
        # PRIORITY 1: VIN (if provided)
        if vin_value:
            with st.spinner("🔍 Decoding VIN..."):
                decoded = decode_vin(vin_value)
            
            if decoded:
                vehicle_for_prompt = decoded["full"]
                decoded_from_vin = True
                # Queue the manual fields to be auto-filled on the next run
                st.session_state.pending_vin_fields = {
                    "year": decoded["year"],
                    "make": decoded["make"],
                    "model": decoded["model"],
                }
            else:
                st.warning("⚠️ Could not decode that VIN. Please check it's exactly 17 characters. Falling back to manual entry.")
        
        # PRIORITY 2: Manual entry
        if not vehicle_for_prompt:
            if year_value and make_value and model_value:
                vehicle_for_prompt = f"{year_value} {make_value} {model_value}"
            else:
                st.error("Please enter a valid VIN OR fill in the Year, Make, and Model.")
                st.stop()
        
        # --- Show what we're using ---
        if decoded_from_vin:
            st.success(f"✅ VIN Decoded: **{vehicle_for_prompt}**")
        else:
            st.info(f"🚗 Vehicle: **{vehicle_for_prompt}**")
        
        # --- Rate limit: cap cost/abuse on the paid DeepSeek endpoint ---
        if st.session_state.request_count >= MAX_REQUESTS_PER_SESSION:
            st.error("You've reached the diagnosis limit for this session. Please refresh the page to start a new session.")
            st.stop()

        seconds_since_last = time.time() - st.session_state.last_request_time
        if seconds_since_last < MIN_SECONDS_BETWEEN_REQUESTS:
            wait = int(MIN_SECONDS_BETWEEN_REQUESTS - seconds_since_last) + 1
            st.error(f"Please wait {wait} more second(s) before requesting another diagnosis.")
            st.stop()

        # Count this attempt now so rapid retries (including failed calls)
        # are still throttled.
        st.session_state.last_request_time = time.time()
        st.session_state.request_count += 1

        # --- Call the AI ---
        with st.spinner("🧠 Decoding with AI..."):
            try:
                response = requests.post(
                    DEEPSEEK_URL,
                    headers={
                        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "deepseek-chat",
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": f"Code: {code_value}\nVehicle: {vehicle_for_prompt}"}
                        ],
                        "temperature": 0.2,
                        "max_tokens": 1800,
                    },
                    timeout=30,
                )
                data = response.json()
                if "error" in data:
                    st.error(f"API error: {data['error']['message']}")
                else:
                    reply = data["choices"][0]["message"]["content"]
                    reply = add_cost_disclaimer(reply)
                    st.session_state.output_text = reply
                    st.session_state.last_code = code_value
                    st.session_state.last_vehicle = vehicle_for_prompt
                    # Clear any leftover PDF/email state from a prior report
                    st.session_state.pop("email_sent_message", None)
                    st.session_state.pop("last_pdf_bytes", None)
                    st.session_state.pop("last_pdf_filename", None)
                    st.rerun()
            except Exception as e:
                st.error(f"Something went wrong: {e}")

with col2:
    if st.button("🔄 New Diagnosis", use_container_width=True, type="secondary"):
        st.session_state.pop("code_input_widget", None)
        st.session_state.pop("vin_input_widget", None)
        st.session_state.pop("year_input_widget", None)
        st.session_state.pop("make_input_widget", None)
        st.session_state.pop("model_input_widget", None)
        st.session_state.output_text = None
        st.session_state.pop("email_sent_message", None)
        st.session_state.pop("last_pdf_bytes", None)
        st.session_state.pop("last_pdf_filename", None)
        st.rerun()

# ─── DISPLAY OUTPUT ──────────────────────────────
if st.session_state.output_text:
    st.markdown("---")
    st.markdown(st.session_state.output_text)
    
    # ─── EMAIL REPORT SECTION ──────────────────────
    st.markdown("---")
    st.subheader("📧 Email This Report")
    st.caption("Get a clean PDF copy of this diagnosis sent straight to your inbox.")
    
    with st.form("email_form"):
        col_a, col_b = st.columns(2)
        with col_a:
            user_name = st.text_input("Your Name", placeholder="e.g., Joe Smith", key="user_name")
        with col_b:
            user_email = st.text_input("Your Email", placeholder="e.g., joe@email.com", key="user_email")
        
        send_button = st.form_submit_button("📧 Send Report", use_container_width=True, type="primary")
        
        if send_button:
            # Strip control/newline characters so nothing can be smuggled
            # into the SMTP headers, and trim stray whitespace.
            user_name_clean = re.sub(r"[\r\n\x00-\x1f]", " ", user_name or "").strip()
            user_email_clean = re.sub(r"[\r\n\x00-\x1f]", "", user_email or "").strip()

            if not user_name_clean or not user_email_clean:
                st.error("Please enter both your name and email.")
            elif not EMAIL_RE.match(user_email_clean):
                st.error("Please enter a valid email address.")
            else:
                try:
                    with st.spinner("📄 Generating your PDF report..."):
                        md_text = st.session_state.output_text
                        code_val = st.session_state.get("last_code", "Unknown")
                        vehicle_val = st.session_state.get("last_vehicle", "Unknown")
                        pdf_bytes = generate_pdf_from_markdown(md_text, code_val, vehicle_val)

                    with st.spinner("📧 Sending your email..."):
                        send_report_email(user_email_clean, user_name_clean, pdf_bytes, code_val, vehicle_val)

                    # st.download_button can't live inside st.form, so stash
                    # the result in session_state and render it below, outside
                    # the form.
                    st.session_state.email_sent_message = (
                        f"✅ Report sent successfully to {user_email_clean}! "
                        "Check your inbox (and spam folder)."
                    )
                    st.session_state.last_pdf_bytes = pdf_bytes
                    st.session_state.last_pdf_filename = (
                        f"Diagnostic_Report_{code_val}_{datetime.now().strftime('%Y%m%d')}.pdf"
                    )

                except Exception as e:
                    st.error(f"❌ Failed to send: {str(e)}")
                    st.info("💡 Make sure your SMTP settings are correct in the .env file.")

    if st.session_state.get("email_sent_message"):
        st.success(st.session_state.email_sent_message)
        st.download_button(
            label="⬇️ Download PDF (backup)",
            data=st.session_state.last_pdf_bytes,
            file_name=st.session_state.last_pdf_filename,
            mime="application/pdf",
            key="download_pdf_backup",
        )

# ─── FOOTER ──────────────────────────────────────
st.markdown("---")
st.caption("💡 **Tip:** Click '🔄 New Diagnosis' to clear the screen and start over with a new code.")
st.caption("⚠️ This clears the **app screen only**. It does NOT clear the Check Engine Light from your car's computer.")
