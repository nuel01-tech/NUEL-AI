const fs = require("fs");
const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } = require("@whiskeysockets/baileys");
const pino = require("pino");
const PHONE = (process.env.WHATSAPP_PHONE_NUMBER || "").replace(/\D/g, "");
const AUTH = process.env.WHATSAPP_AUTH_DIR || "/tmp/nuel-ai-auth";
const WEBHOOK = process.env.WEBHOOK_URL || "http://127.0.0.1:10000/api/incoming";
const TOKEN = process.env.BRIDGE_WEBHOOK_TOKEN || "";
async function start() {
  if (!PHONE || !TOKEN) { console.error("[NUEL-AI] Set WHATSAPP_PHONE_NUMBER and BRIDGE_WEBHOOK_TOKEN."); process.exit(1); }
  fs.mkdirSync(AUTH, { recursive: true });
  const { state, saveCreds } = await useMultiFileAuthState(AUTH);
  const { version } = await fetchLatestBaileysVersion();
  const sock = makeWASocket({ version, auth: state, logger: pino({level:"warn"}), printQRInTerminal:false, browser:["NUEL-AI","Chrome","1.0.0"], markOnlineOnConnect:false, syncFullHistory:false });
  sock.ev.on("creds.update", saveCreds);
  sock.ev.on("connection.update", async u => {
    if (u.connection === "connecting") console.log("[WhatsApp] Connecting…");
    if (u.qr && !state.creds.registered) {
      try {
        const response = await fetch("http://127.0.0.1:10000/api/bridge/qr", {
          method: "POST",
          headers: { "content-type": "application/json", "x-bridge-token": TOKEN },
          body: JSON.stringify({ qr: u.qr })
        });
        if (!response.ok) console.error("[WhatsApp] Could not publish QR to dashboard:", response.status);
      } catch (e) {
        console.error("[WhatsApp] QR dashboard delivery failed:", e.message);
      }
    }
    if (u.connection === "open") {
      try {
        await fetch("http://127.0.0.1:10000/api/bridge/qr", {
          method: "DELETE",
          headers: { "x-bridge-token": TOKEN }
        });
      } catch {}
      console.log("[WhatsApp] Connected.");
    }
    if (u.connection === "close") {
      const status = u.lastDisconnect?.error?.output?.statusCode;
      const retry = status !== DisconnectReason.loggedOut;
      console.error("[WhatsApp] Closed. Reconnect:", retry, "status:", status);
      if (retry) setTimeout(() => start().catch(e => console.error(e)), 5000);
      else console.error("[WhatsApp] Logged out; re-pair this device.");
    }
  });
  sock.ev.on("messages.upsert", async ({messages,type}) => {
    if (type !== "notify") return;
    for (const m of messages || []) {
      if (!m.message || m.key.fromMe || m.key.remoteJid?.endsWith("@g.us") || m.key.remoteJid === "status@broadcast") continue;
      const number = (m.key.remoteJid || "").split("@")[0];
      const text = m.message.conversation || m.message.extendedTextMessage?.text || m.message.imageMessage?.caption || m.message.videoMessage?.caption || "";
      if (!text.trim()) continue;
      try {
        const r = await fetch(WEBHOOK,{method:"POST",headers:{"content-type":"application/json","x-bridge-token":TOKEN},body:JSON.stringify({sender:m.pushName||number,number,text,message_id:m.key.id})});
        if (!r.ok) console.error("[Webhook] HTTP",r.status,await r.text());
      } catch(e) { console.error("[Webhook] Delivery failed:",e.message); }
    }
  });
}
start().catch(e=>{console.error("[NUEL-AI] Bridge startup failed:",e);process.exit(1);});
