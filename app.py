import base64, io, json, os, secrets, subprocess, threading, uuid
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse\nimport qrcode
from groq import Groq

DATA_DIR = Path(os.getenv("DATA_DIR", "/tmp/nuel-ai-data"))
DB = DATA_DIR / "inbox.json"
TOKEN = os.getenv("BRIDGE_WEBHOOK_TOKEN", "")
PHONE = "".join(c for c in os.getenv("WHATSAPP_PHONE_NUMBER", "") if c.isdigit())
KEY = os.getenv("GROQ_API_KEY", "")
DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME", "nuel")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
security = HTTPBasic()
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
BLOCK_NAMES = [x.strip().lower() for x in os.getenv("FAMILY_BLOCKLIST_NAMES", "Mom,Dad,Auntie").split(",") if x.strip()]
BLOCK_NUMBERS = ["".join(c for c in x if c.isdigit()) for x in os.getenv("FAMILY_BLOCKLIST_NUMBERS", "").split(",") if x.strip()]
lock = threading.RLock()
bridge = None
app = FastAPI(title="NUEL-AI", version="1.0.0")

def stamp(): return datetime.now(timezone.utc).isoformat()
def load():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        d = json.loads(DB.read_text(encoding="utf-8"))
        d.setdefault("messages", []); d.setdefault("drafts", []); d.setdefault("summary", "")
        return d
    except (OSError, ValueError): return {"messages": [], "drafts": [], "summary": ""}
def save(d):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    t = DB.with_suffix(".tmp"); t.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8"); t.replace(DB)
def is_family(name, number):
    n = (name or "").lower(); digits = "".join(c for c in (number or "") if c.isdigit())
    return any(x in n for x in BLOCK_NAMES) or any(digits.endswith(x) or x.endswith(digits) for x in BLOCK_NUMBERS if x)
def require_dashboard_auth(credentials: HTTPBasicCredentials = Depends(security)):
    if not DASHBOARD_PASSWORD:
        raise HTTPException(503, "Dashboard access is not configured. Set DASHBOARD_PASSWORD in Render.")
    valid_user = secrets.compare_digest(credentials.username, DASHBOARD_USERNAME)
    valid_password = secrets.compare_digest(credentials.password, DASHBOARD_PASSWORD)
    if not (valid_user and valid_password):
        raise HTTPException(401, "Invalid dashboard credentials.", headers={"WWW-Authenticate": "Basic"})
    return credentials.username

def ai():
    if not KEY: raise HTTPException(503, "Add GROQ_API_KEY in Render environment variables.")
    return Groq(api_key=KEY)

@app.get("/", response_class=HTMLResponse)
def home(_user: str = Depends(require_dashboard_auth)): return HTMLResponse(PAGE)
@app.get("/api/health")
def health(): return {"ok": True, "app": "NUEL-AI", "time": stamp()}
@app.post("/api/bridge/qr")
def receive_bridge_qr(payload: dict, x_bridge_token: str | None = Header(default=None)):
    global latest_qr
    if not TOKEN or x_bridge_token != TOKEN: raise HTTPException(401, "Webhook authentication failed.")
    value = str(payload.get("qr") or "")
    if not value or len(value) > 12000: raise HTTPException(400, "Invalid QR payload.")
    latest_qr = value
    return {"ok": True}
@app.delete("/api/bridge/qr")
def clear_bridge_qr(x_bridge_token: str | None = Header(default=None)):
    global latest_qr
    if not TOKEN or x_bridge_token != TOKEN: raise HTTPException(401, "Webhook authentication failed.")
    latest_qr = None
    return {"ok": True}
@app.get("/api/whatsapp-qr")
def whatsapp_qr(_user: str = Depends(require_dashboard_auth)):
    if not latest_qr: return {"available": False}
    image = qrcode.make(latest_qr)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return {"available": True, "image": "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")}
@app.get("/api/bridge-status")
def bridge_status(_user: str = Depends(require_dashboard_auth)):
    return {"configured": bool(PHONE), "process_running": bridge is not None and bridge.poll() is None}
@app.get("/api/messages")
def messages(_user: str = Depends(require_dashboard_auth)):
    with lock:
        d = load()
        return {"messages": list(reversed(d["messages"][-200:])), "drafts": list(reversed(d["drafts"][-200:]))}
@app.post("/api/incoming")
def incoming(payload: dict, x_bridge_token: str | None = Header(default=None)):
    if not TOKEN or x_bridge_token != TOKEN: raise HTTPException(401, "Webhook authentication failed.")
    sender = str(payload.get("sender") or "Unknown")[:120]; number = str(payload.get("number") or "")[:40]
    body = str(payload.get("text") or "").strip()[:10000]; key = str(payload.get("message_id") or "")
    if not body: raise HTTPException(400, "Message text required.")
    with lock:
        d = load()
        if key and any(m.get("message_id") == key for m in d["messages"]): return {"ok": True, "duplicate": True}
        d["messages"].append({"id": uuid.uuid4().hex, "message_id": key, "sender": sender, "number": number, "text": body, "received_at": stamp(), "family": is_family(sender, number)})
        d["messages"] = d["messages"][-500:]; save(d)
    return {"ok": True}
@app.post("/api/messages/{mid}/draft")
def make_draft(mid: str, _user: str = Depends(require_dashboard_auth)):
    with lock:
        d = load(); msg = next((m for m in d["messages"] if m["id"] == mid), None)
    if not msg: raise HTTPException(404, "Message not found.")
    if msg["family"]: raise HTTPException(403, "Family-blocklisted messages are excluded from AI drafts.")
    r = ai().chat.completions.create(model=MODEL, temperature=0.4, messages=[
        {"role":"system","content":"Write a concise, thoughtful WhatsApp reply draft. Do not invent facts. Return only the draft. Never claim it was sent."},
        {"role":"user","content":"Sender: "+msg["sender"]+"\nIncoming message:\n"+msg["text"]}])
    draft = {"id":uuid.uuid4().hex,"message_id":mid,"sender":msg["sender"],"text":(r.choices[0].message.content or "").strip(),"status":"pending_review","created_at":stamp()}
    with lock:
        d=load(); d["drafts"].append(draft); d["drafts"]=d["drafts"][-500:]; save(d)
    return {"draft":draft}
@app.patch("/api/drafts/{did}")
def update_draft(did: str, payload: dict, _user: str = Depends(require_dashboard_auth)):
    with lock:
        d=load(); item=next((x for x in d["drafts"] if x["id"]==did),None)
        if not item: raise HTTPException(404,"Draft not found.")
        if "text" in payload: item["text"]=str(payload["text"])[:10000]
        if payload.get("status") in ("pending_review","approved","rejected"): item["status"]=payload["status"]
        save(d); return {"draft":item,"note":"Approval is recorded only; no WhatsApp message is sent."}
@app.post("/api/summary")
def summary(_user: str = Depends(require_dashboard_auth)):
    with lock:
        d=load(); msgs=[m for m in d["messages"][-40:] if not m["family"]]
    if not msgs: return {"summary":"No non-family messages to summarize yet."}
    content="\n".join("- "+m["sender"]+": "+m["text"] for m in msgs)
    r=ai().chat.completions.create(model=MODEL,temperature=0.2,messages=[
        {"role":"system","content":"Summarize this inbox in concise bullets. Highlight questions needing attention. Do not invent facts."},
        {"role":"user","content":content}])
    result=(r.choices[0].message.content or "").strip()
    with lock: d=load(); d["summary"]=result; save(d)
    return {"summary":result}
@app.on_event("startup")
def start_bridge():
    global bridge
    if not PHONE: print("[NUEL-AI] Set WHATSAPP_PHONE_NUMBER to start WhatsApp bridge."); return
    Path(os.getenv("WHATSAPP_AUTH_DIR","/tmp/nuel-ai-auth")).mkdir(parents=True,exist_ok=True)
    bridge=subprocess.Popen(["node","bridge/index.js"],env=os.environ.copy())
@app.on_event("shutdown")
def stop_bridge():
    if bridge and bridge.poll() is None:
        bridge.terminate()
        try: bridge.wait(timeout=8)
        except subprocess.TimeoutExpired: bridge.kill()

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NUEL-AI</title><style>
:root{color-scheme:dark;--bg:#0c1118;--panel:#121b26;--line:#263646;--text:#edf3f8;--muted:#9aabba;--blue:#78b9ff;--green:#72d6ad}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 Inter,system-ui,sans-serif}header{padding:24px 5vw;border-bottom:1px solid var(--line);display:flex;justify-content:space-between}.brand{font-weight:800;letter-spacing:.12em}.brand span{color:var(--blue)}.sub{color:var(--muted);font-size:12px}main{max-width:1180px;margin:auto;padding:32px 4vw 64px}.top,.grid{display:grid;grid-template-columns:1.3fr .7fr;gap:18px;margin-bottom:18px}.grid{grid-template-columns:1fr 1fr}.panel{background:var(--panel);border:1px solid var(--line);padding:22px}h1{font-size:clamp(28px,4vw,42px);line-height:1.08;letter-spacing:-.04em;margin:0 0 10px}h2{font-size:17px;margin:0 0 16px}p{color:var(--muted)}.status{color:var(--muted);font-size:13px;margin-top:16px}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#d9b66d}.dot.ok{background:var(--green)}button{border:1px solid #36516b;background:#1b344b;color:#eaf4ff;padding:10px 14px;cursor:pointer;margin:8px 5px 0 0}button.secondary{background:transparent;border-color:var(--line);color:var(--muted)}.item{padding:16px 0;border-top:1px solid var(--line)}.meta{display:flex;justify-content:space-between;gap:10px;color:var(--muted);font-size:12px;margin-bottom:7px}.sender{font-weight:700;color:var(--text)}.message{white-space:pre-wrap;overflow-wrap:anywhere}.tag{font-size:10px;text-transform:uppercase;color:var(--green);border:1px solid #315b4e;padding:2px 6px}.family{color:#e7c98d;border-color:#65532e}textarea{width:100%;min-height:90px;background:#0d151f;border:1px solid var(--line);color:var(--text);padding:12px;font:inherit}.empty{color:var(--muted);padding:18px 0}.summary{white-space:pre-wrap}@media(max-width:760px){.top,.grid{grid-template-columns:1fr}header{flex-direction:column;gap:8px}}
</style></head><body><header><div><div class="brand">NUEL<span>·</span>AI</div><div class="sub">WHATSAPP MESSAGE DESK</div></div><div class="sub">AI-assisted. Human-approved.</div></header><main>
<section class="top"><div class="panel"><h1>Your messages,<br>with more clarity.</h1><p>Review incoming messages, understand the important ones, and prepare replies without giving up control.</p><div class="status"><span id="dot" class="dot"></span> <span id="bridge">Checking WhatsApp…</span></div><button onclick="summarize()">Summarize inbox</button><button class="secondary" onclick="refresh()">Refresh</button></div><div class="panel"><h2>Inbox brief</h2><div id="brief" class="summary">No summary generated yet.</div></div></section>
<section id="qrPanel" class="panel" style="display:none;margin-bottom:18px;text-align:center"><h2>Link WhatsApp with QR code</h2><p>On your phone, open WhatsApp → Linked devices → Link a device, then scan this code using your phone camera inside WhatsApp.</p><img id="qrImage" alt="Temporary WhatsApp linking QR code" style="width:260px;max-width:100%;background:white;padding:10px"><p class="sub">This QR is private to your authenticated dashboard and disappears after connection.</p></section><section class="grid"><div class="panel"><h2>Incoming messages</h2><div id="messages" class="empty">Loading…</div></div><div class="panel"><h2>Reply drafts</h2><div id="drafts" class="empty">Loading…</div></div></section><p style="font-size:12px">Messages are sent to Groq only when you request AI features. Family-blocklisted messages are excluded. Draft approval never sends a WhatsApp message.</p></main>
<script>
async function api(u,o){let r=await fetch(u,{headers:{"Content-Type":"application/json"},...o});let d=await r.json();if(!r.ok)throw Error(d.detail||"Request failed");return d}
function esc(s){return String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
async function refresh(){try{let d=await api("/api/messages");document.getElementById("messages").innerHTML=d.messages.length?d.messages.map(x=>'<article class="item"><div class="meta"><b class="sender">'+esc(x.sender)+'</b><span>'+new Date(x.received_at).toLocaleString()+'</span></div><div class="message">'+esc(x.text)+'</div><span class="tag '+(x.family?"family":"")+'">'+(x.family?"Family · excluded":"Inbox")+'</span>'+(x.family?"":'<button onclick="draftMsg(\\''+x.id+'\\')">Generate reply draft</button>')+'</article>').join(""):'<div class="empty">Inbox is empty. Connect WhatsApp to receive messages.</div>';
document.getElementById("drafts").innerHTML=d.drafts.length?d.drafts.map(x=>'<article class="item"><div class="meta"><b>'+esc(x.sender)+'</b><span class="tag">'+esc(x.status)+'</span></div><textarea id="d-'+x.id+'">'+esc(x.text)+'</textarea><button onclick="saveDraft(\\''+x.id+'\\',\\'approved\\')">Mark approved</button><button class="secondary" onclick="saveDraft(\\''+x.id+'\\',\\'pending_review\\')">Save edits</button><button class="secondary" onclick="saveDraft(\\''+x.id+'\\',\\'rejected\\')">Reject</button></article>').join(""):'<div class="empty">No reply drafts yet.</div>'}catch(e){document.getElementById("messages").textContent=e.message}}
async function draftMsg(id){try{await api("/api/messages/"+id+"/draft",{method:"POST"});await refresh()}catch(e){alert(e.message)}}
async function saveDraft(id,status){try{await api("/api/drafts/"+id,{method:"PATCH",body:JSON.stringify({text:document.getElementById("d-"+id).value,status})});await refresh()}catch(e){alert(e.message)}}
async function summarize(){let b=document.getElementById("brief");b.textContent="Preparing…";try{b.textContent=(await api("/api/summary",{method:"POST"})).summary}catch(e){b.textContent=e.message}}
async function status(){try{let d=await api("/api/bridge-status");document.getElementById("dot").className="dot "+(d.process_running?"ok":"");document.getElementById("bridge").textContent=d.process_running?"WhatsApp bridge process running":d.configured?"Bridge stopped — check Render logs":"WhatsApp not configured — add WHATSAPP_PHONE_NUMBER in Render"}catch(e){}}
refresh();status();setInterval(()=>{refresh();status()},15000);
</script></body></html>"""
