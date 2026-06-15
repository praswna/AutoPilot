"""
앱 아이콘(icon.ico) 생성 스크립트.

실행:  python icon.py
결과:  스크립트와 같은 폴더에 초록색 원형 'CAP' 아이콘(icon.ico)이 생성됩니다.
       원하는 다른 .ico 파일이 있다면 그것을 icon.ico 로 두고 이 스크립트를 건너뛰어도 됩니다.

빌드:  pyinstaller auto_pilot.spec
       (auto_pilot.spec 이 icon='icon.ico' 를 참조하므로 빌드 전에 이 파일이 있어야 합니다.)
"""
import os

from PIL import Image, ImageDraw


def main() -> None:
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")

    img = Image.new("RGBA", (256, 256), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([10, 10, 246, 246], fill=(43, 187, 131))
    draw.text((65, 110), "CAP", fill="white")

    img.save(out_path, format="ICO", sizes=[(256, 256)])
    print(f"아이콘이 생성되었습니다: {out_path}")


if __name__ == "__main__":
    main()
