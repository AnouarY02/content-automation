# AY Automatisering — Projectoverzicht
*Anouar Youfi | Heerenveen | info@ayautomatisering.nl*

---

## 📁 Mapstructuur

```
C:/AY-Automatisering/
│
├── dossiertijd/
│   ├── app/              ← Web-app / portal (GitHub + Vercel)
│   │                       GitHub: github.com/AnouarY02/dossiertijd-app
│   │                       Live:   dossiertijd.vercel.app
│   │
│   ├── website/          ← Marketingwebsite (GitHub + Vercel)
│   │                       GitHub: github.com/AnouarY02/dossiertijd-website
│   │                       Live:   dossiertijd-website.vercel.app
│   │
│   └── compliance/       ← AVG/privacy documenten
│       ├── 01-DPIA-dossiertijd-v1.md
│       ├── 02-dataflow-document-v1.md
│       ├── 03-verwerkersovereenkomst-template-v1.md
│       ├── 04-ISMS-informatiebeveiligingsbeleid-v1.md
│       ├── 05-incident-datalekprocedure-v1.md
│       ├── 06-SCC-subverwerker-instructies.md
│       └── 10-letter-of-intent-pilotklant-template.md
│
└── ayautomatisering/
    ├── website/          ← Next.js website (GitHub + Vercel)
    │                       GitHub: github.com/AnouarY02/ayautomatisering
    │                       Live:   ayautomatisering.nl
    │
    └── acquisitie/       ← Strategie & prospects
        ├── acquisitie-strategie.md      ← Email templates + aanpak
        ├── prospects-met-email.md       ← Bedrijven met email + websitescore
        ├── prospects.csv               ← Bijhoudsysteem (open in Excel)
        └── prospecting-handleiding.md   ← Google Maps zoekopdrachten
```

---

## 🚀 Werken aan DossierTijd app (portal)

```bash
cd C:/AY-Automatisering/dossiertijd/app
npm run dev          # lokaal starten op localhost:3000
git add .
git commit -m "..."
git push origin main  # → Vercel deployt automatisch
```

## 🌐 Werken aan DossierTijd website (marketing)

```bash
cd C:/AY-Automatisering/dossiertijd/website
npm run dev          # lokaal starten
git add .
git commit -m "..."
git push origin main  # → Vercel deployt automatisch
```

## 🌐 Werken aan AY Automatisering website

```bash
cd C:/AY-Automatisering/ayautomatisering/website
pnpm dev             # lokaal starten
git add .
git commit -m "..."
git push origin master  # → Vercel deployt automatisch
```

---

## 📋 Nog te doen

- [ ] KvK-nummer invullen in compliance docs (⏳ inschrijving loopt)
- [ ] Telefoonnummer invullen in `compliance/05-incident-datalekprocedure-v1.md`
- [ ] info@dossiertijd.nl mailbox aanmaken in TransIP

---

## 🔑 Belangrijke accounts

| Service | Account | Gebruik |
|---|---|---|
| Vercel | anouary02@gmail.com | Hosting beide projecten |
| GitHub | AnouarY02 | Code repositories |
| TransIP | — | Domeinen + e-mail |
| OpenAI | — | GPT-4o API voor DossierTijd |
