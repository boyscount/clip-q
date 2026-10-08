"""ทดสอบการสร้างช็อตคนด้วย AI — ไม่ยิง Google จริง ใช้ตัวแทนที่ตอบแทน

รูปร่างคำขอกับคำตอบที่จำลองไว้ตรงกับของจริงที่ยืนยันมาแล้ว
  · ส่งคำขอรูปแบบนี้ได้ HTTP 429 (ผ่านการตรวจ ติดแค่โควตา)
  · ส่งคำขอที่สะกดผิดได้ HTTP 400 — แปลว่า Google ตรวจรูปแบบก่อนโควตาจริง

    python tools/test_gen.py
"""

from __future__ import annotations

import base64
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import aigen, persona, scene  # noqa: E402

PASS = FAIL = 0
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()


def check(label, got, want=True):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok    {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}\n          ได้ {got!r}\n          คาด {want!r}")


class Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("ไม่ใช่ JSON")
        return self._payload


def ok_payload(parts=None):
    return {"candidates": [{"finishReason": "STOP", "content": {"parts": parts or [
        {"inlineData": {"mimeType": "image/png", "data": PNG}}]}}]}


class Stub:
    """แทน requests — จำคำขอไว้ตรวจ และตอบตามคิวที่ตั้งไว้"""

    def __init__(self, *responses):
        self.queue = list(responses)
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "body": json})
        return self.queue.pop(0) if self.queue else Resp(200, ok_payload())


def main() -> int:
    saved_key = os.environ.get("GOOGLE_API_KEY")
    os.environ["GOOGLE_API_KEY"] = "AIza-ทดสอบ"
    try:
        print("\n1. ประกอบคำสั่ง")
        p = aigen.build_prompt("cafe", "a Thai woman in her 20s", "ครีมกันแดด VENITA")
        check("ใส่รูปลักษณ์ที่พิมพ์มา", "a Thai woman in her 20s" in p, True)
        check("ใส่สินค้าที่ถือ", "ครีมกันแดด VENITA" in p, True)
        check("ใส่ฉากจากทะเบียน", scene.SCENES["cafe"].prompt in p, True)
        check("บังคับแนวตั้งเสมอ", "9:16" in p, True)
        check("สั่งห้ามมีตัวหนังสือในภาพ", "no text" in p, True)

        bare = aigen.build_prompt(None, "", "")
        check("ไม่ใส่อะไรเลย ก็ยังได้คำสั่งที่ใช้ได้", len(bare) > 40, True)
        check("ไม่มีฉาก ก็ไม่มี prompt ฉากปน",
              any(s.prompt in bare for s in scene.SCENES.values()), False)
        check("auto ไม่นับเป็นฉาก",
              aigen.build_prompt("auto", "", "") == bare, True)

        print("\n2. ยิงสำเร็จ")
        stub = Stub(Resp(200, ok_payload()))
        got = aigen.generate_images("x", 1, session=stub)
        check("ได้รูปกลับมาหนึ่งใบ", len(got), 1)
        check("เป็นไบต์ของไฟล์จริง", got[0][0].startswith(b"\x89PNG"), True)
        check("รู้นามสกุลจาก mimeType", got[0][1], ".png")

        call = stub.calls[0]
        check("ยิงไปที่รุ่นที่ตั้งไว้", aigen.image_model() in call["url"], True)
        check("ส่งคีย์ทาง header ไม่ใช่ใน URL",
              call["headers"]["x-goog-api-key"] == "AIza-ทดสอบ"
              and "AIza" not in call["url"], True)
        check("ขอผลเป็นรูป",
              call["body"]["generationConfig"]["responseModalities"], ["IMAGE"])
        check("ส่งคำสั่งไปใน contents",
              call["body"]["contents"][0]["parts"][0]["text"], "x")

        print("\n3. ขอหลายใบ = ยิงหลายครั้ง")
        stub = Stub(Resp(200, ok_payload()), Resp(200, ok_payload()), Resp(200, ok_payload()))
        check("ขอ 3 ได้ 3", len(aigen.generate_images("x", 3, session=stub)), 3)
        check("ยิงไป 3 ครั้ง", len(stub.calls), 3)
        check("ขอเกินเพดาน ถูกตัดลง",
              len(aigen.generate_images("x", 99, session=Stub())), aigen.MAX_BATCH)
        check("ขอ 0 ยังได้อย่างน้อยหนึ่ง",
              len(aigen.generate_images("x", 0, session=Stub())), 1)

        print("\n4. ยิงพังกลางทาง — เก็บของที่ได้แล้วไว้ ไม่ทิ้งเงินทิ้ง")
        stub = Stub(Resp(200, ok_payload()), Resp(429, {"error": {"message": "quota"}}))
        check("ใบแรกผ่าน ใบสองพัง → ได้หนึ่งใบ",
              len(aigen.generate_images("x", 2, session=stub)), 1)

        print("\n5. ข้อความ error ที่ผู้ใช้อ่านแล้วรู้ว่าต้องทำอะไร")
        billing = Resp(429, {"error": {"message":
                             "Quota exceeded ... limit: 0, model: gemini-3.1-flash-image"}})
        try:
            aigen.generate_images("x", 1, session=Stub(billing))
            check("โควตา 0 ต้องโยน error", False, True)
        except aigen.GenError as exc:
            check("โควตา 0 → บอกให้ไปเปิด billing", "billing" in str(exc), True)
            check("และบอกว่ารุ่นไหน", "gemini-3.1-flash-image" in str(exc), True)

        for status, word in ((403, "สิทธิ"), (400, "รูปแบบ"), (500, "HTTP 500")):
            try:
                aigen.generate_images("x", 1, session=Stub(
                    Resp(status, {"error": {"message": "เกิดข้อผิดพลาด"}})))
                check(f"HTTP {status} ต้องโยน error", False, True)
            except aigen.GenError as exc:
                check(f"HTTP {status} → ข้อความอ่านรู้เรื่อง", word in str(exc), True)

        try:
            aigen.generate_images("x", 1, session=Stub(Resp(200, ok_payload(
                [{"text": "ขอโทษครับ สร้างไม่ได้"}]))))
            check("ตอบมาแต่ไม่มีรูป ต้องโยน error", False, True)
        except aigen.GenError as exc:
            check("ตอบมาแต่ไม่มีรูป → บอกให้แก้คำสั่ง", "ไม่มีรูป" in str(exc), True)

        try:
            aigen.generate_images("x", 1, session=Stub(Resp(200, {"candidates": [
                {"finishReason": "SAFETY", "content": {}}]})))
            check("โดนบล็อกต้องโยน error", False, True)
        except aigen.GenError as exc:
            check("โดนบล็อก → บอกเหตุผล", "SAFETY" in str(exc), True)

        os.environ.pop("GOOGLE_API_KEY")
        try:
            aigen.generate_images("x", 1, session=Stub())
            check("ไม่มีคีย์ต้องโยน error", False, True)
        except aigen.GenError as exc:
            check("ไม่มีคีย์ → บอกให้ไปตั้ง .env", "GOOGLE_API_KEY" in str(exc), True)
        os.environ["GOOGLE_API_KEY"] = "AIza-ทดสอบ"

        print("\n6. เซฟลงโฟลเดอร์ตัวละคร")
        with tempfile.TemporaryDirectory() as tmp:
            persona.PERSONA_DIR = Path(tmp)
            shots = [(b"a", ".png"), (b"b", ".jpg")]

            out = aigen.save_shots("น้องมายด์", "auto", shots)
            check("สร้างโฟลเดอร์ตัวละครให้เอง", out[0].parent.name, "น้องมายด์")
            check("ตั้งชื่อขึ้นต้น ai_ แยกจากรูปที่ถ่ายเอง",
                  [p.name for p in out], ["ai_01.png", "ai_02.jpg"])

            again = aigen.save_shots("น้องมายด์", "auto", [(b"c", ".png")])
            check("สั่งซ้ำ ไล่เลขต่อ ไม่ทับของเดิม", again[0].name, "ai_03.png")
            check("ไฟล์เดิมยังอยู่", len(list(out[0].parent.glob("ai_*"))), 3)

            incafe = aigen.save_shots("น้องมายด์", "cafe", [(b"d", ".png")])
            check("เลือกฉาก → ลงโฟลเดอร์ย่อยของฉาก", incafe[0].parent.name, "คาเฟ่")
            check("เลขเริ่มใหม่ในโฟลเดอร์ฉาก", incafe[0].name, "ai_01.png")
            check("เรนเดอร์ฉากนี้แล้วหยิบรูปที่เพิ่งสร้างได้",
                  [p.name for p in persona.shots("น้องมายด์", "cafe")], ["ai_01.png"])
            check("ไม่เลือกฉาก ยังเห็นของชั้นนอกเหมือนเดิม",
                  len(persona.shots("น้องมายด์")), 3)
    finally:
        if saved_key is None:
            os.environ.pop("GOOGLE_API_KEY", None)
        else:
            os.environ["GOOGLE_API_KEY"] = saved_key

    print(f"\nผ่าน {PASS} · ไม่ผ่าน {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
