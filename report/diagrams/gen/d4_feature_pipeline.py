import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import Svg, save

s = Svg(1180, 460)

sensor = s.box(20, 150, 180, 110, "Sensor", ["accel (x/y/z) + mic"], kind="sense", title_size=15)
feat = s.box(240, 150, 220, 110, "Feature Vector", ["FFT spectrum +", "6 scalars (RMS, peak,", "crest, kurtosis...)"], kind="sense", title_size=15)
ae = s.box(500, 150, 220, 110, "Autoencoder", ["compress -> rebuild", "(trained on healthy", "data only)"], kind="brain", title_size=15)
score = s.box(760, 150, 200, 110, "Anomaly Score", ["input vs.", "reconstruction gap"], kind="brain", title_size=15)

healthy = s.box(1000, 30, 160, 65, "Healthy", kind="tell", title_size=14)
warning = s.box(1000, 197, 160, 65, "Warning", kind="warn", title_size=14)
fault = s.box(1000, 365, 160, 65, "Fault", kind="act", title_size=14)

s.arrow(200, 205, 240, 205)
s.arrow(460, 205, 500, 205)
s.arrow(720, 205, 760, 205)
s.arrow(960, 190, 1000, 65, label=None)
s.arrow(960, 205, 1000, 225, label=None)
s.arrow(960, 220, 1000, 390, label=None)

s.text(1085, 145, "thresholds set", size=11.5, anchor="middle", fill="#555")
s.text(1085, 160, "from commissioning spread", size=11.5, anchor="middle", fill="#555")

s.text(590, 300, "Trained once per machine, on that machine's own healthy data.", size=13, style="italic", fill="#333")
s.text(590, 325, "A network that's only ever seen normal gets worse at rebuilding anything else.", size=13, style="italic", fill="#333")

save(s, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "04-feature-pipeline.svg"),
     os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "04-feature-pipeline.png"))
print("done")
