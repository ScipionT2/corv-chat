# Nova AI — Membership Tiers & Monetization Plan

_Last updated: 2026-05-22_

---

## 🆓 Free Tier — $0/month
**Goal:** Get users in the door. Enough to be useful, limited enough to upsell.

### AI Models (4 models — basic tier)
- Llama 3.3 70B (Groq)
- Qwen 3 32B (Groq)
- DeepSeek V3.1 (SambaNova)
- Gemini 2.0 Flash (Google)

### Restrictions
- **15 messages/day** limit (resets at midnight UTC)
- **No file/image attachments** (greyed out, shows "Upgrade to Pro")
- **No web search** toggle
- **1 conversation** saved at a time (new chat clears old one)
- **No chat export**
- **Basic image generation** — 3 images/day (Flux default only)
- **No thinking mode** (reasoning hidden)
- **Standard response speed** (no priority queue)
- **Nova branding** — "Powered by Nova AI" in responses
- **No custom agents**
- **No API key support** (can't bring own keys)

---

## ⭐ Pro Tier — $12/month ($9/month annual)
**Goal:** Power users, students, developers. The sweet spot.
**Payment:** Ko-fi (ko-fi.com/escipion17) or PayPal (Pedrozaescipion@gmail.com)

### AI Models (12 models — all free models unlocked)
- Everything in Free, plus:
- Llama 3.1 70B, Llama 4 Scout, Llama 4 Maverick (Groq)
- GPT-OSS 120B — Groq + SambaNova
- MiniMax M2.7 196K context (SambaNova)
- DeepSeek V3.2 (SambaNova)
- Gemini 2.5 Flash (Google)

### Perks
- **Unlimited messages** — no daily cap
- **File & image attachments** — upload docs, code, images for analysis
- **Web search** toggle — AI can browse the web
- **Unlimited chat history** — all conversations saved forever
- **Chat export** — download conversations as Markdown/PDF
- **Unlimited image generation** — all 5 Flux models + Turbo
- **Thinking mode** — see AI reasoning process
- **Priority speed** — faster response queue
- **No branding** — clean responses
- **Custom agents** — create/edit agents with custom system prompts
- **BYOK support** — bring your own OpenAI/Anthropic/OpenRouter keys
- **Dark + Light themes**
- **Email support**

---

## 💎 Elite Tier — $29/month ($24/month annual)
**Goal:** Professionals, power users who want frontier models included.
**Payment:** Ko-fi or PayPal

### AI Models (ALL models — frontier included with our keys)
- Everything in Pro, plus:
- **GPT-4o** — included (our key, no BYOK needed)
- **GPT-4o Mini** — included
- **Claude Haiku 3.5** — included
- **OpenRouter auto** — included
- Still can BYOK for GPT-5.5, Claude Opus/Sonnet if they want cutting-edge

### Perks
- Everything in Pro, plus:
- **Frontier models included** — GPT-4o, Claude Haiku on us
- **200 messages/day with frontier models** (unlimited with free models)
- **Image generation with DALL-E 3** — 20 images/day (our key)
- **Voice input** — speech-to-text in browser
- **Voice output** — text-to-speech responses
- **Priority support** — direct email, faster response
- **API access** — programmatic access to Nova API
- **Custom themes** — create and share themes
- **Early access** — beta features before everyone else
- **Profile badge** — Elite badge on profile

---

## 🏢 Enterprise — Custom Pricing
**Goal:** Teams, companies, orgs.
**Contact:** contact@nov-assistant.com

### Everything in Elite, plus:
- **Dedicated infrastructure** — isolated instance
- **SSO & team management** — SAML, Google Workspace, Microsoft
- **Admin dashboard** — usage analytics, user management
- **SLA guarantee** — 99.9% uptime
- **Custom model fine-tuning** — train on your data
- **Dedicated support manager**
- **Custom domain** — yourcompany.nova-ai.com
- **Data residency** — choose your region
- **Audit logs** — compliance & security
- **Volume pricing** — bulk discounts
- **On-premise option** — self-hosted deployment

---

## Implementation Priority

### Phase 1 — Ship Now (this week)
1. Add `tier` field to user DB (free/pro/elite/enterprise)
2. Add message counter (daily limit for free tier)
3. Gate file attachments behind Pro
4. Gate web search behind Pro
5. Limit free to 4 models + 15 msgs/day + 3 images/day
6. Show upgrade prompts when hitting limits
7. Update pricing page with new tiers

### Phase 2 — Next Week
8. Add Elite tier with frontier models on our keys
9. Chat export (Markdown/PDF)
10. Thinking mode gating
11. Custom agents gating
12. Ko-fi/PayPal webhook to auto-upgrade users

### Phase 3 — Post-Launch
13. Voice input/output for Elite
14. API access
15. Enterprise features
16. Stripe integration (replace Ko-fi for proper recurring billing)

---

## Revenue Projections

| Tier | Price | If 100 users | If 500 users | If 1000 users |
|------|-------|-------------|-------------|--------------|
| Free | $0 | $0 | $0 | $0 |
| Pro | $12/mo | $1,200/mo | $6,000/mo | $12,000/mo |
| Elite | $29/mo | $2,900/mo | $14,500/mo | $29,000/mo |

**Cost basis:**
- GCP VM (e2-micro): Free tier
- Groq API: Free (rate limited)
- SambaNova API: Free (rate limited)
- Domain + SSL: ~$10/year
- **Margin: ~95%+ on Pro, ~70%+ on Elite** (frontier model costs eat into Elite)

---

## Upgrade Flow UX

### Free user hits limit:
```
╔══════════════════════════════════════╗
║  ⚡ Daily limit reached (15/15)     ║
║                                      ║
║  Upgrade to Pro for unlimited        ║
║  messages, file uploads, and more.   ║
║                                      ║
║  [Upgrade to Pro — $12/mo]           ║
║  [Maybe later]                       ║
╚══════════════════════════════════════╝
```

### Free user tries gated feature:
```
╔══════════════════════════════════════╗
║  🔒 Pro Feature                      ║
║                                      ║
║  File attachments are available      ║
║  on Pro and above.                   ║
║                                      ║
║  [See Plans]  [Not now]              ║
╚══════════════════════════════════════╝
```
