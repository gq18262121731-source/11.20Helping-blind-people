"""Generate a local camera source for OCR and visual pipeline tests."""
import asyncio, os, time
import cv2, numpy as np, httpx

URL = os.getenv("QH_LOCAL_CAMERA_HTTP", "http://127.0.0.1:8765/api/local-camera/frame")

async def main(seconds=30):
    async with httpx.AsyncClient() as client:
        for seq in range(1, int(seconds * 5) + 1):
            image = np.full((480, 640, 3), (30, 40, 60), np.uint8)
            cv2.putText(image, "MORNING-STAR CAMERA", (110, 250), cv2.FONT_HERSHEY_SIMPLEX, 1, (80, 220, 255), 2)
            encoded = cv2.imencode(".jpg", image)[1].tobytes()
            response = await client.post(URL, params={"seq": seq}, content=encoded)
            response.raise_for_status()
            await asyncio.sleep(0.2)

if __name__ == "__main__": asyncio.run(main(float(os.getenv("QH_CAMERA_SECONDS", "30"))))
