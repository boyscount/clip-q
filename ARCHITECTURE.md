# ClipQueue — รายละเอียดทางเทคนิค

ระบบสร้างคลิปรีวิวสินค้า Shopee Affiliate อัตโนมัติ ตั้งแต่ดึงสินค้า → เขียนสคริปต์ →
พากย์ไทย → เรนเดอร์ mp4 → ส่งเข้ามือถือ → ติดตามว่าคลิปไหนทำเงิน

เอกสารนี้ว่าด้วยว่า **อะไรอยู่ตรงไหนและทำไม**
วิธีดีพลอยอยู่ที่ [DEPLOY.md](DEPLOY.md) · วิธีใช้งานอยู่ที่ [README.md](README.md)

---

## 1. Tech stack

| ชั้น | เลือกใช้ | เวอร์ชันที่ทดสอบ | ทำไมเลือกตัวนี้ |
|---|---|---|---|
| ภาษา | Python | 3.13.14 | ffmpeg wrapper, Pillow, edge-tts อยู่ในระบบนิเวศเดียวกันหมด |
| Web API | FastAPI | 0.142.2 | pydantic ผูกกับ validation ได้ตรง ๆ ไม่ต้องเขียนชั้นตรวจเอง |
| ASGI server | uvicorn | 0.54.0 | มาตรฐานของ FastAPI |
| Validation | pydantic | 2.13.5 | บังคับขอบเขตทุกฟิลด์ก่อนถึง DB หรือ ffmpeg |
| ฐานข้อมูล | SQLite (WAL) | 3.50.4 | ไฟล์เดียว ไม่ต้องดูแล service · เขียนเผื่อย้าย Postgres ไว้แล้ว |
| เรนเดอร์วิดีโอ | ffmpeg | 9.0.2 | งานหลักทั้งหมด — ซูมภาพ ต่อคลิป เบิร์นซับ มิกซ์เสียง |
| เสียงพากย์ | edge-tts | 7.2.8 | ฟรี เสียงไทยใช้ได้ · **ไม่เสถียร** ดูข้อ 8 |
| ประมวลผลรูป | Pillow | 12.3.0 | ครอปรูปสินค้าเป็นช็อตจัตุรัส |
| HTTP client | requests | 2.34.2 | เรียก Shopee API และดาวน์โหลดรูป |
| เขียนจุดขาย | anthropic | 1.11.0 | `claude-opus-5-5` + structured output |
| หน้าเว็บ | HTML/CSS/JS ล้วน | - | ไม่มี build step · เปิดไฟล์ก็ดูได้ ดูข้อ 5 |
| เปิดออกเน็ต | Cloudflare Tunnel | 2026.10.0 | ได้ https ฟรี ไม่ต้องเปิดพอร์ตที่เราเตอร์ |

**ไม่มีในนี้โดยตั้งใจ:** ไม่มี frontend framework, ไม่มี ORM, ไม่มี Redis,
ไม่มี message broker, ไม่มี Docker ตอน dev — เพราะงานจริงคือ ffmpeg
ทุกชั้นที่เพิ่มมาคือของที่ต้องดูแลโดยไม่ช่วยให้คลิปออกเร็วขึ้น

---

## 2. โครงสร้างโค้ด

```
poc-clipbot/
├── src/        1,221 บรรทัด   ท่อผลิตคลิป — ไม่รู้จักเว็บหรือฐานข้อมูล
├── server/     2,284 บรรทัด   API, ฐานข้อมูล, worker, CLI
├── tools/      1,785 บรรทัด   สคริปต์สั่งงานและชุดทดสอบ
└── app/        2,738 บรรทัด   หน้าเว็บ 2 หน้า
```

### src/ — ท่อผลิต (ใช้ซ้ำได้โดยไม่ต้องมีเซิร์ฟเวอร์)

| ไฟล์ | บรรทัด | หน้าที่ |
|---|---|---|
| `shopee.py` | 268 | client Shopee Affiliate — ลายเซ็น SHA256, ดึงสินค้า, รายงาน conversion |
| `voice.py` | 184 | edge-tts ทีละบรรทัด → ตัดความเงียบ → มิกซ์ลงไทม์ไลน์เดียว + cue ซับ |
| `script_gen.py` | 173 | วางแผนสคริปต์ให้ได้ความยาวตามเป้า (ดูข้อ 6) |
| `bullets.py` | 156 | ให้ Claude เขียนจุดขายทีละชุด 6 สินค้า พร้อม cache |
| `render.py` | 94 | Ken Burns ต่อช็อต → concat → mux + เบิร์นซับ |
| `subtitle.py` | 93 | cue → ไฟล์ `.ass` ขนาดฟอนต์/ขอบตามสัดส่วนเฟรม |
| `images.py` | 93 | ดาวน์โหลดรูป ทำเป็นช็อตจัตุรัส 1200px |
| `main.py` | 81 | CLI รันท่อทั้งเส้นจาก `product.json` |
| `ff.py` | 49 | ครอบ ffmpeg/ffprobe + ข้อความ error ที่อ่านรู้เรื่อง |
| `speech.py` | 30 | โมเดลความเร็วการพูด อ่านค่าจาก `app/speech-model.json` |

### server/ — บริการ

| ไฟล์ | บรรทัด | หน้าที่ |
|---|---|---|
| `db.py` | 603 | schema, migration, การจองงานแบบ atomic, query ทั้งหมด |
| `app.py` | 307 | FastAPI — 13 route, auth, rate limit, security headers |
| `doctor.py` | 242 | ตรวจความพร้อม 17 อย่างก่อนใช้งาน |
| `service.py` | 230 | จัดตารางตามเพดาน, ตรวจสิทธิ์, idempotency |
| `worker.py` | 193 | จองงาน → เรียก `src/` → ส่งต่อเข้ามือถือ |
| `stats.py` | 183 | ดึงรายงาน conversion แล้วจับคู่กลับไปที่คลิป |
| `admin.py` | 177 | CLI จัดการผู้ใช้/บัญชี/token |
| `models.py` | 117 | pydantic — ดักข้อมูลขาเข้าทุกฟิลด์ |
| `notify.py` | 113 | แจ้งเตือน ntfy / telegram / discord |
| `outbox.py` | 85 | คัดลอกคลิปเข้าโฟลเดอร์ที่ซิงก์กับมือถือ |
| `config.py` | 34 | อ่าน `.env` — ต้องรันก่อนโมดูลอื่นอ่าน env |

### app/ — หน้าเว็บ

| ไฟล์ | บรรทัด | ขนาด | หน้าที่ |
|---|---|---|---|
| `clipqueue.html` | 2,442 | 167 KB | หน้าหลัก — คลังสินค้า ตั้งค่า คิว วิดีโอ log บัญชี settings |
| `post.html` | 296 | 14 KB | หน้ามือถือ 3 ขั้นสำหรับกดอัป |

---

## 3. ฐานข้อมูล

SQLite เปิด WAL · ไฟล์เดียวที่ `data/clipqueue.db` · ทุก id เป็น TEXT ·
เวลาเป็น ISO-8601 พร้อม offset · JSON เก็บเป็น text

### 7 ตาราง

```
users ──┬── accounts ──┐
        ├── products   ├── jobs ── conversions
        ├── events     │
        └── batches ───┘
```

| ตาราง | คอลัมน์ | เก็บอะไร |
|---|---|---|
| `users` | 5 | ผู้ใช้ · token เก็บเป็น SHA256 ไม่ใช่ข้อความตรง |
| `accounts` | 7 | บัญชี Shopee · `sub_id` · `daily_cap` (0 = ไม่จำกัด) |
| `products` | 15 | คลังสินค้า · PK รวม `(user_id, id)` |
| `jobs` | 25 | งานเรนเดอร์ · สถานะ · สคริปต์ · แคปชัน · path ไฟล์ |
| `conversions` | 12 | ออเดอร์จากรายงาน Shopee · **เงินเป็นสตางค์จำนวนเต็ม** |
| `events` | 6 | log ที่หน้าเว็บอ่าน |
| `batches` | 4 | กันยิงซ้ำ — PK `(user_id, key)` |

### 7 ดัชนี

`jobs_claim(status, scheduled_at)` ตัวนี้สำคัญสุด — worker ใช้จองงาน
ส่วน `jobs_sub(sub_id)` ใช้จับคู่รายงาน conversion กลับมาที่คลิป

### การตัดสินใจที่สำคัญ

**เงินเก็บเป็นสตางค์จำนวนเต็ม** — รายงานส่งมาเป็นสตริงทศนิยม ถ้าแปลงเป็น float
แล้วบวกสะสม ยอดจะเพี้ยนทีละนิด มีเทสคุมว่า `0.1 + 0.2` ต้องได้ 30 สตางค์พอดี

**สถานะงาน 5 แบบ** `queued → rendering → ready → posted` และ `failed`
ไม่มีสถานะ "กำลังอัป" เพราะเราไม่ได้อัปเอง

**เวลาเก็บตามเวลาไทย (+07:00)** ไม่ใช่ UTC — รอบ "19:30" ที่ผู้ใช้เลือกคือเวลานาฬิกา
ถ้าเก็บเป็น UTC ทุกคลิปจะเลื่อนไป 7 ชั่วโมง (เคยเป็นบั๊กจริง) ปรับด้วย `CLIPQUEUE_TZ_OFFSET`

**การจองงานแบบ atomic** — `BEGIN IMMEDIATE` + `UPDATE ... WHERE status='queued'`
แล้วเช็ค `rowcount` ทดสอบแล้วว่า worker 4 ตัวแย่งงานเดียวกันได้คนเดียว

**Migration ของจริง** — `CREATE TABLE IF NOT EXISTS` ไม่เพิ่มคอลัมน์ให้ตารางที่มีอยู่
`add_missing_columns()` จึงรัน **ก่อน** schema script (เพราะ script สร้างดัชนีบนคอลัมน์ใหม่)
แล้ว `backfill_sub_ids()` รันหลัง — ดึง sub_id จากลิงก์ที่ส่งออกไปแล้วจริง ไม่สร้างใหม่
เพราะลิงก์ที่โพสต์ไปแล้วเปลี่ยนไม่ได้

### ย้าย Postgres เมื่อไหร่ ย้ายยังไง

ย้ายเมื่อ: worker อยู่คนละเครื่อง · ผู้ใช้เกิน ~20 คน · ต้องการ point-in-time backup

สิ่งที่ต้องแก้มีแค่ `server/db.py` — connection factory, placeholder `?` → `%s`,
`INSERT ... ON CONFLICT` ใช้ได้ทั้งคู่ ประมาณ 30 บรรทัด ชั้นอื่นไม่ต้องแตะ

---

## 4. API

13 route · auth ด้วย `Authorization: Bearer <token>` ทุกเส้นยกเว้นที่ระบุ

| Method | Path | Auth | ทำอะไร |
|---|---|---|---|
| GET | `/api/health` | เปิด | ffmpeg พร้อมไหม outbox/notify ตั้งหรือยัง |
| GET | `/api/state` | token | สินค้า บัญชี งาน log ยอดรายได้ ทั้งหมดในครั้งเดียว |
| POST | `/api/queue` | token | สร้างคิว (201) — idempotent ด้วย key |
| GET | `/api/next` | token | คลิปถัดไปที่ต้องอัป (หน้ามือถือใช้) |
| POST | `/api/jobs/{id}/posted` | token | ทำเครื่องหมายว่าอัปแล้ว + ลบออกจาก outbox |
| POST | `/api/jobs/{id}/retry` | token | ส่งกลับเข้าคิว |
| GET | `/api/jobs/{id}/video` | token | ดาวน์โหลด mp4 (กัน path traversal) |
| PUT | `/api/accounts/{id}/cap` | token | ตั้งเพดานต่อวัน |
| GET | `/api/stats` | token | สรุปว่าคลิปไหนทำเงิน |
| POST | `/api/stats/sync` | token | ดึงรายงาน conversion จาก Shopee |
| DELETE | `/api/events` | token | ล้าง log |
| GET | `/` `/post` | เปิด | หน้าเว็บ (ตัว JS ขอ token เอง) |

`/api/docs` เปิดเฉพาะตอนไม่ได้อยู่โหมดสาธารณะ

### การดักข้อมูล

ทุกฟิลด์มีขอบเขตใน `server/models.py` ก่อนถึง DB หรือ ffmpeg

| กติกา | ค่า |
|---|---|
| สินค้าต่อคำขอ | 1-50 · รหัสซ้ำถูกยุบ |
| บัญชี | 1-10 |
| รอบเวลา | 1-12 · ต้องตรง `HH:MM` แบบ 24 ชม. |
| คลิปต่อสินค้า | 1-10 |
| **คลิปรวมต่อคำขอ** | **สูงสุด 200** — กันสั่ง 3,000 คลิปโดยไม่ตั้งใจ |
| วันที่เริ่ม | วันนี้ถึง +60 วัน |
| รูปแบบ/สัดส่วน/เสียง | เฉพาะค่าที่รู้จัก |
| ฟิลด์แปลกปลอม | ปฏิเสธ (`extra: forbid`) |

---

## 5. หน้าเว็บ

HTML/CSS/JS ล้วน ไม่มี build step ไม่มี dependency นอกจากฟอนต์จาก Google Fonts

**ทำงานได้สองโหมด** — ตอนโหลดจะลองเรียก `/api/health` ถ้าได้ก็ต่อเซิร์ฟเวอร์จริง
ถ้าไม่ได้ (เปิดจากไฟล์ หรือเป็น artifact ที่เผยแพร่) จะสลับเป็นโหมดสาธิตพร้อมป้ายบอก
ที่แถบซ้ายล่าง — เหตุผลคือหน้าเว็บคนละ origin เรียก API ไม่ได้อยู่แล้ว

**ธีมมืดเป็นค่าเริ่มต้น** ไม่ตาม `prefers-color-scheme` — โทนมืดอยู่บน `:root` ตรง ๆ
โหมดสว่างเป็น opt-in ผ่าน `[data-theme="light"]`

**Poll ทุก 2 วินาที** แต่วาดกริดสินค้าใหม่เฉพาะตอนคลังเปลี่ยนจริง (เทียบลายเซ็น)
ไม่งั้น `<img>` ถูกสร้างใหม่แล้วโหลดรูปซ้ำทุก 2 วินาที — เคยเป็นบั๊กจริง

**วิดีโอโหลดผ่าน fetch แล้วทำ blob** เพราะ `<video src>` ส่ง header `Authorization` ไม่ได้

---

## 6. ท่อผลิตคลิป

```
สินค้า + รูป
   │
   ├─ script_gen  เลือก beat ให้ได้ความยาวตามเป้า
   ├─ bullets     (ถ้ามีคีย์) ให้ Claude เขียนจุดขาย
   ├─ voice       edge-tts ทีละบรรทัด → ตัดความเงียบ → มิกซ์ + cue
   ├─ subtitle    cue → .ass
   ├─ images      รูปสินค้า → ช็อตจัตุรัส 1200px
   └─ render      Ken Burns → concat → mux + เบิร์นซับ → mp4
```

### โมเดลความเร็วการพูด

```
ความยาว = 0.3323 × จำนวนบรรทัด + 0.06046 × จำนวนตัวอักษร
```

16.5 ตัวอักษรไทย/วินาที ที่เสียง Premwadee +8% — fit จากคลิปจริง 12 คลิป
คลาดเคลื่อนเฉลี่ย 0.39 วินาที เก็บใน `app/speech-model.json` ทั้ง Python และหน้าเว็บ
อ่านจากไฟล์เดียวกัน เปลี่ยนเสียงแล้วรัน `tools/fit_speech_model.py` ใหม่ได้

`script_gen.plan_lines()` ใช้โมเดลนี้เลือก beat: hook กับ CTA ลงเสมอ ที่เหลือใส่
ต่อเมื่อทำให้ใกล้เป้าขึ้น ผลวัดจริง: เป้า 16/24/42 วินาที ได้ห่างเฉลี่ย **0.6 วินาที**

### ค่าเรนเดอร์

| ค่า | ใช้ |
|---|---|
| สัดส่วน | 9:16 (1080×1920) · 4:5 (1080×1350) · 1:1 (1080×1080) |
| รูปแบบ | รีวิวสั้น 16 วิ · โชว์สินค้า 24 วิ · เล่าเรื่อง 42 วิ |
| วิดีโอ | libx264 · CRF 20 · yuv420p · 30 fps · `+faststart` |
| เสียง | aac 128k · TTS 24 kHz mono |
| ขนาดไฟล์จริง | ~0.091 MB ต่อวินาที ที่ 1080×1920 |
| เวลาเรนเดอร์ | ~55 วินาที ต่อคลิป 17 วินาที บนเครื่องทดสอบ |

### ที่ต้องรู้เรื่อง ffmpeg

- **ซับต้องใช้ ASS ไม่ใช่ drawtext** — libass หาฟอนต์ไทยจากชื่อและ shape สระ/วรรณยุกต์ถูก
- **ฟอนต์ไทยขาดแล้วไม่ error** libass วาดเป็นช่องว่างเปล่าและ ffmpeg บอกว่าสำเร็จ
  `doctor` จึงเรนเดอร์ตัวอักษรไทยจริงแล้ววัดขนาดไฟล์
- **path บน Windows ใน filter** `ass=C:\...` พังเพราะ `:` เป็นตัวคั่น option
  แก้ด้วยการรัน ffmpeg โดยตั้ง cwd เป็นโฟลเดอร์งานแล้วใช้ชื่อไฟล์เปล่า

---

## 7. งานเบื้องหลัง

worker รันเป็น thread ในโปรเซส API (ค่าเริ่มต้น) หรือแยกด้วย `python -m server.worker`
จะรันกี่ตัวก็ได้เพราะการจองงานเป็น atomic

```
reclaim_stale()      งานที่ rendering เกิน 15 นาที = worker ตาย → คืนเข้าคิว
claim_next()         BEGIN IMMEDIATE + UPDATE WHERE status='queued'
render()             เรียก src/ · รายงาน progress 10 → 45 → 60 → 85
finish_job()         เก็บสคริปต์ แคปชัน ความยาว ขนาด path
handoff()            คัดลอกเข้า outbox + ส่งแจ้งเตือน (ล้มเหลวไม่กระทบงานที่เสร็จแล้ว)
```

`CLIPQUEUE_FAKE_RENDER=1` ข้าม ffmpeg ทั้งหมด — ใช้ในชุดทดสอบ

---

## 8. ข้อจำกัดที่รู้แล้ว

**edge-tts ไม่เสถียร** ยิงข้อความเดิมซ้ำ บางครั้งคืน `NoAudioReceived` แบบสุ่ม
ไม่เกี่ยวกับตัวข้อความ มี retry 6 ครั้ง backoff ถ่างขึ้นเรื่อย ๆ
**ถ้าใช้จริงจังควรย้ายไป Google Cloud TTS หรือ Botnoi ที่มี SLA**

**เสียงไทยไม่ส่ง WordBoundary** เสียงอังกฤษส่ง timing ระดับคำ แต่ `th-TH-*` ไม่ส่งเลย
จึงทำซับคาราโอเกะไม่ได้ ตอนนี้ตัดตามสัดส่วนตัวอักษร อยากแม่นกว่านี้ต้องใช้ WhisperX

**ยังไม่เคยยิง Shopee API จริง** ยืนยันแล้วว่า endpoint ถูก (`open-api.affiliate.shopee.co.th`
— ไม่ใช่ `.th`) และรูปแบบลายเซ็นถูก (ผ่าน error 10020 ไปเจอ 10035 "ยังไม่มีสิทธิ์")
แต่ชื่อฟิลด์ใน `productOfferV2` / `conversionReport` ยังไม่ได้พิสูจน์

**ไม่มีการอัปคลิปอัตโนมัติ** Shopee ไม่เปิด public API สำหรับ Shopee Video
ขั้นสุดท้ายจึงเป็นกึ่งอัตโนมัติ — ระบบเตรียมไฟล์ แคปชัน ลิงก์ให้พร้อมแล้วแจ้งเตือน

**หน้าเว็บยังไม่มีระบบสมัคร** สร้างผู้ใช้ด้วย CLI · token ไม่มีวันหมดอายุ ·
ไม่มีโควตา CPU ต่อผู้ใช้ — ยังไม่เหมาะเปิดให้คนนอก

---

## 9. ความปลอดภัย

| ชั้น | ทำอะไร |
|---|---|
| token | SHA256 ในฐานข้อมูล ไม่เก็บข้อความตรง · สุ่ม 32 ตัวอักษร |
| แยกผู้ใช้ | ทุก query ผูก `user_id` · ทดสอบแล้วว่าข้ามกันไม่ได้ |
| ดักข้อมูล | pydantic ทุกฟิลด์ก่อนถึง DB |
| path traversal | ไฟล์วิดีโอต้องอยู่ใน `renders/` เท่านั้น ถึงแม้ path จะมาจาก DB |
| rate limit | 120 คำขอ/นาที/token (กันพลาด ไม่ใช่กันโจมตี) |
| โหมดสาธารณะ | ไม่บูตถ้ามี demo token · ปฏิเสธ token < 24 ตัว · ปิด `/api/docs` |
| headers | `X-Frame-Options: DENY` · `nosniff` · `no-referrer` · `no-store` บน API |
| ค่าลับ | `.env` ถูก gitignore · App Secret ไม่ถูกบันทึกลง localStorage |
| แจ้งเตือน | **ไม่ใส่ token ลงในลิงก์** เพราะหัวข้อ ntfy เป็นสาธารณะ |

---

## 10. การทดสอบ

**193 เคส** รันได้โดยไม่ต้องมี ffmpeg เน็ต หรือ API key

| ชุด | เคส | ครอบคลุม |
|---|---|---|
| `test_server.py` | 105 | auth · ดักข้อมูล 23 เคส · แยกผู้ใช้ · จัดตาราง · idempotency · วงจรชีวิตงาน · worker แย่งงาน · งานค้าง · path traversal · rate limit · migration · โหมดสาธารณะ |
| `test_stats.py` | 50 | แปลงเงิน · จับคู่ sub-id · ยิงซ้ำไม่เพิ่มแถว · ออเดอร์ยกเลิก |
| `test_handoff.py` | 38 | ชื่อไฟล์ · แจ้งเตือน · outbox · หน้ามือถือ |
| `test_bullets.py` | - | batching · cache · ตัวกรองความยาว · โมเดลปฏิเสธ |

```bash
python tools/test_server.py
python tools/test_stats.py
python tools/test_handoff.py
python tools/test_bullets.py
```

---

## 11. ตัวแปรสภาพแวดล้อม

ดู [.env.example](.env.example) มีคำอธิบายครบ และ [DEPLOY.md](DEPLOY.md) ข้อ 4 มีตารางเต็ม

กลุ่มหลัก: `SHOPEE_*` (คีย์ API) · `CLIPQUEUE_OUTBOX` (โฟลเดอร์ซิงก์) ·
`CLIPQUEUE_NOTIFY` + คีย์ของ provider · `CLIPQUEUE_BASE_URL` (ลิงก์ในแจ้งเตือน) ·
`ANTHROPIC_API_KEY` (จุดขาย) · `CLIPQUEUE_PUBLIC` (เปิดการป้องกันตอนออกเน็ต)

`server/config.py` อ่าน `.env` โดย **ไม่ทับค่าที่ตั้งไว้ใน environment แล้ว**
โมดูลที่อ่าน env ตอน import (`notify`, `outbox`) เรียก `load_env()` เองเพื่อไม่ให้
ขึ้นกับลำดับ import

---

## 12. คำสั่งที่ใช้บ่อย

```bash
.\start.ps1                                    # รันในเครื่อง
.\tunnel.ps1                                   # เปิดออกอินเทอร์เน็ต
python -m server.doctor                        # ตรวจความพร้อม 17 อย่าง
python -m server.admin create-user --email ... # สร้างผู้ใช้
python -m server.admin rotate-token --email ...# เปลี่ยน token
python -m server.stats sync --days 14          # ดึงรายงาน Shopee
python -m server.stats show                    # คลิปไหนทำเงิน
python tools/fetch_products.py --mock --limit 6
python tools/backup.py --keep 14
python tools/prune.py --days 30 --dry-run
```
