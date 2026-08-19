# FPL Bot 2.0 — Dnevni izvještaji, AI sažetci, tjedni pregled i chat uživo

Ovo je nadograđena verzija tvog FPL bota. Sad radi ovo:

- 📅 **Pun dnevni izvještaj** ujutro (ozljede, cijene, forma, preporuke, raspored, chip savjeti, tvoja ekipa)
- 🆕 **Provjera novih vijesti svaka 3 sata** — novi članci i YouTube videi od provjerenih FPL izvora, s **AI sažetkom** (2-3 rečenice) ako je sadržaj dostupan za čitanje/titlovanje, inače samo naslov + link
- 📊 **Tjedni pregled** — ide ponedjeljkom (ili utorkom ako se u ponedjeljak još igra), s konsenzusom analitičara i personaliziranim AI prijedlogom za tvoju ekipu
- 💬 **Chat uživo** — pitaš bota pitanja na Telegramu, odgovara koristeći svježe podatke

---

## Prije nego kreneš — što ti treba

1. Telegram račun
2. GitHub račun (besplatan)
3. Anthropic Console račun s API ključem (plaćaš samo potrošeno, procjena $2-8/mjesec)

---

## Korak 1 — Telegram bot

1. U Telegramu otvori razgovor s **@BotFather**.
2. Pošalji `/newbot`, upiši ime i korisničko ime (mora završavati na `bot`).
3. Spremi **token** koji dobiješ (izgleda kao `123456789:ABCdef...`).
4. Pošalji svom botu bilo koju poruku (npr. "bok").
5. U pregledniku otvori: `https://api.telegram.org/bot<TVOJ_TOKEN>/getUpdates` i pronađi `"chat":{"id":123456789,...}` — to je tvoj **chat ID**.

## Korak 2 — Anthropic API ključ

1. Idi na **console.anthropic.com**, napravi račun (odvojeno od claude.ai chata).
2. **Settings → Billing** — dodaj karticu.
3. **Settings → Limits** — postavi mjesečni hard cap, npr. **$10**, radi sigurnosti.
4. **Settings → API Keys → Create Key** — nazovi ga npr. "fpl-bot", kopiraj i spremi ključ (počinje s `sk-ant-...`), prikazuje se samo jednom.

## Korak 3 — GitHub repozitorij

**Bitno:** ovaj repozitorij treba biti **Public** (javan), ne Private. Razlog: chat funkcija (Korak 6) provjerava nove poruke svakih 5 minuta, što je oko 8600 pokretanja mjesečno — to je iznad besplatnog limita od 2000 min/mjesec za privatne repozitorije, ali za **javne repozitorije GitHub Actions je potpuno besplatan i neograničen**.

Ovo je sigurno jer:
- Svi tvoji tajni podaci (Telegram token, API ključevi, Team ID) idu u **GitHub Secrets**, koji ostaju skriveni čak i u javnom repozitoriju — nitko ih ne može vidjeti.
- Kod koji se vidi je samo generička logika bota, ništa osobno.
- Tvoja pitanja/odgovori u chatu se **ne spremaju** u repozitorij, samo koji je zadnji Telegram update obrađen (broj, bez sadržaja poruka).
- Jedino što se javno vidi u `state.json` su AI sažetci javno dostupnih FPL članaka/videa (npr. "Fantasy Football Scout: preporučuju Haalanda za kapetana zbog...") — ništa osobno, ali budi svjestan da je to vidljivo ako te to smeta.

Ako ti privatnost ipak više odgovara, možeš ostaviti repozitorij **Private** i jednostavno preskočiti Korak 6 (chat uživo) — sve ostalo (dnevni izvještaj, vijesti, tjedni pregled) ostaje daleko ispod besplatnog limita čak i kao privatan repo.

1. Na github.com klikni **New repository**, ime npr. `fpl-bot`, odaberi **Public** (ili Private ako preskačeš chat), **Create repository**.
2. **Add file → Upload files** — povuci/spusti SVE datoteke i mape iz ovog paketa (uključujući `.github` mapu — bitno da struktura ostane ista). **Commit changes**.

## Korak 4 — Postavi Secrets

**Settings → Secrets and variables → Actions → New repository secret**, dodaj svaki od ovih:

| Ime | Vrijednost |
|---|---|
| `TELEGRAM_BOT_TOKEN` | token iz Koraka 1 |
| `TELEGRAM_CHAT_ID` | chat ID iz Koraka 1 |
| `ANTHROPIC_API_KEY` | ključ iz Koraka 2 |
| `FPL_TEAM_ID` | (opcionalno) tvoj FPL Team ID — broj iz URL-a na fantasy.premierleague.com |

## Korak 5 — Testiraj

Repozitorij ima **3 odvojena workflowa** u tabu **Actions**, svaki se može ručno pokrenuti (Run workflow) za test:

1. **FPL Dnevni Izvještaj i Vijesti** — pokreni s `mode: full` za pun izvještaj, ili `mode: news` za provjeru vijesti
2. **FPL Tjedni Pregled** — pokreni bilo kad za test (stvarno će poslati samo ako je trenutno kolo skoro gotovo — ako ne pošalje ništa u testu, to je očekivano ponašanje, ne greška)
3. **FPL Chat Bot (uzivo)** — pošalji botu pitanje na Telegramu PRIJE nego pokreneš ovaj workflow ručno, da vidiš odgovara li

Ako nešto ne uspije, klikni na taj pokrenuti workflow da vidiš log grešaka.

---

## Kako prilagoditi

- **Dodati YouTube kanal** → `sources.py`, lista `YOUTUBE_CHANNELS`
- **Dodati stranicu s člancima** → `sources.py`, lista `ARTICLE_FEEDS` (treba RSS feed URL te stranice)
- **Promijeniti vremena** → uredi `cron:` linije u `.github/workflows/*.yml` (uvijek UTC)
- **Koliko sati unatrag gleda nove vijesti** → `RSS_LOOKBACK_HOURS` u `daily-and-news.yml`

---

## Granice i napomene

- **Video bez titlova** → AI ne može "poslušati" zvuk (to bi zahtijevalo preuzimanje videa, što krši YouTube uvjete korištenja) — takav video dobiješ samo kao naslov + link, bez sažetka.
- **Konsenzus i personalizirani prijedlozi** su AI interpretacija javno dostupnih sažetaka, ne kladioničke kvote niti zajamčen savjet.
- **Tjedni pregled** čeka do ponedjeljka (ili utorka ako se ponedjeljkom još igra) — ako se neka utakmica odgodi za tjedne/mjesece, pregled je jasno označi kao neodigranu i nastavlja dalje, ne čeka unedogled.
- **Trošak** — realno očekuj $2-8/mjesec ovisno o prometu vijesti i koliko pitanja postaviš botu; hard cap od $10 u Anthropic Console te štiti od iznenađenja.
- Kod je organiziran u module (`common.py`, `sources.py`, `video_transcripts.py`) koje dijele `daily_report.py`, `news_check.py`, `weekly_review.py` i `chat_bot.py` — ako želiš izmjenu, reci mi koju datoteku i što točno, ili je uredi sam direktno na GitHubu.
