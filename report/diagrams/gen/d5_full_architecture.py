import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import Svg, save

s = Svg(1300, 700)

# base station group
s.group_box(410, 30, 400, 300, "Base Station — Arduino UNO Q", kind="neutral")
stm = s.box(450, 65, 320, 95, "STM32U585", ["sampling, FFT,", "scalar stats"], kind="sense", title_size=15)
qrb = s.box(450, 210, 320, 105, "QRB2210 (Linux)", ["registry + AI pipeline", "+ dashboard server"], kind="brain", title_size=15)
s.arrow(610, 160, 610, 210, label="LPUART1", width=2)
s.arrow(610, 210, 610, 160, label=None, width=2)

# satellite nodes
sat = s.box(20, 250, 260, 110, "Satellite Nodes", ["ESP32S3 x N,", "own accel + mic"], kind="sense", title_size=15)
s.arrow(280, 285, 450, 245, label="Wi-Fi / MQTT", curve=(370, 230))

# outputs
dash = s.box(900, 30, 300, 85, "Live Dashboard", kind="tell", title_size=15)
phone = s.box(900, 140, 300, 85, "Phone Alert (Telegram)", kind="tell", title_size=15)
ring = s.box(900, 250, 300, 85, "Status Ring + LED Matrix", kind="tell", title_size=15)
rig = s.box(900, 385, 300, 95, "Motor-Driver Rig", ["listener process,", "per-motor stop + latch"], kind="act", title_size=15)

s.arrow(770, 240, 900, 75, label="WS: status + scores", curve=(830, 130))
s.arrow(770, 250, 900, 182, label="fault message")
s.arrow(770, 260, 900, 292, label="STATUS_LED")
s.arrow(770, 280, 900, 420, label="MQTT: STOP <motor>", color="#B23A2E", width=2.4, curve=(830, 350))

m1 = s.box(920, 530, 80, 60, "Motor 1", kind="act", title_size=13)
m2 = s.box(1020, 530, 80, 60, "Motor 2", kind="act", title_size=13)
m3 = s.box(1120, 530, 80, 60, "Motor 3", kind="act", title_size=13)
s.arrow(960, 480, 960, 530, color="#B23A2E", width=2)
s.arrow(1060, 480, 1060, 530, color="#B23A2E", width=2)
s.arrow(1160, 480, 1160, 530, color="#B23A2E", width=2)
s.text(1150, 500, "only the faulted motor stops", size=11.5, anchor="middle", fill="#B23A2E", style="italic")

s.text(30, 650, "Only the QRB2210 Linux side decides. Every other board senses, displays, or moves.", size=14, style="italic", fill="#333")

save(s, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "05-full-architecture.svg"),
     os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "05-full-architecture.png"))
print("done")
