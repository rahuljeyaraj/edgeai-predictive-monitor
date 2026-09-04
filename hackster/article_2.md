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

     STATUS:  This is the REWRITE (article 2). Chapters 1 to 3 match the
              currently published article, synced from the live page on
              2026-09-04, except for three small wordings in 3.2 and 3.3 that
              are edited here and not yet edited on the page. The video embed
              in 2.1 is NEW and not yet published.

              Chapters 4 and 5 are NEW and not yet published, as is the block
              quote at the end of 2.9 stating that the microphone is muted in
              both models.

              The two appendices (Bill of Materials, Schematics) have been
              REMOVED. Hackster's own "Things used in this project" section
              already carries the full BOM with quantities, and the
              Schematics and Custom-parts sections already carry the PDFs,
              the KiCad zip and the 3D models. Chapter 3 does not repeat
              any of it: the written instructions live in the repo's
              docs/BILL_OF_MATERIALS.md and docs/BUILD_GUIDE.md, and this
              chapter carries only what those cannot, which is photographs
              of the build.

     IMAGES:  Section 2.9 carries a screenshot of the Edge Impulse model page.
              Save it into hackster/assets/ as edge-impulse-model.png before
              uploading, and replace PUBLIC_EI_URL in that section with the
              project's public Studio URL.

              Chapter 3 carries exactly two photographs, one per node type,
              each a five-view composite of the same build (exploded, wired,
              closed, front, top). Save them into hackster/assets/ as
              base-station-build.jpg and satellite-build.jpg before
              uploading.

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

# 2.3 Every Machine Gets a Sensor Node

[IMAGE: Sensor node on a machine housing, held by its magnet.]
*Every Machine Gets a Sensor Node (AI generated)*

"A node is an accelerometer and a microphone in a small printed case, mounted on the machine housing with a strong magnet," he began. "The accelerometer feels vibration up to 6 kHz. The microphone hears sound up to 24 kHz. There are two types of node, a base station node and satellite nodes."

# 2.4 One Arduino UNO Q Runs the Whole Shop

"What is a base station?" Ravi asked.

"The base station is the central node that takes all the decisions," Arjun said. "It is built around the powerful Arduino UNO Q board, which carries two processors. The microcontroller listens to the asset it is attached to through its sensors. The Linux processor receives that sensor data and runs the AI models for every node to detect faults. It also serves the dashboard and sends the alerts. Doing this on any other board means buying two boards and wiring them together."

# 2.5 Machines Further Away Get a Satellite Node

[IMAGE: base station and satellite internal wiring, side by side]
*Internal wiring of Base station and satellite nodes*

"A satellite, on the other hand, is that same pair of sensors connected to a XIAO ESP32-S3," he went on. "It watches its own asset and sends the readings to the base station over Wi-Fi. The base station costs around $100. Every satellite node after that costs about $25, so the system scales cheaply."

# 2.6 The Network Is Whatever the Shop Already Has

[IMAGE: Wi-Fi onboarding pages, base station and satellite side by side]
*The Wi-Fi onboarding page of base station (left) and satellite (right)*

"I guess we would need to set up a Wi-Fi network on the floor," Ravi said.

"No," Arjun corrected. "If there is Wi-Fi on the floor, everything joins it, base station included. If there is none, the base station becomes the Wi-Fi access point itself, and the satellite nodes and the phone or laptop running the dashboard connect straight to it."

# 2.7 Each Machine Is Taught Its Own Normal

[IMAGE: report/diagrams/15f-setup-steps.png]
*Commissioning steps*

Ravi liked the flexibility of the system. He needed to know more. "How do we set up the sensor nodes with our machines?"

"Every new asset is commissioned once from the dashboard, and it takes only a few minutes," Arjun said patiently. "The node records the asset while it is idle and again while it runs under each of its normal operating conditions, and a model is trained on the base station from those recordings. Nothing is downloaded and no factory average is used. Different assets such as Pump 1 and Pump 2 end up with their own models, each judged against itself."

# 2.8 Fault Detection Is Drift Away From That Normal

[IMAGE: report/diagrams/04-feature-pipeline.png]
*From raw vibration and sound to fault detection*

"Each node reduces its raw signal to 536 numbers, five times a second," he continued. "That is 128 frequency bins and 6 summary values for each of four channels, the three vibration axes and the sound. During commissioning the model on the UNO Q learned to rebuild the healthy version of those numbers. Every reading after that, it rebuilds what it expects and compares it with what actually arrived. The bigger the difference, the further the machine has moved from healthy. Past a threshold set from that machine's own data, it reports a fault."

Ravi struggled at first, but he understood the gist of it.

# 2.9 Fault Identification Names the Fault

[IMAGE: report/diagrams/11-edge-impulse-flow.png]
*Fault identification steps*

"The same 536 numbers feed a second model that names the type of fault, such as bearing wear, imbalance or a loose mount," Arjun said. "This one is trained per asset class instead of per machine, so a single model covers every pump in the shop. It needs recordings labelled with each fault, which the dashboard collects and uploads to Edge Impulse in a few clicks. That upload is the only step in the whole system that needs an internet connection."

"So we need to induce a fault and record that data, and later, when a similar fault happens, the system will alert us with the type of fault?"

"Exactly."

[IMAGE: edge-impulse-model.png - Edge Impulse Studio model page for the pump fault classifier]
*The trained fault identification model in Edge Impulse, built from recordings the dashboard uploaded: four fault classes, their confusion matrix, and the cost of one inference*

The [Edge Impulse project](https://studio.edgeimpulse.com/studio/1092356) is public, so the impulse, the data and the trained model can all be inspected.

> The microphone is currently muted in both models. On this rig it is not acoustically isolated: sound from a machine standing next to it is picked up as if it belonged to this one, which produces false fault flags and wrong fault names. Until the mounting is fixed, the sound channel is zeroed out before the feature vector reaches either model, and every decision shown here is made on vibration alone. With the microphone active and nothing else running nearby, both models do well on the combined vibration and sound signal, but that mode is not usable on a floor where several machines run at once. The dashboard still shows the live sound spectrum, and the frame format is unchanged.

# 2.10 Dashboard in Any Browser

[IMAGE: report/diagrams/08-dashboard-anatomy.png]
*Dashboard depiction*

"So how will we monitor the health of the assets? Do we need to put it up on a monitor?" Ravi asked.

"Yes, the system has a dashboard," Arjun said. "It is served from the UNO Q itself, so any phone or laptop on the shop network can open it and there is no app to install. It lists every machine with its status, and the status tiles at the top double as filters. Open a machine and you see how far it has drifted, plotted live against its own warning and fault lines, along with the fault name, the live vibration and sound spectra, and the last half hour of history. If a machine has been stopped, a banner says so above every page."

# 2.11 Status Light on Every Node

[IMAGE: Row of nodes on machines, one showing red]
*One glance down the row tells you which machine needs attention (AI generated)*

[IMAGE: report/diagrams/18-status-light.gif]

Ravi raised his concern. "But we do not have a person to spare to monitor the dashboard all day. All six of us are out on the floor, working alongside the machines."

"EPM has a solution for that too," Arjun said with a smile. "Each sensor node has an RGB dome on top of it. Green is healthy, amber is a warning, red is a fault. So you can read it from across the floor without opening anything."

# 2.12 Fleet Summary on the Base Station

[IMAGE: led_matrix_1.gif - UNO Q LED matrix scrolling the fleet summary]
*1 Tripped(TRP), 1 Faulty(FLT), 10 Offline(OFF), 1 Healthy(OK)*

"And when some nodes are out of sight, you can still read the status of every machine without opening the dashboard," he added, encouraged by the happiness on his father's face. "The UNO Q's own LED matrix scrolls a one line summary of the whole fleet, worst status first. One glance on the way past tells you whether anything is wrong."

# 2.13 Telegram Alert on a Phone

[IMAGE: Alerts page next to a phone showing a Telegram alert]
*The Alerts page, and what lands on a subscribed phone*

"Scan the QR code on the dashboard once and that phone is subscribed to Telegram notifications. There is no account to create and no bot name to remember. We can select what alerts we need, either warnings and faults or faults only, and which machines, either the whole shop or a named few. The message carries the machine's name and the fault name."

"So I can know if something went wrong while I am at home."

"Yes."

# 2.14 Physical AI, Not Just an Alert

[IMAGE: report/diagrams/07-trip-sequence.png]
*Fault detected and named Unbalanced, 10 seconds to press Hold; if not held it trips, and stays tripped until acknowledged*

"It also has the feature you were looking for," Arjun said. "When a fault is confirmed, the system stops the machine itself to prevent further damage, and it stays stopped until someone clears it. It announces itself first: a banner counts down for 10 seconds, names the machine, and offers a Hold button for the case where the machine has to keep running."

# 2.15 No Server, No Subscription

"The models run on the UNO Q and the dashboard is served from it. Nothing has to talk to a server, and there is nothing to pay for after the build," Arjun concluded.

"You found a gem," Ravi said with excitement.

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

# 4 Planned Improvements

Arjun forked the repository that same evening and opened an issue list for his father's build.

- Isolate the microphone mounting acoustically and turn the sound channel back on for both models. It is the one thing holding back a signal both models already know how to use.
- Add a relay per motor. Today's trip stops motion; a relay would remove power at the source as well. The trip message, the latch and the confirmation logic would not change.
- Add hysteresis to the fault threshold, so a score sitting exactly on the line stops making the countdown flap.
- Give each operating condition its own threshold. Training one machine across several conditions costs sensitivity, and the hard part is not the thresholds, it is knowing which condition the machine is in right now.
- Pre-train one shared healthy model per asset class, so commissioning the fortieth pump becomes calibration rather than training, and takes less time than the first one did.
- Record more labelled fault data per class. The fault identification model's ceiling is set by how much genuinely different fault data exists, and the recording workflow is now good enough that this is a matter of time rather than tooling.
- Trend severity, not just detect it. The anomaly score is already stored per machine, so the question after something is wrong is how fast it is getting worse.

# 5 Conclusion

The compressor gave Ravi weeks of warning. Months, maybe. Nobody could feel it, because a machine that is failing slowly feels exactly like it did yesterday, every day, until the morning it does not turn.

That is the gap this fills. Each machine is measured against what it felt like the day it was serviced, on the machine itself, by a sensor node attached to it. A bearing gets changed because a machine asked for it, not because a calendar came round. A light on the housing says which machine to fix, from across the floor. A phone alerts you if a fault happened in your absence. And the motor stops itself to avoid further damage.

Eleven machines was the number that ended every sales call. It is also the number that makes this worth building: a base station and ten satellites, roughly $350 of parts, no gateway, no subscription, no account, and nothing leaving the shop.

All the code, the firmware, the wiring, and the 3D models are at [github.com/rahuljeyaraj/edgeai-predictive-monitor](https://github.com/rahuljeyaraj/edgeai-predictive-monitor). Fork it, as Arjun did.
