import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import Svg, save

s = Svg(1040, 560)

# legend
s.text(30, 30, "Watches / Notices", size=12, fill="#33475B")
s.parts.append('<rect x="140" y="18" width="14" height="14" fill="#EEF2F7" stroke="#33475B"/>')
s.text(170, 30, "Decides", size=12, fill="#1F5FA8")
s.parts.append('<rect x="240" y="18" width="14" height="14" fill="#DCEAFB" stroke="#1F5FA8"/>')
s.text(260, 30, "", size=12)
s.text(330, 30, "Acts", size=12, fill="#B23A2E")
s.parts.append('<rect x="370" y="18" width="14" height="14" fill="#FBE3E1" stroke="#B23A2E"/>')
s.text(400, 30, "", size=12)
s.text(460, 30, "Tells a human", size=12, fill="#2E7D46")
s.parts.append('<rect x="580" y="18" width="14" height="14" fill="#E8F5EA" stroke="#2E7D46"/>')

# left column: sensing sources
pod = s.box(30, 90, 210, 80, "Sensor Pod", ["accelerometer + microphone", "on the base station"], kind="sense")
sat = s.box(30, 210, 210, 90, "Satellite Nodes", ["accelerometer + microphone", "one per extra machine", "Wi-Fi"], kind="sense")

# center: base station brain
brain = s.box(340, 130, 260, 180, "Base Station", ["registry + AI pipeline", "(commissioning, scoring,", "gate, trip logic)"], kind="brain", title_size=17)

# right column: outputs
dash = s.box(720, 60, 260, 70, "Live Dashboard", kind="tell")
phone = s.box(720, 150, 260, 70, "Phone Alert (Telegram)", kind="tell")
led = s.box(720, 240, 260, 70, "Status Ring + LED Matrix", kind="tell")
motor = s.box(680, 370, 340, 90, "Motor Power", ["cut on confirmed fault", "stays off until cleared"], kind="act", title_size=16)

# arrows in
s.arrow(240, 130, 340, 190, label="sensor frames")
s.arrow(240, 255, 340, 250, label="Wi-Fi / MQTT")

# arrows out
s.arrow(600, 180, 720, 95, label="status + scores")
s.arrow(600, 210, 720, 185)
s.arrow(600, 240, 720, 275)
s.arrow(600, 280, 680, 400, label="STOP command", color="#B23A2E", width=2.4)

# caption strip: the point of the whole picture
s.parts.append('<line x1="30" y1="480" x2="1010" y2="480" stroke="#cccccc" stroke-width="1"/>')
s.text(30, 510, "Sensing -> deciding -> acting, in one loop, with no human required at the moment it matters.",
       size=14, style="italic", fill="#333333")
s.text(30, 535, "The STOP command is the one arrow that makes this Physical AI rather than a dashboard.",
       size=12.5, fill="#B23A2E")

save(s, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "01-system-at-a-glance.svg"),
     os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "01-system-at-a-glance.png"))
print("done")
