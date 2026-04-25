const pptxgen = require("pptxgenjs");

let pres = new pptxgen();
pres.layout = 'LAYOUT_16x9';
pres.author = 'Ricardo Fuentes - Blockchain Developer';
pres.title = 'PLAN B2B FLOW Demo Deck';

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
    { text: { text: "CUBO+ Digital Asset Ecosystem", options: { x: 0.5, y: 5.42, w: 4, h: 0.2, fontSize: 10, color: TEXT_MUTED, fontFace: "Arial" } } },
    { text: { text: "LIVE DEMO", options: { x: 8, y: 5.42, w: 1.5, h: 0.2, fontSize: 10, color: ACCENT_ORANGE, fontFace: "Consolas", align: "right" } } }
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
s0.addText("Modular Financial Infrastructure on Bitcoin", { x: 1, y: 2.8, w: 8, h: 0.5, fontSize: 20, fontFace: "Consolas", color: ACCENT_CYAN });
s0.addShape(pres.shapes.RECTANGLE, { x: 1, y: 1.6, w: 2, h: 0.08, fill: { color: ACCENT_ORANGE } });

// 1. The Overview
let s1 = addSlide("The Solution: 5 Modular Verticals");
s1.addText("We don't build monoliths. We build infrastructure that businesses can use a la carte.", { x: 0.5, y: 1.5, w: 9, h: 0.5, fontSize: 18, color: TEXT_MUTED, fontFace: "Arial" });

const v_text = [
  "1. Wallet (Infrastructure API)",
  "2. Tokenization (Asset Issuance)",
  "3. Public Assets (Transparency)",
  "4. Campaigns (Nostr Ad Platform)",
  "5. Marketplace API (Escrow B2B)"
];

s1.addText(v_text.map(t => ({ text: t, options: { breakLine: true } })), { x: 0.5, y: 2.2, w: 8, h: 2.5, fontSize: 20, color: TEXT_LIGHT, fontFace: "Consolas", bullet: { type: "bullet" }, paraSpaceAfter: 15 });

// 2. Vertical 1
let s2 = addSlide("V1: Wallet (Core Infrastructure)");
s2.addText("Wallet-as-a-Service for Fintechs", { x: 0.5, y: 1.4, w: 9, h: 0.4, fontSize: 16, color: ACCENT_CYAN, fontFace: "Consolas" });
s2.addText([
  { text: "Tech Stack:", options: { bold: true, color: ACCENT_ORANGE } },
  { text: " HD Wallets (BIP-84/86), LND gRPC, Elements RPC.\n", options: { color: TEXT_LIGHT } },
  { text: "Security:", options: { bold: true, color: ACCENT_ORANGE } },
  { text: " AES-256-GCM encryption in rest, TOTP 2FA.\n", options: { color: TEXT_LIGHT } },
  { text: "Use Case:", options: { bold: true, color: ACCENT_ORANGE } },
  { text: " A local Fintech wants to offer Lightning/Liquid deposits. They use our API instead of managing their own complex node infrastructure.", options: { color: TEXT_LIGHT } }
], { x: 0.5, y: 2.0, w: 8, h: 2.5, fontSize: 16, fontFace: "Arial" });

// 3. Vertical 2
let s3 = addSlide("V2: Tokenization (Asset Issuance)");
s3.addText("Native Liquid Assets backed by AI", { x: 0.5, y: 1.4, w: 9, h: 0.4, fontSize: 16, color: ACCENT_CYAN, fontFace: "Consolas" });
s3.addText([
  { text: "Tech Stack:", options: { bold: true, color: ACCENT_ORANGE } },
  { text: " AI Evaluation Engine, Liquid Native Issuance (UTXO).\n", options: { color: TEXT_LIGHT } },
  { text: "Security:", options: { bold: true, color: ACCENT_ORANGE } },
  { text: " No Turing-complete smart contracts = No EVM exploits.\n", options: { color: TEXT_LIGHT } },
  { text: "Use Case:", options: { bold: true, color: ACCENT_ORANGE } },
  { text: " A commercial real estate developer digitizes a building into 10,000 units on Liquid to distribute to institutional investors easily.", options: { color: TEXT_LIGHT } }
], { x: 0.5, y: 2.0, w: 8, h: 2.5, fontSize: 16, fontFace: "Arial" });

// 4. Vertical 3
let s4 = addSlide("V3: Public Assets (Catalog)");
s4.addText("Transparency & Fractionalization", { x: 0.5, y: 1.4, w: 9, h: 0.4, fontSize: 16, color: ACCENT_CYAN, fontFace: "Consolas" });
s4.addText([
  { text: "Tech Stack:", options: { bold: true, color: ACCENT_ORANGE } },
  { text: " Liquid Confidential Transactions, PostgreSQL, Redis.\n", options: { color: TEXT_LIGHT } },
  { text: "Feature:", options: { bold: true, color: ACCENT_ORANGE } },
  { text: " Cryptographic proof of reserves without compromising user privacy.\n", options: { color: TEXT_LIGHT } },
  { text: "Use Case:", options: { bold: true, color: ACCENT_ORANGE } },
  { text: " An aggregator or crowdfunding portal lists our verified assets. Investors can audit the asset supply on-chain while keeping owner identities secret.", options: { color: TEXT_LIGHT } }
], { x: 0.5, y: 2.0, w: 8, h: 2.5, fontSize: 16, fontFace: "Arial" });

// 5. Vertical 4
let s5 = addSlide("V4: Nostr Ad Platform");
s5.addText("Programmatic Marketing & Campaigns", { x: 0.5, y: 1.4, w: 9, h: 0.4, fontSize: 16, color: ACCENT_CYAN, fontFace: "Consolas" });
s5.addText([
  { text: "Tech Stack:", options: { bold: true, color: ACCENT_ORANGE } },
  { text: " NIP-57 Zaps, Nostr Relays, L402 Macaroons.\n", options: { color: TEXT_LIGHT } },
  { text: "Feature:", options: { bold: true, color: ACCENT_ORANGE } },
  { text: " Decentralized, automated referral rewards and API monetization.\n", options: { color: TEXT_LIGHT } },
  { text: "Use Case:", options: { bold: true, color: ACCENT_ORANGE } },
  { text: " The real estate developer sets a marketing budget. Influencers share the asset on Nostr and instantly receive Lightning Zaps for generating verified traction.", options: { color: TEXT_LIGHT } }
], { x: 0.5, y: 2.0, w: 8, h: 2.5, fontSize: 16, fontFace: "Arial" });

// 6. Vertical 5
let s6 = addSlide("V5: Marketplace API (Escrow B2B)");
s6.addText("Trust-as-a-Service", { x: 0.5, y: 1.4, w: 9, h: 0.4, fontSize: 16, color: ACCENT_CYAN, fontFace: "Consolas" });
s6.addText([
  { text: "Tech Stack:", options: { bold: true, color: ACCENT_ORANGE } },
  { text: " Liquid PSETs, 2-of-3 Multisig, Atomic Swaps.\n", options: { color: TEXT_LIGHT } },
  { text: "Feature:", options: { bold: true, color: ACCENT_ORANGE } },
  { text: " B2B API to secure external transactions independently of tokenization.\n", options: { color: TEXT_LIGHT } },
  { text: "Use Case:", options: { bold: true, color: ACCENT_ORANGE } },
  { text: " A third-party heavy machinery marketplace wants secure payments. Buyers deposit BTC into our generated 2-of-3 escrow. Once delivery is confirmed, we release the funds.", options: { color: TEXT_LIGHT } }
], { x: 0.5, y: 2.0, w: 8, h: 2.5, fontSize: 16, fontFace: "Arial" });

// 7. Architecture Overview
let s7 = pres.addSlide({ masterName: "MASTER_SLIDE" });
s7.addText("Architecture Overview", { x: 0.5, y: 0.4, w: 9, h: 0.6, fontSize: 24, fontFace: "Arial", color: TEXT_LIGHT, bold: true });
s7.addShape(pres.shapes.RECTANGLE, { x: 0.5, y: 1.0, w: 1.5, h: 0.05, fill: { color: ACCENT_ORANGE } });

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
  s7.addShape(pres.shapes.RECTANGLE, { x: b.x, y: b.y, w: b.w, h: b.h, fill: { color: b.color }, line: { color: b.border, width: 2 } });
  s7.addText(b.text, { x: b.x, y: b.y, w: b.w, h: b.h, align: "center", fontSize: 12, color: TEXT_LIGHT, fontFace: "Consolas", margin: 0 });
});

[
  { x: 3.0, y: 1.9, w: 0.5, h: 0 },
  { x: 6.0, y: 1.9, w: 1.0, h: 0 },
  { x: 2.0, y: 2.3, w: 0, h: 0.5 },
  { x: 5.0, y: 2.3, w: 0, h: 0.5 },
  { x: 8.0, y: 2.3, w: 0, h: 0.5 }
].forEach(l => {
  s7.addShape(pres.shapes.LINE, { x: l.x, y: l.y, w: l.w, h: l.h, line: { color: "555555", width: 2, dashType: "dash" } });
});

// 8. The Ask
let s8 = addSlide("Traction & The Ask");
s8.addText("Ready for Public Beta. 11K+ LOC deployed. Penetration test completed.", { x: 0.5, y: 1.4, w: 9, h: 0.4, fontSize: 14, color: ACCENT_CYAN, fontFace: "Consolas" });

s8.addText("Use of Proceeds ($500K Pre-Seed):", { x: 0.5, y: 2.0, w: 4, h: 0.5, fontSize: 18, color: TEXT_LIGHT });
s8.addTable([
  [ "Engineering (50%)", "Mainnet, HSM Custody" ],
  [ "Operations (20%)", "Testnet infra, Liquid nodes" ],
  [ "Growth (20%)", "B2B API, Marketing" ],
  [ "Legal (10%)", "Compliance framework" ]
], { x: 0.5, y: 2.6, w: 4.5, fill: { color: "1A1A24" }, border: { pt: 1, color: "333344" }, rowH: [0.5, 0.5, 0.5, 0.5], color: TEXT_LIGHT, fontSize: 14, fontFace: "Arial" });

s8.addText("18-Month Milestones:", { x: 5.5, y: 2.0, w: 4, h: 0.5, fontSize: 18, color: TEXT_LIGHT });
s8.addText([
  { text: "• 50+ Digital Assets Tokenized", options: { breakLine: true } },
  { text: "• 100M+ sats Monthly Vol", options: { breakLine: true } },
  { text: "• 5+ B2B API Partners", options: { breakLine: true } },
  { text: "• 20+ CUBO+ Scholarships" }
], { x: 5.5, y: 2.6, w: 4, h: 2, fontSize: 16, color: ACCENT_CYAN, fontFace: "Consolas", bullet: { type: "bullet" } });

pres.writeFile({ fileName: "C:/Users/R/Desktop/Pitch_Deck_PLAN_B2B_FLOW/Demo_Pitch_5_Vertientes.pptx" }).then(fileName => {
    console.log(`created file: ${fileName}`);
});
