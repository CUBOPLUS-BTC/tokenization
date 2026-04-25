const pptxgen = require("pptxgenjs");

let pres = new pptxgen();
pres.layout = 'LAYOUT_16x9';
pres.author = 'Ricardo Fuentes - Blockchain Developer';
pres.title = 'PLAN B2B FLOW Pitch Deck';

// Theme colors
const BG_COLOR = "0F0F14";
const ACCENT_ORANGE = "F7931A";
const TEXT_LIGHT = "FFFFFF";
const TEXT_MUTED = "A0A0B0";
const ACCENT_CYAN = "00D4FF";

pres.defineSlideMaster({
  title: 'MASTER_SLIDE',
  background: { color: BG_COLOR },
  objects: [
    { rect: { x: 0, y: 5.4, w: 10, h: 0.225, fill: { color: "1A1A24" } } },
    { text: { text: "CUBO+ Digital Asset Tokenization Platform", options: { x: 0.5, y: 5.42, w: 4, h: 0.2, fontSize: 10, color: TEXT_MUTED, fontFace: "Arial" } } },
    { text: { text: "CONFIDENTIAL", options: { x: 8, y: 5.42, w: 1.5, h: 0.2, fontSize: 10, color: ACCENT_ORANGE, fontFace: "Consolas", align: "right" } } }
  ]
});

function addSlide(title) {
  let slide = pres.addSlide({ masterName: "MASTER_SLIDE" });
  if (title) {
    slide.addText(title, { x: 0.5, y: 0.4, w: 9, h: 0.6, fontSize: 32, fontFace: "Arial", color: TEXT_LIGHT, bold: true });
    slide.addShape(pres.shapes.RECTANGLE, { x: 0.5, y: 1.1, w: 1.5, h: 0.05, fill: { color: ACCENT_ORANGE } });
  }
  return slide;
}

// 0. Title Slide
let s0 = pres.addSlide({ masterName: "MASTER_SLIDE" });
s0.addText("PLAN B2B FLOW", { x: 1, y: 1.8, w: 8, h: 1, fontSize: 54, fontFace: "Arial", color: TEXT_LIGHT, bold: true });
s0.addText("A Bitcoin-Native Ecosystem for Digital Asset Tokenization", { x: 1, y: 2.8, w: 8, h: 0.5, fontSize: 20, fontFace: "Consolas", color: ACCENT_CYAN });
s0.addText("Ricardo Fuentes - Blockchain Developer", { x: 1, y: 4.5, w: 8, h: 0.5, fontSize: 14, fontFace: "Arial", color: TEXT_MUTED });
s0.addShape(pres.shapes.RECTANGLE, { x: 1, y: 1.6, w: 2, h: 0.08, fill: { color: ACCENT_ORANGE } });

// 1. The Problem
let s1 = addSlide("The $16 Trillion Problem");
s1.addText("Digital Assets are locked behind walls:", { x: 0.5, y: 1.5, w: 9, h: 0.5, fontSize: 18, color: TEXT_LIGHT, fontFace: "Arial" });
s1.addText([
  { text: "High Entry Barriers", options: { bold: true, color: ACCENT_ORANGE } },
  { text: ": Minimum investments of $50K-$500K+", options: { breakLine: true, color: TEXT_LIGHT } },
  { text: "Expensive Intermediaries", options: { bold: true, color: ACCENT_ORANGE } },
  { text: ": 3-8% in transaction fees", options: { breakLine: true, color: TEXT_LIGHT } },
  { text: "Slow Settlement", options: { bold: true, color: ACCENT_ORANGE } },
  { text: ": 30-90 days for finality", options: { breakLine: true, color: TEXT_LIGHT } },
  { text: "Zero Transparency", options: { bold: true, color: ACCENT_ORANGE } },
  { text: ": Opaque treasury allocation", options: { color: TEXT_LIGHT } }
], { x: 0.5, y: 2.2, w: 6, h: 2.5, fontSize: 16, fontFace: "Arial", bullet: { type: "bullet" }, paraSpaceAfter: 10 });

s1.addShape(pres.shapes.RECTANGLE, { x: 7, y: 2.2, w: 2.5, h: 2.5, fill: { color: "1A1A24" }, line: { color: "333344", width: 1 } });
s1.addText("$16T", { x: 7, y: 2.6, w: 2.5, h: 1, fontSize: 48, color: ACCENT_CYAN, bold: true, align: "center", margin: 0 });
s1.addText("Market Cap Locked", { x: 7, y: 3.5, w: 2.5, h: 0.5, fontSize: 14, color: TEXT_MUTED, align: "center", fontFace: "Consolas", margin: 0 });

// 2. The Wrong Foundation
let s2 = addSlide("Why Ethereum Isn't the Answer");
s2.addTable([
  [ { text: "Pain Point", options: { bold: true, color: TEXT_LIGHT } }, { text: "Ethereum (EVM)", options: { bold: true, color: "FF4444" } }, { text: "Liquid Network", options: { bold: true, color: ACCENT_CYAN } } ],
  [ "Transaction Fees", "$5-$200 per tx (kills micro-investing)", "~0.1 sat/vbyte" ],
  [ "Smart Contract Risk", "Turing-complete (High attack surface)", "UTXO-based (Minimal surface)" ],
  [ "Settlement Finality", "Minutes to hours", "1-minute blocks" ],
  [ "Privacy", "Public ledger (amounts exposed)", "Confidential Transactions" ]
], { x: 0.5, y: 1.8, w: 9, fill: { color: "1A1A24" }, border: { pt: 1, color: "333344" }, rowH: [0.6, 0.6, 0.6, 0.6, 0.6], color: TEXT_LIGHT, fontSize: 14, fontFace: "Arial" });

// 3. Why Now
let s3 = addSlide("The \"Why Now\"");
s3.addText("Three Converging Forces in 2026:", { x: 0.5, y: 1.5, w: 9, h: 0.5, fontSize: 18, color: TEXT_LIGHT, fontFace: "Arial" });
const cW = 2.8;
s3.addShape(pres.shapes.RECTANGLE, { x: 0.5, y: 2.2, w: cW, h: 2.5, fill: { color: "1A1A24" }, line: { color: ACCENT_ORANGE, width: 1 } });
s3.addText("Bitcoin Institutional", { x: 0.5, y: 2.4, w: cW, h: 0.5, fontSize: 16, color: TEXT_LIGHT, bold: true, align: "center" });
s3.addText("Bitcoin is infrastructure. Sovereign adoption and ETFs make it the trusted foundation.", { x: 0.7, y: 3.0, w: cW-0.4, h: 1.5, fontSize: 14, color: TEXT_MUTED, align: "center" });

s3.addShape(pres.shapes.RECTANGLE, { x: 3.6, y: 2.2, w: cW, h: 2.5, fill: { color: "1A1A24" }, line: { color: ACCENT_CYAN, width: 1 } });
s3.addText("Liquid Network Maturity", { x: 3.6, y: 2.4, w: cW, h: 0.5, fontSize: 16, color: TEXT_LIGHT, bold: true, align: "center" });
s3.addText("Production-ready tooling for confidential transactions and native asset issuance.", { x: 3.8, y: 3.0, w: cW-0.4, h: 1.5, fontSize: 14, color: TEXT_MUTED, align: "center" });

s3.addShape(pres.shapes.RECTANGLE, { x: 6.7, y: 2.2, w: cW, h: 2.5, fill: { color: "1A1A24" }, line: { color: "F9E795", width: 1 } });
s3.addText("LatAm Capital Gap", { x: 6.7, y: 2.4, w: cW, h: 0.5, fontSize: 16, color: TEXT_LIGHT, bold: true, align: "center" });
s3.addText("CUBO+ initiatives create a pipeline of Bitcoin-literate developers and communities.", { x: 6.9, y: 3.0, w: cW-0.4, h: 1.5, fontSize: 14, color: TEXT_MUTED, align: "center" });

// 4. The Solution
let s4 = addSlide("The Solution: Bitcoin-Native Marketplace");
s4.addText("Submit Digital Asset -> AI Evaluation -> Liquid Tokenization -> 2-of-3 Multisig Escrow", { x: 0.5, y: 1.5, w: 9, h: 0.4, fontSize: 14, color: ACCENT_CYAN, fontFace: "Consolas", bold: true });

s4.addTable([
  [ { text: "Feature", options: { bold: true, color: ACCENT_ORANGE } }, { text: "Our Platform", options: { bold: true } }, { text: "Traditional", options: { bold: true, color: TEXT_MUTED } } ],
  [ "Settlement", "Liquid 2-of-3 multisig", "Smart contract / Wire" ],
  [ "Custody", "HD wallet (AES-256-GCM)", "3rd Party Custodian" ],
  [ "Payments", "Lightning Network (Instant)", "Wire / Stablecoins" ],
  [ "Social / Marketing", "Standard Ad Campaigns (Nostr)", "None" ],
  [ "Transparency", "Nostr public treasury log", "Opaque Black Box" ]
], { x: 0.5, y: 2.2, w: 9, fill: { color: "1A1A24" }, border: { pt: 1, color: "333344" }, rowH: [0.5, 0.5, 0.5, 0.5, 0.5, 0.5], color: TEXT_LIGHT, fontSize: 14, fontFace: "Arial" });

// 5. Architecture (Full Screen Diagram)
let s5 = pres.addSlide({ masterName: "MASTER_SLIDE" });
s5.addText("Architecture: Integration & Data Flow", { x: 0.5, y: 0.4, w: 9, h: 0.6, fontSize: 24, fontFace: "Arial", color: TEXT_LIGHT, bold: true });
s5.addShape(pres.shapes.RECTANGLE, { x: 0.5, y: 1.0, w: 1.5, h: 0.05, fill: { color: ACCENT_ORANGE } });

const archBoxes = [
  { text: "Client Layer\nReact PWA / Nostr Bot", x: 0.5, y: 1.5, w: 2.5, h: 0.8, color: "1A1A24", border: "555555" },
  { text: "API Gateway\nNginx, JWT, TLS", x: 3.5, y: 1.5, w: 2.5, h: 0.8, color: "1A1A24", border: "555555" },
  { text: "Blockchain Layer\nLiquid, LND, BTC Core", x: 7.0, y: 1.5, w: 2.5, h: 0.8, color: "1E2761", border: ACCENT_CYAN },
  
  { text: "Auth Service\nKYC, 2FA, Nostr IDs", x: 1.0, y: 2.8, w: 2, h: 0.8, color: "1A1A24", border: ACCENT_ORANGE },
  { text: "Wallet Service\nHD Keys, Lightning", x: 4.0, y: 2.8, w: 2, h: 0.8, color: "1A1A24", border: ACCENT_ORANGE },
  { text: "Marketplace\nEscrow, Matching", x: 7.0, y: 2.8, w: 2, h: 0.8, color: "1A1A24", border: ACCENT_ORANGE },
  
  { text: "Tokenization Service\nAI Eval, Issuance", x: 1.0, y: 4.1, w: 2, h: 0.8, color: "1A1A24", border: ACCENT_ORANGE },
  { text: "Data Layer\nRedis Streams, PostgreSQL", x: 4.0, y: 4.1, w: 5, h: 0.8, color: "2C5F2D", border: "97BC62" }
];

archBoxes.forEach(b => {
  s5.addShape(pres.shapes.RECTANGLE, { x: b.x, y: b.y, w: b.w, h: b.h, fill: { color: b.color }, line: { color: b.border, width: 2 } });
  s5.addText(b.text, { x: b.x, y: b.y, w: b.w, h: b.h, align: "center", fontSize: 12, color: TEXT_LIGHT, fontFace: "Consolas", margin: 0 });
});

[
  { x: 3.0, y: 1.9, w: 0.5, h: 0 },
  { x: 6.0, y: 1.9, w: 1.0, h: 0 },
  { x: 2.0, y: 2.3, w: 0, h: 0.5 },
  { x: 5.0, y: 2.3, w: 0, h: 0.5 },
  { x: 8.0, y: 2.3, w: 0, h: 0.5 }
].forEach(l => {
  s5.addShape(pres.shapes.LINE, { x: l.x, y: l.y, w: l.w, h: l.h, line: { color: "555555", width: 2, dashType: "dash" } });
});

// 6. Technical Moat
let s6 = addSlide("Technical Moat & Security");
s6.addText([
  { text: "1. Bitcoin UTXO Security Model", options: { bold: true, color: ACCENT_ORANGE } },
  { text: "\nNo re-entrancy, no flash loan exploits. Native scripting via Liquid PSETs.\n", options: { fontSize: 14, color: TEXT_MUTED } },
  { text: "2. Confidential Transactions", options: { bold: true, color: ACCENT_ORANGE } },
  { text: "\nAmounts cryptographically hidden by default (importblindingkey).\n", options: { fontSize: 14, color: TEXT_MUTED } },
  { text: "3. Lightning Network Settlement", options: { bold: true, color: ACCENT_ORANGE } },
  { text: "\nSub-second finality via LND gRPC. Micro-investments made viable.\n", options: { fontSize: 14, color: TEXT_MUTED } },
  { text: "4. Nostr Ad Infrastructure", options: { bold: true, color: ACCENT_ORANGE } },
  { text: "\nProgrammatic Ad Campaigns via NIP-57. Budget management & targeted reach.\n", options: { fontSize: 14, color: TEXT_MUTED } },
  { text: "5. Security by Design", options: { bold: true, color: ACCENT_ORANGE } },
  { text: "\nAES-256-GCM wallets, TOTP 2FA, 23-finding pentest remediated.", options: { fontSize: 14, color: TEXT_MUTED } }
], { x: 0.5, y: 1.5, w: 9, h: 3.5, fontSize: 16, color: TEXT_LIGHT, fontFace: "Arial" });

// 7. Business Model Canvas
let s7 = addSlide("Business Model & Revenue Streams");
s7.addText("Core Revenue Generators:", { x: 0.5, y: 1.5, w: 9, h: 0.5, fontSize: 18, color: TEXT_LIGHT });
s7.addTable([
  [ { text: "Revenue Stream", options: { bold: true, color: ACCENT_CYAN } }, { text: "Mechanism", options: { bold: true } }, { text: "Margin Est.", options: { bold: true } } ],
  [ "Trading Fees", "0.5-1% per multisig matched trade", "85%+" ],
  [ "Tokenization Fees", "One-time fee per Liquid issuance", "90%+" ],
  [ "B2B API Access", "SaaS model, scoped API keys", "95%+" ],
  [ "Premium AI Eval", "Detailed asset reports", "70%+" ],
  [ "Ad Platform Fees", "Commission on automated ad campaigns", "80%+" ]
], { x: 0.5, y: 2.1, w: 9, fill: { color: "1A1A24" }, border: { pt: 1, color: "333344" }, rowH: [0.5, 0.4, 0.4, 0.4, 0.4, 0.4], color: TEXT_LIGHT, fontSize: 14, fontFace: "Arial" });

// 8. Roadmap
let s8 = addSlide("Traction & Roadmap");
s8.addText("Current State (2026): 11K+ LOC deployed, React PWA, Regtest complete.", { x: 0.5, y: 1.4, w: 9, h: 0.4, fontSize: 14, color: ACCENT_CYAN, fontFace: "Consolas" });

s8.addShape(pres.shapes.RECTANGLE, { x: 0.5, y: 2.2, w: 2.8, h: 2.5, fill: { color: "1E2761" }, line: { color: ACCENT_CYAN, width: 1 } });
s8.addText("Phase 1: Release", { x: 0.5, y: 2.4, w: 2.8, h: 0.4, fontSize: 16, color: TEXT_LIGHT, bold: true, align: "center" });
s8.addText("• End-to-end regtest\n• Security hardening\n• Observability", { x: 0.7, y: 2.9, w: 2.4, h: 1.5, fontSize: 14, color: TEXT_MUTED });

s8.addShape(pres.shapes.RECTANGLE, { x: 3.6, y: 2.2, w: 2.8, h: 2.5, fill: { color: "1A1A24" }, line: { color: ACCENT_ORANGE, width: 1 } });
s8.addText("Phase 2: Public Beta", { x: 3.6, y: 2.4, w: 2.8, h: 0.4, fontSize: 16, color: TEXT_LIGHT, bold: true, align: "center" });
s8.addText("• Testnet/Signet live\n• Digital Ad Platform v1\n• B2B API Launch", { x: 3.8, y: 2.9, w: 2.4, h: 1.5, fontSize: 14, color: TEXT_MUTED });

s8.addShape(pres.shapes.RECTANGLE, { x: 6.7, y: 2.2, w: 2.8, h: 2.5, fill: { color: "1A1A24" }, line: { color: "555555", width: 1 } });
s8.addText("Phase 3: Mainnet", { x: 6.7, y: 2.4, w: 2.8, h: 0.4, fontSize: 16, color: TEXT_LIGHT, bold: true, align: "center" });
s8.addText("• Mainnet deployment\n• Custody HSM compliance\n• Global expansion", { x: 6.9, y: 2.9, w: 2.4, h: 1.5, fontSize: 14, color: TEXT_MUTED });

// 9. The CUBO+ Flywheel
let s9 = addSlide("The Team & Growth Engine");
s9.addText("The CUBO+ Structural Advantage:", { x: 0.5, y: 1.5, w: 9, h: 0.5, fontSize: 18, color: TEXT_LIGHT, bold: true });

s9.addShape(pres.shapes.RECTANGLE, { x: 2, y: 2.5, w: 6, h: 2, fill: { color: "1A1A24" }, line: { color: ACCENT_ORANGE, width: 2 } });
s9.addText("Trading Fees -> Education Treasury -> CUBO+ Scholarships", { x: 2, y: 2.8, w: 6, h: 0.5, fontSize: 16, color: TEXT_LIGHT, bold: true, align: "center" });
s9.addText("More Users <- More Developers", { x: 2, y: 3.6, w: 6, h: 0.5, fontSize: 16, color: ACCENT_CYAN, bold: true, align: "center" });

s9.addText("Self-sustaining talent pipeline in El Salvador.", { x: 0.5, y: 4.8, w: 9, h: 0.5, fontSize: 14, color: TEXT_MUTED, align: "center", fontFace: "Consolas" });

// 10. The Ask
let s10 = addSlide("The Ask: $500K Pre-Seed");
s10.addText("Use of Proceeds:", { x: 0.5, y: 1.5, w: 4, h: 0.5, fontSize: 18, color: TEXT_LIGHT });
s10.addTable([
  [ "Engineering (50%)", "$250K", "Mainnet, HSM" ],
  [ "Operations (20%)", "$100K", "Testnet infra, Liquid nodes" ],
  [ "Growth (20%)", "$100K", "B2B API, Marketing" ],
  [ "Legal (10%)", "$50K", "Compliance framework" ]
], { x: 0.5, y: 2.2, w: 4.5, fill: { color: "1A1A24" }, border: { pt: 1, color: "333344" }, rowH: [0.5, 0.5, 0.5, 0.5], color: TEXT_LIGHT, fontSize: 12, fontFace: "Arial" });

s10.addText("18-Month Milestones:", { x: 5.5, y: 1.5, w: 4, h: 0.5, fontSize: 18, color: TEXT_LIGHT });
s10.addText([
  { text: "• 50+ Digital Assets Tokenized", options: { breakLine: true } },
  { text: "• 100M+ sats Monthly Vol", options: { breakLine: true } },
  { text: "• 5+ B2B API Partners", options: { breakLine: true } },
  { text: "• 20+ CUBO+ Scholarships" }
], { x: 5.5, y: 2.2, w: 4, h: 2, fontSize: 16, color: ACCENT_CYAN, fontFace: "Consolas", bullet: { type: "bullet" } });

pres.writeFile({ fileName: "C:/Users/R/Desktop/Pitch_Deck_PLAN_B2B_FLOW_v1.pptx" }).then(fileName => {
    console.log(`created file: ${fileName}`);
});
