import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import Svg, save

s = Svg(1040, 640)

def pin_label(s, x, y, lines, anchor="start"):
    w = max(6.4 * len(l) for l in lines)
    if anchor == "end":
        rx = x - w
    elif anchor == "middle":
        rx = x - w / 2
    else:
        rx = x
    s.parts.append(f'<rect x="{rx-4}" y="{y-13}" width="{w+8}" height="{len(lines)*16+6}" fill="#FFFFFF" opacity="0.94" stroke="#dddddd" stroke-width="0.5"/>')
    for i, l in enumerate(lines):
        s.text(x, y + i * 16, l, size=11.5, anchor=anchor, family="DejaVu Sans Mono, monospace", fill="#222")

# center: XIAO ESP32S3
node = s.box(390, 250, 260, 150, "XIAO ESP32S3", ["satellite node brain", "Wi-Fi built in"], kind="sense", title_size=17)

# top-left: KX134
kx = s.box(30, 40, 260, 100, "KX134 Accelerometer", ["vibration, SPI"], kind="sense")
# top-right: INMP441
mic = s.box(750, 40, 260, 100, "INMP441 Microphone", ["sound, I2S"], kind="sense")
# bottom-right: WS2812
led = s.box(750, 480, 260, 100, "WS2812 Status Ring", ["8-pixel, local light"], kind="tell")
# bottom-left: WiFi/MQTT out
mqtt = s.box(30, 480, 260, 100, "Base Station (MQTT)", ["over Wi-Fi", "no wire"], kind="brain")

# connectors
s.arrow(220, 140, 470, 250, color="#33475B")
pin_label(s, 250, 190, ["SCK D8 / MISO D9 / MOSI D10", "CS   D3  (software GPIO)", "INT1 D2  (buffer-full)"])

s.arrow(830, 140, 590, 250, color="#33475B")
pin_label(s, 820, 190, ["WS/LRCLK D0", "BCLK     D1", "SD       D4"], anchor="end")

s.arrow(750, 500, 640, 400, color="#33475B")
pin_label(s, 730, 460, ["DIN  D5"], anchor="end")

s.arrow(290, 480, 460, 400, color="#33475B", dashed=True)
pin_label(s, 300, 430, ["telemetry + commands", "(wireless, not a pin)"])

s.text(30, 610, "Only 11 GPIOs are broken out on this board; every pin above was chosen to avoid its fixed hardware SPI lines.",
       size=12, style="italic", fill="#555555")

save(s, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "03-satellite-node-wiring.svg"),
     os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "03-satellite-node-wiring.png"))
print("done")
