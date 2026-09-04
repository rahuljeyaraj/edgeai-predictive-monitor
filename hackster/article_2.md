<!-- ==========================================================================
     NOT PART OF THE ARTICLE. Transcription legend for Hackster's editor.

       line starting with #     ->  the H button (only one heading level exists)
       **bold**                 ->  the B button
       *italic*                 ->  the i button
       `backticks`              ->  the # button   (inline code)
       ``` fenced block         ->  the </> button (block code)
       line starting with >     ->  the quote button
       line starting with -     ->  the bullet button (one level, never nested)
       [text](url)              ->  the link button
       [IMAGE: ...]             ->  the image embed, caption pasted + italicised
       [VIDEO: ...]             ->  the video embed, caption pasted + italicised
       [COVER IMAGE: ...]       ->  the cover upload, AND embedded inline as well

     Hard limits, already respected below:
       - One heading level only. The number carries the hierarchy.
       - Bullets cannot nest. Not one nested bullet in this file.

     TITLE:   EdgeAI Predictive Monitor - the pulse of your machinery
     TAGLINE: Small workshops run machines until they fail. EPM predicts and
              names faults, stops the machine to protect it - no cloud, no
              subscription.

     STATUS:  This is the REWRITE (article 2). It has diverged from the
              currently published page and REPLACES it wholesale rather than
              patching it. Differences from the live page, all unpublished:

              - Chapter 2 was restructured. The old 2.3, 2.4 and 2.5 (node,
                base station, satellite) are merged into one 2.3, so Ch 2 now
                runs 2.1 to 2.13 instead of 2.1 to 2.15. Every section number
                after 2.3 has shifted down by two.
              - The Ravi/Arjun dialogue now stops after 2.7. Sections 2.8 to
                2.12 are straight narration. Dialogue returns once at 3.1 as a
                deliberate bookend to the sales calls in 1.3.
              - 2.12 (Physical AI) is substantially expanded. It is the one
                capability none of the commercial products in 1.3 have, and it
                now says so explicitly, including the honest motion-not-power
                limit.
              - The muted-microphone block quote in 2.7 is cut to two
                sentences and now points forward to chapters 4 and 5.
              - Chapter 4 (Measured Results) is NEW. Every figure in it is
                taken from report/REPORT.md ch 12, which recorded them off the
                physical rig. Do not add a number to this chapter that is not
                already measured and written down there.
              - Planned Improvements is now chapter 5 and Conclusion is
                chapter 6.

              - 4.4 now carries the classifier's real numbers, read off the
                Edge Impulse Studio model page on 2026-09-04: 100.0% accuracy
                and 0.00 loss on the validation set, F1 1.00 on all four
                classes, 1 ms per inference, 1.9 KB peak RAM, 50 KB flash,
                int8 via the EON Compiler. The Edge Impulse screenshot MOVED
                from 2.7 to 4.4 to sit with them. The 59.82% and 69.64% in
                report/REPORT.md I.3 are from the STALE Kaggle-replay phase
                and must never be used.
              - The paragraph after those numbers states plainly why a 100%
                score is not four unseen faults on a real machine. DO NOT cut
                it while the 100% stands. A perfect number with no caveat
                reads to a judge as undisclosed leakage, which is worth less
                than no number at all.

              OUTSTANDING: the figures above are Edge Impulse's VALIDATION
              set, which is split out of the training data. If the Studio
              project's "Model testing" tab has a result on the held-out test
              set, that number is the stronger one to publish and should
              replace the validation figure here.

              The video embed in 2.1 is NEW and not yet published.

              The two appendices (Bill of Materials, Schematics) have been
              REMOVED. Hackster's own "Things used in this project" section
              already carries the full BOM with quantities, and the
              Schematics and Custom-parts sections already carry the PDFs,
              the KiCad zip and the 3D models. Chapter 3 does not repeat
              any of it: the written instructions live in the repo's
              docs/BILL_OF_MATERIALS.md and docs/BUILD_GUIDE.md, and this
              chapter carries only what those cannot, which is photographs
              of the build.

     VIDEO:   Section 2.1 carries the only video, the full demo walkthrough on
              YouTube (8h05_KkEtwQ, public, 12:53). It is the hook: Arjun
              watches it, then calls his father over. Shot and uploaded, NOT
              yet embedded on the published page. Paste the URL into the embed
              itself, not just the caption. An embed with no URL is worth
              nothing at judging time.

     NOTE:    Image lines carry the caption exactly as published. Where the
              published image is a photo or an AI-generated render rather than
              a repo diagram, the [IMAGE: ...] line describes it instead of
              naming a file.
     ========================================================================== -->

# 1 Predictive Maintenance, Built for a Different Scale

# 1.1 The Limits of Manual Inspection

"Twenty-two years. Eleven machines, six people, and I'm out on the floor with them most days." Ravi wiped his hands on a rag. "I know the pulse of every machine in here. Hand on the housing, that's all it takes."

He looked out over the floor.

"That's what I used to say. Then the compressor seized." A pause. "Anything that breaks overnight, I'll catch by the end of the shift. But that bearing had been going for weeks before it stopped. Months, maybe. Every day it felt the same as the day before." He shrugged. "You can't feel the slow changes."

"So you do what everyone does. Change bearings on a calendar, whether they need it or not. Re-grease, tighten everything down, put it back." He held up his hands. "Most of them come out fine. Good money in the bin, on machines that were never going to fail. And after the compressor we shortened the service cycle. Now we throw the money away faster."

# 1.2 What a small Workshop Actually Needs

"What I need isn't complicated." He counted on his fingers. "Something that remembers what this machine felt like the day it was serviced. Tells me when that's changed. Shuts it down if I'm not standing there." A beat. "If it can tell me what's actually wrong, that's a bonus. I'd settle for the first three."

# 1.3 Sized for the Enterprise Floor

"Every solution out there is built for a factory I don't have," Ravi said. "You can't buy one sensor for the one machine that matters. [Fluke](https://www.fluke.com/en/product/condition-monitoring/vibration/3563) quoted me sixteen sensors, two gateways and sixteen software subscriptions." He shook his head. "[Tractian](https://tractian.com/en/solutions/condition-monitoring/vibration-sensor), [Augury](https://www.augury.com/machine-health-solutions/se/), same story, except they won't print a price at all. You fill in a form and wait for the call."

"And none of them decide anything at the machine. [Murata](https://video.murata.com/en-global/detail/video/6245856062001), [KCF](https://kcftech.com/solutions/smartsensing-suite/wireless-vibration-sensor/), all of them. The sensor talks to a gateway, the gateway talks to a computer in another country, and the answer comes back to me. There's no Wi-Fi past my office door. The compressor sits outside under a tin sheet." He opened his hand. "If the line drops, I'd have eleven glorified paperweights attached to my machines. Paperweights I'd still be paying a subscription on."

"Then somebody has to watch it in a screen all day looking at machines' vibration profiles. I neither have the manpower nor facility for that."

"Every call opened the same way. How many assets do you run." He almost laughed. "Eleven. That's where the call ends."

He shrugged. "We're too small to be a customer."

# 2 Predictive Maintenance Using the Arduino UNO Q

# 2.1 An Open-Source Build Instead of a Quote

Arjun, Ravi's son, was in his final year of engineering and had heard the compressor story at one family dinner too many. Over the holidays he decided to build his father a sensor himself.

During his research he found an open-source project on GitHub. The [EdgeAI Predictive Monitor](https://github.com/rahuljeyaraj/edgeai-predictive-monitor) (EPM), built on the Arduino UNO Q. He read the whole repository in one sitting, and the demo video made the rest of it clear.

[VIDEO: https://www.youtube.com/watch?v=8h05_KkEtwQ]
*EdgeAI Predictive Monitor Demo Video.*

Three machines on a bench, watched live. Faults induced on camera one after another, an unbalanced rotor, a bad bearing, a loose mount, each one named within seconds, and each one ending with the system stopping that motor itself. The rest of it walks through commissioning a machine from scratch, training the models, and adding ten more nodes to the same base station.

He watched it a second time.

"Dad, come look at this," he called out.

# 2.2 The Whole System in One Picture

[IMAGE: 17-system-overview-alt.png]
*System overview*

Arjun explained the system to his father, part by part.

# 2.3 One Base Station, Ten Satellites

[IMAGE: Sensor node on a machine housing, held by its magnet.]
*Every machine gets a sensor node (AI generated)*

"A node is an accelerometer and a microphone in a small printed case, mounted on the machine housing with a strong magnet," he began. "The accelerometer feels vibration up to 6 kHz. The microphone hears sound up to 24 kHz. Every machine gets one. There are two kinds."

"The base station is the one that decides. It is built around the Arduino UNO Q, which carries two processors on one board. The microcontroller listens to the machine the base station is bolted to. The Linux processor takes that data, and the data from every other node, runs the AI models for all of them, serves the dashboard and sends the alerts. On any other board that is two boards and a wire between them."

[IMAGE: base station and satellite internal wiring, side by side]
*Internal wiring of the base station and satellite nodes*

"A satellite is the same pair of sensors on a XIAO ESP32-S3. It watches its own machine and sends readings to the base station over Wi-Fi. It makes no decisions and holds no model."

"The base station costs around $100. Every satellite after that is about $25, so the eleventh machine costs $25 to cover, not another licence."

# 2.4 The Network Is Whatever the Shop Already Has

[IMAGE: Wi-Fi onboarding pages, base station and satellite side by side]
*The Wi-Fi onboarding page of the base station (left) and a satellite (right)*

"I guess we would need to set up a Wi-Fi network on the floor," Ravi said.

"No," Arjun corrected. "If there is Wi-Fi on the floor, everything joins it, base station included. If there is none, the base station becomes the Wi-Fi access point itself, and the satellite nodes and the phone or laptop running the dashboard connect straight to it."

# 2.5 Each Machine Is Taught Its Own Normal

[IMAGE: report/diagrams/15f-setup-steps.png]
*Commissioning steps*

Ravi liked the flexibility of the system. He needed to know more. "How do we set up the sensor nodes with our machines?"

"Every new asset is commissioned once from the dashboard, and it takes only a few minutes," Arjun said. "The node records the asset while it is idle and again while it runs under each of its normal operating conditions, and a model is trained on the base station from those recordings. Nothing is downloaded and no factory average is used. Different assets such as Pump 1 and Pump 2 end up with their own models, each judged against itself."

# 2.6 Fault Detection Is Drift Away From That Normal

[IMAGE: report/diagrams/04-feature-pipeline.png]
*From raw vibration and sound to fault detection*

"Each node reduces its raw signal to 536 numbers, five times a second," he continued. "That is 128 frequency bins and 6 summary values for each of four channels, the three vibration axes and the sound. During commissioning the model on the UNO Q learned to rebuild the healthy version of those numbers. Every reading after that, it rebuilds what it expects and compares it with what actually arrived. The bigger the difference, the further the machine has moved from healthy. Past a threshold set from that machine's own data, it reports a fault."

Ravi struggled at first, but he understood the gist of it.

# 2.7 Fault Identification Names the Fault

[IMAGE: report/diagrams/11-edge-impulse-flow.png]
*Fault identification steps*

"The same 536 numbers feed a second model that names the type of fault, such as bearing wear, imbalance or a loose mount," Arjun said. "This one is trained per asset class instead of per machine, so a single model covers every pump in the shop. It needs recordings labelled with each fault, which the dashboard collects and uploads to Edge Impulse in a few clicks. That upload is the only step in the whole system that needs an internet connection."

"So we need to induce a fault and record that data, and later, when a similar fault happens, the system will alert us with the type of fault?"

"Exactly."

The [Edge Impulse project](https://studio.edgeimpulse.com/studio/1092356) is public, so the impulse, the data and the trained model can all be inspected. What it scores, and what one inference costs, is in chapter 4.

> The microphone is muted in both models for now. This rig is not acoustically isolated, so a machine running next to it is heard as if it belonged to this one, and the sound channel is therefore zeroed before the feature vector reaches either model. Every result in chapter 4 is vibration alone, and restoring sound is the first item in chapter 5.

# 2.8 Dashboard in Any Browser

[IMAGE: report/diagrams/08-dashboard-anatomy.png]
*Dashboard depiction*

The dashboard is served from the UNO Q itself, so any phone or laptop on the shop network can open it and there is no app to install. It lists every machine with its status, and the status tiles at the top double as filters. Open a machine and you see how far it has drifted, plotted live against its own warning and fault lines, along with the fault name, the live vibration and sound spectra, and the last half hour of history. If a machine has been stopped, a banner says so above every page.

# 2.9 Status Light on Every Node

[IMAGE: Row of nodes on machines, one showing red]
*One glance down the row tells you which machine needs attention (AI generated)*

[IMAGE: report/diagrams/18-status-light.gif]

A dashboard only helps somebody who is looking at one, and in a shop of six people nobody is spare to do that. So the answer had to be readable without opening anything.

Each sensor node carries an RGB dome on top. Green is healthy, amber is a warning, red is a fault. Walking the floor is the check.

# 2.10 Fleet Summary on the Base Station

[IMAGE: led_matrix_1.gif - UNO Q LED matrix scrolling the fleet summary]
*1 Tripped(TRP), 1 Faulty(FLT), 10 Offline(OFF), 1 Healthy(OK)*

Nodes out of sight are covered by the base station itself. The UNO Q's own LED matrix scrolls a one line summary of the whole fleet, worst status first, so one glance on the way past says whether anything anywhere is wrong.

# 2.11 Telegram Alert on a Phone

[IMAGE: Alerts page next to a phone showing a Telegram alert]
*The Alerts page, and what lands on a subscribed phone*

Nights and weekends are covered by phone. Scanning the QR code on the dashboard once subscribes that phone to Telegram notifications, with no account to create and no bot name to remember. Each phone chooses what it wants, warnings and faults or faults only, and which machines, the whole shop or a named few. The message carries the machine's name and the fault name.

# 2.12 Physical AI, Not Just an Alert

[IMAGE: report/diagrams/07-trip-sequence.png]
*Fault detected and named Unbalanced, 10 seconds to press Hold; if not held it trips, and stays tripped until acknowledged*

Everything above this point is still, in the end, a notification. This is the part that is not.

Every product Ravi called ends at the message. The sensor measures, a gateway forwards, a server decides, and a human is told. If the line is down, or it is 3 a.m., or the person holding the phone is forty minutes away, the machine keeps destroying itself for exactly as long as it takes someone to arrive.

EPM closes that loop on the board. When a fault is confirmed, the base station stops the machine itself.

- It announces first. A banner names the machine and counts down 10 seconds, with a Hold button for the case where the machine genuinely has to keep running.
- It latches. A tripped machine stays tripped and refuses further speed commands until somebody clears it from the dashboard, so a fault that comes and goes cannot quietly restart the motor.
- It does not trust the label. The naming model is deliberately kept out of the safety path. If the classifier picks the wrong fault, the machine still stops. Only the name on the banner is wrong.
- It decides where it measures. No gateway, no server, no internet. The decision and the action happen on the same board that felt the vibration.

The honest limit is that the trip stops motion, not power. It commands the machine to stop and confirms it did. Cutting the supply at the source needs a relay per motor, which is in chapter 5.

# 2.13 No Server, No Subscription

The models run on the UNO Q and the dashboard is served from it. Nothing has to talk to a server, and there is nothing to pay for after the build.

"So the whole thing is ours," Ravi said. "Once."

# 3 Build

# 3.1 Full Build Instructions

"So who do we call?" Ravi asked.

"Nobody. No quote, no form to fill in, no waiting for someone to ring back." Arjun turned the laptop around. "It's all on this one page. What to buy, where to buy it, and how to build it step by step."

- [Bill of materials](https://github.com/rahuljeyaraj/edgeai-predictive-monitor/blob/main/docs/BILL_OF_MATERIALS.md), every part with a quantity and a purchase link, plus the software and the bench tools
- [Build guide](https://github.com/rahuljeyaraj/edgeai-predictive-monitor/blob/main/docs/BUILD_GUIDE.md), from an empty bench to a commissioned machine, including two paths that need no hardware at all
- [3D models](https://github.com/rahuljeyaraj/edgeai-predictive-monitor/tree/main/3d-models), thirteen printable parts as 3MF and STL, also under Custom parts and enclosures on this page
- [KiCad schematics](https://github.com/rahuljeyaraj/edgeai-predictive-monitor/tree/main/hardware/kicad), editable projects for all three boards, also under Schematics on this page

# 3.2 Base Station

[IMAGE: base-station-build.jpg - base station laid out and assembled, five views]
*The base station: Arduino UNO Q, accelerometer, microphone and status ring, laid out and closed up.*

Every sensor connects through its own crimped harness, so the pod can be opened and a part swapped without a soldering iron. The accelerometer sits on its own plate against the shell wall, which is what couples it to the machine rather than to the board.

The white globe is the diffuser cap off a 9W LED bulb, and it is the status dome over the ring. The rectangular window on the top face holds a Fresnel lens over the UNO Q's own LED matrix, which is what makes the fleet summary readable from across the floor. The round hole on the bottom is the microphone port. The foot underneath takes the ring magnet, pressed in by hand.

# 3.3 Satellite Node

[IMAGE: satellite-build.jpg - satellite node laid out and assembled, five views]
*The satellite node: the same shell pattern and the same three sensors, scaled down for the XIAO ESP32-S3.*

The satellite is deliberately the same build. Same sensors, similar harnesses, same status dome, one USB-C cable for power. The only difference you can see is the front face, which has no lens window, because a satellite has no LED matrix to magnify. The black sticker on the side is the Wi-Fi antenna.

There is nothing to set per unit in software. A node takes its identity from its own Wi-Fi hardware address, so ten of these are ten distinct machines on the dashboard with no ID typed anywhere.

# 4 Measured Results

# 4.1 What Was Measured, and How

Every figure in this chapter came off the physical rig: a sensor node on a spinning motor, a trip that actually stopped that motor, a dashboard checked in a real browser against the live board. The microphone is muted, so all of it is vibration alone. The full record of how each was checked is in the [project report](https://github.com/rahuljeyaraj/edgeai-predictive-monitor/blob/main/report/REPORT.md).

# 4.2 Fault Detection

Numbers straight off the rig:

- After training on the healthy running machine, its live anomaly score sat at **0.046**, against a warning line of **0.144** and a fault line of **0.288**. That is real daylight between a machine reading as itself and the line that means trouble.
- Induced fault, a 2.4x overspeed: scored **1.851** against a fault threshold of **0.292**, and the motor **tripped in about 11 seconds**.
- Ramping back down returned the node cleanly to Idle rather than to Fault.

The running/stopped gate matters as much as the model, because a stopped machine must not read as a broken one:

- Full-spectrum average energy separates stopped from running by only **1.18x**, which is not enough to threshold safely.
- Excess energy over the machine's own measured baseline separates them by **2.09x**, and that is the metric shipped.

# 4.3 Why the Feature Vector Looks Like That

The 536 numbers were not a guess. An offline harness replayed real captures through the whole pipeline and swept it:

- **Per-axis beats fused.** Keeping the three vibration axes separate gave **+38.5 sigma** worst-case fault separation, against **+1.8 sigma** for a single combined tri-axial magnitude on the same captures.
- **The six summary statistics carry more than expected.** Adding them took healthy-versus-imbalance separation from roughly **3 sigma** to roughly **80 sigma**. A spectrum on its own was leaving a lot on the table.

# 4.4 Fault Identification

The naming model was trained on **541 captures taken from this rig**, across four classes: healthy, bearing, loose mount and unbalanced.

[IMAGE: edge-impulse-model.jpg - Edge Impulse Studio model page for the fault classifier]
*The trained fault identification model in Edge Impulse: four fault classes, their confusion matrix, and the cost of one inference*

- **100.0% accuracy** on the validation set, loss **0.00**. Every class scores an **F1 of 1.00**, the confusion matrix has nothing off the diagonal, and area under the ROC curve is **1.00**.
- **1 ms per inference**, **1.9 KB peak RAM**, **50 KB flash**, quantized to int8 and built with Edge Impulse's EON Compiler. Naming the fault costs almost nothing next to producing the 536 numbers it reads, which is what makes running a second model per frame affordable on the board.

A perfect score is a claim that deserves its caveat, so here it is. These four fault conditions are induced deliberately on a bench rig and they sit a long way apart in the feature space, which the data explorer shows as four clusters with open ground between them. Because each condition exists as one continuous recording rather than many independent short ones, a file-level train/test split was not available; a contiguous-tail split was used instead, with the last portion of each recording held out and never trained on. That is the closest leakage-free approximation available under the constraint, and it is not the same thing as four unseen faults on a real machine. It is also why the classifier is kept out of the safety path: if it ever names the wrong fault, the machine still stops.

The model, its data and its confusion matrix are public in the [Edge Impulse project](https://studio.edgeimpulse.com/studio/1092356).

# 4.5 The Costs, Measured

Three limits with numbers on them, because a result without its cost is only half a result:

- **Training one machine across several operating conditions costs sensitivity.** Adding a second condition widened the healthy spread **5.1x**. The same 2.4x overspeed that scored 1.851 and tripped under single-condition training never crossed the threshold at all under two conditions.
- **A score sitting exactly on the fault line makes the countdown flap.** In one session the countdown started and cancelled three times before the trip finally fired. The system was correct each time, since a fault has to persist to be believed, but it is unpleasant to watch and hysteresis fixes it.
- **Faults above roughly bin 24 look alike on this rig.** Above the motor's own mechanical signature there is nothing but sensor noise to tell classes apart. On a machine with genuine high-frequency fault content the sensor can see it, so this is a property of the bench rig rather than of the design.

# 5 Planned Improvements

Arjun forked the repository that same evening and opened an issue list for his father's build.

- Isolate the microphone mounting acoustically and turn the sound channel back on for both models. It is the one thing holding back a signal both models already know how to use.
- Add a relay per motor. Today's trip stops motion; a relay would remove power at the source as well. The trip message, the latch and the confirmation logic would not change.
- Add hysteresis to the fault threshold, so a score sitting exactly on the line stops making the countdown flap.
- Give each operating condition its own threshold. Training one machine across several conditions costs a measured 5.1x, and the hard part is not the thresholds, it is knowing which condition the machine is in right now.
- Pre-train one shared healthy model per asset class, so commissioning the fortieth pump becomes calibration rather than training, and takes less time than the first one did.
- Record more labelled fault data per class. The fault identification model's ceiling is set by how much genuinely different fault data exists, and the recording workflow is now good enough that this is a matter of time rather than tooling.
- Trend severity, not just detect it. The anomaly score is already stored per machine, so the question after something is wrong is how fast it is getting worse.

# 6 Conclusion

The compressor gave Ravi weeks of warning. Months, maybe. Nobody could feel it, because a machine that is failing slowly feels exactly like it did yesterday, every day, until the morning it does not turn.

That is the gap this fills. Each machine is measured against what it felt like the day it was serviced, on the machine itself, by a sensor node attached to it. A bearing gets changed because a machine asked for it, not because a calendar came round. A light on the housing says which machine to fix, from across the floor. A phone says so when nobody is on the floor at all. And the motor stops itself to avoid further damage.

Eleven machines was the number that ended every sales call. It is also the number that makes this worth building: a base station and ten satellites, roughly $350 of parts, no gateway, no subscription, no account, and nothing leaving the shop.

All the code, the firmware, the wiring, and the 3D models are at [github.com/rahuljeyaraj/edgeai-predictive-monitor](https://github.com/rahuljeyaraj/edgeai-predictive-monitor). Fork it, as Arjun did.
