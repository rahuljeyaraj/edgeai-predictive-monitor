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

     STATUS:  This is the REWRITE (article 2). Chapters 1 and 2 match the
              currently published article, synced from the live page on
              2026-09-04. Chapter 3 is NEW and not yet published.

              The two appendices (Bill of Materials, Schematics) have been
              REMOVED. Hackster's own "Things used in this project" section
              already carries the full BOM with quantities, and the
              Schematics resource section already carries the PDFs and the
              KiCad zip. The three schematic images now sit inline in 3.6,
              3.7 and 3.13, where they do work instead of repeating a
              section further down the same page. Buy links and the
              per-block grouping, the two things Hackster's BOM field
              cannot express, moved into 3.3 and the linked repo doc.

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

During his research he found an open-source project on GitHub. The [EdgeAI Predictive Monitor](https://github.com/rahuljeyaraj/edgeai-predictive-monitor), built on the Arduino UNO Q. He read the whole repository in one sitting.

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

# 3 Building It

# 3.1 Two Pages and a Repository

Ravi had heard enough. "Can we build it?"

Arjun had already found the two pages he needed. "The whole thing is one GitHub repository," he said. "There is a [parts list](https://github.com/rahuljeyaraj/edgeai-predictive-monitor/blob/main/docs/BILL_OF_MATERIALS.md) that tells you exactly what to buy and where, and a [build guide](https://github.com/rahuljeyaraj/edgeai-predictive-monitor/blob/main/docs/BUILD_GUIDE.md) that goes from an empty bench to a machine being watched. Everything else in the repo is those two pages in more detail."

What follows is the same route, in order. Every step links to the file that carries the full version.

# 3.2 He Ran It on His Laptop First

Before spending a rupee, Arjun ran the whole system on his own laptop.

This is not a demo mode. The dashboard, the feature pipeline, the model, the commissioning flow, the thresholds and the classifier are the same code that runs on the board. Only the sensor is replaced, by a simulator that speaks the real wire protocol and replays real recorded vibration.

You need an MQTT broker on the machine first:

```
sudo apt-get install -y mosquitto mosquitto-clients
sudo systemctl enable --now mosquitto
```

Then:

```
cd base-station
./start_desktop_dashboard.sh
```

That builds a virtual environment, starts the application on port 8180 with its own isolated data directory so it can never touch a real device, starts a simulated node, and prints both URLs. About ten minutes, no hardware, nothing bought.

- `--nodes N` runs several simulated machines at once, each with its own control page
- `--auto-online` skips the click that brings the node online
- `--host 0.0.0.0` lets you open the dashboard from a phone to check the mobile layout

One thing you will not see is the base station's own machine. Its data arrives over a wired SPI link to a chip that does not exist on a laptop. Only the wireless nodes appear, and that is expected.

Full detail is in section 8 of the [build guide](https://github.com/rahuljeyaraj/edgeai-predictive-monitor/blob/main/docs/BUILD_GUIDE.md).

# 3.3 What to Buy

The system is bought in blocks, not as a kit.

- **One base station per shop.** The Arduino UNO Q, a KX134 accelerometer, an INMP441 microphone, a WS2812B ring, and the connectors and magnet that go with them. Around $100.
- **One satellite node per additional machine.** A XIAO ESP32-S3 and the same three sensing parts. Around $25.
- **The bench rig is optional.** An Arduino Uno, a CNC shield, three stepper drivers and three NEMA-17 motors. It is only needed to reproduce the measurements and to see the trip actually stop a motor.

So one machine is about $100, three machines about $150, and ten machines about $325. The sensing parts are deliberately identical across every node, so they are one line item bought in bulk rather than a different list per machine.

Every part, with quantities and a direct purchase link for each, is in the [bill of materials](https://github.com/rahuljeyaraj/edgeai-predictive-monitor/blob/main/docs/BILL_OF_MATERIALS.md). It also lists the software and the bench tools, all of which are free.

# 3.4 Printing the Case

[IMAGE: PHOTO NEEDED - the printed shell, mount kit and bezel laid out before assembly]

Thirteen printable parts live in [3d-models](https://github.com/rahuljeyaraj/edgeai-predictive-monitor/tree/main/3d-models), each supplied as both a ready-to-slice 3MF and a plain STL.

- For the base station, print `a1`, `a2` and `a3`
- For each satellite, print `b1`, `b2` and `b3`
- The `c` parts are the bench rig, and a deployment does not need them

Each pod is three sub-assemblies: a two-piece snap-fit shell, a wall mount kit of two plates and a leg, and a front bezel. The base station's bezel carries a lens window over the UNO Q's LED matrix. The satellite's does not, because a satellite has no matrix.

PLA+, 0.4mm nozzle, 0.2mm layers, 20% infill for the shells and 40% or more for the rig parts. No supports, because the plates are already oriented. There is an optional embossed wordmark for the shell face in [hardware/enclosure-logo](https://github.com/rahuljeyaraj/edgeai-predictive-monitor/tree/main/hardware/enclosure-logo), engraved rather than raised so a thin stroke cannot snap off.

# 3.5 Making the Harnesses

Every sensor connects through a JST-XH harness rather than soldered jumper wires, so a pod can be opened and a sensor swapped without touching an iron.

- Cut the 10-wire ribbon into lengths, keeping the same colour order on every harness you make
- Crimp a female pin onto each end and seat it in its housing
- Tug-test every crimp before it goes in. A crimp that pulls out in your fingers will pull out under vibration, which is the only environment this project ever lives in

Solder the pin headers onto the accelerometer and microphone breakouts now if they did not ship fitted.

# 3.6 Wiring the Base Station

[IMAGE: report/diagrams/02b-base-station-schematic-kicad.png]
*Base station wiring. Arduino UNO Q with the SPI accelerometer, the I2S microphone and the WS2812B status ring.*

Three peripherals hang off the real-time half of the board. Nothing hangs off the Linux half.

- Accelerometer SPI clock, data in and data out: D13, D12, D11
- Accelerometer chip select: D8
- Accelerometer buffer-full interrupt: D9
- Microphone clock, frame sync and data: SCL, D10, A4
- Status ring data in: D3

Pins are given as the UNO Q's own silkscreen labels, which is what you actually push a wire into. The microphone's clock is the one signal without a D number. It comes out on the dedicated SCL pin, and the I2C peripheral is switched off to free it, since nothing here uses I2C.

The schematics are real, editable KiCad projects rather than drawings, generated from Python so that changing a connection is a change to a script. They are in [hardware/kicad](https://github.com/rahuljeyaraj/edgeai-predictive-monitor/tree/main/hardware/kicad), and also attached to this project as PDFs and as a zip.

# 3.7 Wiring a Satellite Node

[IMAGE: report/diagrams/03b-satellite-node-schematic-kicad.png]
*Satellite node wiring. XIAO ESP32-S3 with the same sensor set.*

- Accelerometer SPI clock, data in and data out: D8, D9, D10
- Accelerometer chip select: D3
- Accelerometer buffer-full interrupt: D2
- Microphone word select: D0
- Microphone bit clock: D1
- Microphone data in: D4
- Status ring data in: D5

The XIAO breaks out only eleven pins, so every assignment above exists to keep the fixed hardware SPI lines free for the accelerometer, the one peripheral that genuinely needs them.

There is nothing to set per unit. A node takes its identity from its own Wi-Fi hardware address, so there is no ID to type, no jumper to solder and no build flag to change between one node and the next.

# 3.8 Closing Up the Pod

[IMAGE: PHOTO NEEDED - an assembled pod, opened, showing the board and the three harnesses]

- Seat the board in the back half of the shell
- Fit the Fresnel lens into the bezel window over the LED matrix, on the base station only
- Pop the diffuser cap off a 9W LED bulb and fit it over the status ring. That cap is the status dome, and it is the only reason a bulb is on the parts list
- Plug in the three harnesses and close the shell
- Bolt the ring magnet into the mount foot with an M6 bolt through its bore, then attach the mount

Where the finished pod goes matters as much as which accelerometer is inside it. Mount it rigidly, as close to the bearing as the geometry allows. A soft or loose mount is a low-pass filter nobody asked for, and it strips out exactly the high-frequency content that early bearing faults live in.

# 3.9 Bringing Up the Base Station

Three scripts configure things that sit outside the application, and each is run once per board:

```
cd base-station
./provision-spi.sh
./provision-baud.sh
./provision-wifi.sh
```

The middle one matters more than it looks. The Linux side's serial speed has to match the firmware's, and a mismatch breaks the whole link silently, with no error printed anywhere.

Then open `base-station/sketch` in Arduino App Lab and flash it to the STM32. Finally:

```
cd base-station
./start_dashboard.sh
```

That builds the Linux application, pushes it to the board, waits for its container and prints the board's own network address. Use that address, not a localhost one. A real shop floor has no cable to the board and no port forwarding, and testing through one hides problems you will meet later anyway.

Open it, and the base station's own machine is already there. Nothing has been trained yet, but the sensing half of the loop is live and watchable: vibration and sound spectra, live traces, and a status ring that goes solid the moment real data starts arriving.

# 3.10 Bringing Up a Satellite Node

```
cd satellite
pio run
pio run -t upload
pio device monitor
```

No Wi-Fi credential is compiled in. A bench board being reflashed twenty times a day can pass one as a build flag, and the node will seed its storage on first boot and skip the setup page, but a real build passes nothing and that shortcut does nothing.

If a node comes up but a sensor does not, there is a [bring-up guide](https://github.com/rahuljeyaraj/edgeai-predictive-monitor/blob/main/docs/SATELLITE_BRINGUP_GUIDE.md) that walks the board up one module at a time, telling you what to watch in the serial log at each stage.

# 3.11 Putting the Nodes on the Shop Network

[IMAGE: report/diagrams/09-onboarding.png]
*Onboarding a node. Power it up, join its own network from a phone, fill in three fields, and it appears on the dashboard.*

- **Power it up.** A node with nothing saved raises its own Wi-Fi network, named from its own hardware address, so ten unconfigured nodes on a bench are ten distinguishable networks rather than one collision
- **Join it from any phone.** No app. The setup page opens by itself, using the same mechanism that airport Wi-Fi uses to push you to its login page
- **Fill in three fields.** The shop's network name, its password, and the address of the base station, which comes pre-filled. It is a field rather than a fixed value because some networks block name lookup, and when they do a technician needs to type an address rather than reflash a board
- **It tests before it saves.** Submitting does not blindly write. The node tries the credentials and only stores them on success, so a typo cannot strand a device on a machine you now need a ladder to reach
- **It appears.** No pairing, no ID to type. The asset shows up the moment its first reading lands

The base station onboards itself the same way, through its own hotspot and its Network tab.

# 3.12 Teaching the First Machine Its Normal

A node that is wired, flashed and online is still not monitoring anything. Every machine is taught its own normal once, from the dashboard, in four to six minutes. Press **Set up** on the asset and work through the six steps shown back in 2.7.

- **Name and class.** Both required. The name is what an alert prints at two in the morning, and the class is what recordings are grouped by
- **Off.** Switch the machine off and measure. This gives the sensor's own noise floor
- **Running conditions.** Switch it on and record it under each way it normally runs. At least one, as many as it has
- **Train.** The model, its statistics and its two thresholds are fitted on the board
- **Trip output.** Optional, and only if a machine is wired to something that can stop it. Press Test and watch the machine actually stop, rather than picking from a dropdown and hoping
- **Done.** The asset goes live

Step two is the one instruction no computer can check. Nothing in the software can confirm the machine is really off, so the screen says so plainly. A baseline recorded while the machine is running teaches the system that its own vibration is silence, and nothing works properly again until it is re-measured.

# 3.13 The Bench Rig That Proves the Trip

[IMAGE: report/diagrams/06-motor-driver-rig-schematic-kicad.png]
*Motor-driver rig wiring. Arduino Uno and a CNC Shield V3, one driver per stepper axis. Validation only.*

None of this is needed to monitor a machine. It exists so that the fault detection and the trip can be proved on a bench rather than asserted.

- Set each stepper driver's current limit with a multimeter before applying power. Too little skips steps, too much cooks the driver
- Flash the Uno with PlatformIO, then start the rig host, which serves a control page and receives trips
- Claim a motor at step five of that machine's setup. The control page then shows it with a PROTECTED badge naming the asset, and if the trip ever fires the card turns red and locks until a human presses Reset and re-arm

[IMAGE: hackster/assets/IMG20260901093909.jpg]
*Worn bearings on the left, new ones on the right. Swapping one in is how a real bearing fault gets recorded rather than simulated.*

Faults are induced physically. A flywheel with a bolt circle takes M6 bolts, and moving or removing one produces a repeatable imbalance. Worn bearings go in to produce bearing wear. That is where the labelled recordings behind the fault naming came from.

# 3.14 Everything in One Place

- [The repository](https://github.com/rahuljeyaraj/edgeai-predictive-monitor), MIT licensed
- [Bill of materials](https://github.com/rahuljeyaraj/edgeai-predictive-monitor/blob/main/docs/BILL_OF_MATERIALS.md), what to buy and where, including the free software and the bench tools
- [Build guide](https://github.com/rahuljeyaraj/edgeai-predictive-monitor/blob/main/docs/BUILD_GUIDE.md), the long version of this chapter, including the paths that need no hardware
- [3D models](https://github.com/rahuljeyaraj/edgeai-predictive-monitor/tree/main/3d-models), thirteen parts as 3MF and STL, also attached to this project
- [KiCad schematics](https://github.com/rahuljeyaraj/edgeai-predictive-monitor/tree/main/hardware/kicad), editable projects, also attached to this project as PDFs
- [The full report](https://github.com/rahuljeyaraj/edgeai-predictive-monitor/blob/main/report/REPORT.md), which is where every one of these decisions is argued rather than just stated

"So we could have the compressor on it by the weekend," Ravi said.

Arjun was already reading the parts list out loud.
