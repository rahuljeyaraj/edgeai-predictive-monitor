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

# center: UNO Q
uno = s.box(390, 250, 260, 150, "Arduino UNO Q", ["STM32U585 side", "(real-time sensing)"], kind="brain", title_size=17)

# top-left: KX134
kx = s.box(30, 40, 260, 100, "KX134 Accelerometer", ["vibration, SPI"], kind="sense")
# top-right: INMP441
mic = s.box(750, 40, 260, 100, "INMP441 Microphone", ["sound, I2S / SAI1"], kind="sense")
# bottom-right: WS2812
led = s.box(750, 480, 260, 100, "WS2812 Status Ring", ["8-pixel, local light"], kind="tell")

# connectors
s.arrow(220, 140, 470, 250, color="#33475B")
pin_label(s, 250, 190, ["SCK D13 / MISO D12 / MOSI D11", "CS   D8  (PB4, GPIO)", "INT  D9  (PB8, buffer-full)"])

s.arrow(830, 140, 590, 250, color="#33475B")
pin_label(s, 820, 190, ["CLK PB10", "WS  PB9", "SD  PC1"], anchor="end")

s.arrow(750, 500, 640, 400, color="#33475B")
pin_label(s, 730, 460, ["DIN  PB0", "(TIM3 CH3)"], anchor="end")

s.text(30, 600, "Debug logging runs on a separate USART1 (D0/D1) straight to a host PC — not shown, fully decoupled from the sensor link.",
       size=12, style="italic", fill="#555555")

save(s, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "02-base-station-wiring.svg"),
     os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "02-base-station-wiring.png"))
print("done")
