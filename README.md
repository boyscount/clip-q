# poc-clipbot

> [ARCHITECTURE.md](ARCHITECTURE.md) — tech stack, ฐานข้อมูล, API, ข้อจำกัดที่รู้แล้ว
> [DEPLOY.md](DEPLOY.md) — Cloudflare Tunnel, VPS, ความปลอดภัย

POC: `product.json` → คลิปรีวิวสินค้าแนวตั้ง 1080×1920 พร้อมเสียงพากย์ไทยและซับเบิร์นติดภาพ

เป็นการพิสูจน์ "ท่อที่ 2" ของระบบแบบ Triple Bot (สร้างวิดีโอ) เท่านั้น
ยังไม่มีการดึงสินค้าจากแพลตฟอร์ม และไม่มีการโพสต์อัตโนมัติ

## ติดตั้ง

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
winget install Gyan.FFmpeg
```

## รัน

```bash
.venv\Scripts\python.exe tools\make_placeholders.py
.venv\Scripts\python.exe -m src.main product.json
```

ได้ไฟล์ที่ `out/clip.mp4`

ออปชัน

| flag | ความหมาย |
|---|---|
| `--voice male\|female` | สลับเสียงพากย์ |
| `--mode llm` | ให้ Claude เขียนสคริปต์แทน template (ต้องมี `ANTHROPIC_API_KEY` + `pip install anthropic`) |
| `--rate +15%` | เร่ง/ลดความเร็วเสียง |
| `--seed 7` | ล็อกการสุ่ม hook ให้ได้คลิปเดิม |
| `--bgm song.mp3` | ใส่เพลงประกอบ (คลอที่ 9% ใต้เสียงพูด) |
| `--keep` | ไม่ลบไฟล์ชั่วคราวใน `out/work/` |

## ท่อการทำงาน

```
product.json
   └─ script_gen.py   สคริปต์พูด (template สุ่ม hook / หรือ Claude)
        └─ voice.py   edge-tts ไทย ทีละบรรทัด → mix ลงไทม์ไลน์เดียว + cue ซับ
             └─ subtitle.py   cue → .ass (ป้ายราคา + ชื่อสินค้า + ซับ)
                  └─ render.py   Ken Burns ต่อรูป → concat → mux + เบิร์นซับ
                       └─ out/clip.mp4
```

## สิ่งที่เจอระหว่างทำ (สำคัญถ้าจะต่อยอด)

**1. edge-tts ฟรีแต่ไม่เสถียร**
ยิงข้อความเดิมซ้ำ ๆ บางครั้งได้ บางครั้งคืน `NoAudioReceived` แบบสุ่ม
ไม่เกี่ยวกับตัวข้อความ ต้องมี retry + backoff (ตั้งไว้ 6 ครั้ง ถ่างขึ้นเรื่อย ๆ)
รอบที่ทดสอบ มี 1 บรรทัดต้อง retry 5 ครั้ง ทำให้ขั้น TTS กิน 50 วินาที
**ถ้าทำจริงควรย้ายไป Google Cloud TTS หรือ Botnoi ที่มี SLA**

**2. เสียงไทยไม่ส่ง WordBoundary**
เสียง `en-US-*` ส่ง timing ระดับคำมาให้ แต่ `th-TH-*` ไม่ส่งเลย
เลยทำซับคาราโอเกะตามคำไม่ได้ ตอนนี้ตัดซับตามสัดส่วนจำนวนตัวอักษร
ซึ่งทำให้บางทีตัดกลางวลี (เช่น `... ในราคา 399` / `บาท หูฟังบลูทูธ ...`)
ถ้าอยากได้สวยกว่านี้ต้องใช้ **WhisperX** force-align เสียงที่ได้ หรือ **pythainlp** ตัดคำก่อน

**3. ซับต้องใช้ ASS ไม่ใช่ drawtext**
libass หาฟอนต์ไทยจากชื่อ (`Leelawadee UI`) และ shape สระ/วรรณยุกต์ถูกต้อง
drawtext ต้องชี้ path ฟอนต์เองและยังวางสระลอยผิดตำแหน่ง

**4. path บน Windows ในอาร์กิวเมนต์ filter**
`ass=C:\...` พังเพราะ ffmpeg อ่าน `:` เป็นตัวคั่น option
เลี่ยงด้วยการรัน ffmpeg โดยตั้ง cwd เป็นโฟลเดอร์งานแล้วใช้ชื่อไฟล์เปล่า ๆ

**5. ความเร็ว**
คลิป 26.5 วินาที บนเครื่องนี้: TTS 50s (ส่วนใหญ่คือ retry) + เรนเดอร์ภาพ 15s + mux 10s
ขั้น ffmpeg กิน CPU เต็ม ๆ ถ้าทำหลายคลิปต้องแยกเป็น worker + job queue

## ดึงสินค้าจริงจาก Shopee Affiliate

```bash
cp .env.example .env     # แล้วใส่ SHOPEE_APP_ID / SHOPEE_APP_SECRET
.venv\Scripts\python.exe tools\fetch_products.py --limit 12 --sync-app
```

ได้ `app/products.json` กับรูปใน `app/assets/products/<itemId>/01.jpg…`
แล้ว `make_preview_video.py` จะหยิบรูปจริงไปใช้แทนภาพไล่สีเอง

ยังไม่มีคีย์ก็ลองท่อทั้งเส้นได้ด้วย `--mock` ซึ่งสร้างรูปตัวอย่างขึ้นมาเอง
แล้ววิ่งผ่านขั้นดาวน์โหลด ครอป และทำช็อตจริงทุกขั้น

| flag | ความหมาย |
|---|---|
| `--limit N` | จำนวนสินค้า |
| `--keyword "หูฟัง"` | ค้นเฉพาะคำนี้ |
| `--shop-id 123` | เฉพาะร้านนี้ |
| `--mock` | ไม่เรียก API ใช้ข้อมูลตัวอย่าง |
| `--sync-app` | เขียนทับ `PRODUCTS` ใน `clipqueue.html` |
| `--fresh` | ลบรูปเดิมก่อนดึงใหม่ |

**ยังไม่ได้ทดสอบกับ API จริง** — [src/shopee.py](src/shopee.py) เขียนตามสเปกที่
ประกาศไว้ของ `productOfferV2` และลายเซ็น `SHA256 Credential=…` ถ้าชื่อฟิลด์ใน
เวอร์ชัน API ของบัญชีคุณไม่ตรง แก้ที่ `PRODUCT_QUERY` ได้ ข้อความ error จะบอกชื่อฟิลด์

## ยังไม่มีในนี้

- โพสต์อัตโนมัติขึ้น Shopee Video (ไม่มี public API ต้องกดอัปเอง)
## ให้ Claude เขียนจุดขาย

Shopee ส่งมาแค่ชื่อ ราคา ยอดขาย เรตติ้ง — ไม่มีจุดขายให้เอาไปทำสคริปต์
[src/bullets.py](src/bullets.py) ขอจาก Claude ทีละชุด 6 สินค้า ด้วย structured
output เลยได้คำตอบที่ผูกกับ item_id ตรง ๆ ไม่ต้องมานั่ง parse ข้อความ

```bash
# ใส่ ANTHROPIC_API_KEY ใน .env ก่อน
.venv\Scripts\python.exe tools\fetch_products.py --limit 12 --bullets llm
```

`--bullets auto` (ค่าเริ่มต้น) ใช้ Claude ถ้ามีคีย์ ไม่มีก็ถอยไปใช้ยอดขาย/เรตติ้ง
`--bullets fallback` บังคับไม่เรียก API

ผลลัพธ์ถูก cache ไว้ที่ `app/bullets-cache.json` คีย์ด้วย (item_id, ชื่อสินค้า)
รันซ้ำจึงจ่ายเฉพาะสินค้าใหม่ และสินค้าที่เปลี่ยนชื่อจะถูกเขียนใหม่ให้เอง

กติกาในพรอมต์ที่สำคัญ — **ห้ามแต่งสเปกที่ไม่มีในชื่อสินค้า** (ความจุแบต จำนวนชั่วโมง
มาตรฐานกันน้ำ ระยะรับประกัน) เพราะข้อมูลที่ API ส่งมามีแค่ชื่อ ถ้าปล่อยให้เดา
คลิปจะเคลมสิ่งที่สินค้าไม่ได้เป็น ซึ่งผิดเงื่อนไข affiliate ตรง ๆ

บรรทัดที่ได้ถูกกรองให้ยาว 16-36 ตัวอักษร เพราะ [script_gen.py](src/script_gen.py)
เลือก beat ตามจำนวนตัวอักษรเพื่อคุมความยาวคลิป บรรทัดที่ยาวผิดปกติจะทำให้แผนเพี้ยน

ทดสอบได้โดยไม่เสียเงิน: `python tools/test_bullets.py` ใช้ client ปลอม
ตรวจ batching, cache, ตัวกรองความยาว, กรณีโมเดลปฏิเสธ และกรณีไม่ได้ schema กลับมา
- คิวงาน / ฐานข้อมูลฝั่งเซิร์ฟเวอร์ (หน้าเว็บยังเป็น prototype)
- เทมเพลตภาพหลายแบบ, transition, เพลงประกอบที่คัดมา
