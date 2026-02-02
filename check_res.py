import cv2
import config
from ingest.config_loader import ConfigLoader
from ingest.usb_camera_worker import USBCameraWorker

def main():
    loader = ConfigLoader()
    loader.load()
    
    print("\n--- 카메라 해상도 확인 결과 ---")
    # 1. 로컬 USB 카메라 확인
    for cam_name, cam_cfg in loader.get_local_usb_cameras().items():
        cap = cv2.VideoCapture(cam_cfg.get('device', 0))
        ret, frame = cap.read()
        if ret:
            h, w = frame.shape[:2]
            print(f"[LOCAL] {cam_name}: {w}x{h}")
        cap.release()

    # 2. ZMQ로 들어오는 프레임은 main2.py 실행 중 터미널에 print를 추가하여 확인해야 합니다.
    print("------------------------------\n")

if __name__ == "__main__":
    main()