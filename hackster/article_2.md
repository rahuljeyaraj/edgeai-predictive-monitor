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

     STATUS:  This is the REWRITE (article 2). Chapters 1 and 2 and both
              appendices below match the currently published article, synced
              from the live page on 2026-09-04. Later chapters still to come.

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

# Appendix A: Bill of Materials
**Base station components**

- [Arduino UNO Q (4GB, ABX00173)](https://robu.in/product/official-arduino-uno-q-4gb-single-board-computer-abx00173/) *(×1)*

- [KX134-1211 SPI accelerometer](https://robu.in/product/smartelex-triple-axis-accelerometer-breakout-kx134/) *(×1)*

- [INMP441 I2S MEMS microphone](https://robu.in/product/inmp441-mems-high-precision-omnidirectional-microphone-module-i2s/) *(×1)*

- [8 Bit WS2812B 5050 addressable RGB LED](https://robu.in/product/8bit-ws2812-5050-rgb-led-built-full-color-driving-lights-circular-development-board/) *(×1)*

- [9W LED bulb, for status dome diffuser](https://www.amazon.in/Crompton-Dyna-Round-Cool-Light/dp/B0B6FJNH97/ref=sr_1_7?crid=1WFL4GPO6P5LR&dib=eyJ2IjoiMSJ9.XyZtwgJUEsfIg3kZ7nFTImsLPe5r1mEJa_H20SUz3olKYCbFgZnkmbyhoA-Wla8CEQkV5t__DHhqnMopYsJL-56zZykWqQbgYFST9za5VwhseFFIIwoWvLKliusMA28RmbJe0Dy1pFeW7hyzVbY_tmK4HiCHaGcKcNTdNRIQsBpR1Pkqf18r6akvuweK5o3geswtYcHDecuRpcsAYa0WPkUe2RR2veMZwjC00RlY0zwl7nMjOlcNKf0uPb91fHCUvCpKs_Pn4DdT7N3zQZ6mRXZNQLF_SAy_OqcL-PW_6ls.a3O5yF9NfaExtQIvBHhLP1AZ89IPo-RTNP8CqGYTZdQ&dib_tag=se&keywords=bulb&qid=1788412570&sprefix=bul%2Caps%2C285&sr=8-7&th=1) *(×1)*

- [JST-XH 2.54 connector, straight, 8-pin, male and female ](https://makerbazar.in/products/male-female-connector-straight?variant=46140555493616) *(×2)*

- [JST-XH 2.54 connector, straight, 6-pin, male and female](https://makerbazar.in/products/male-female-connector-straight?variant=46137886703856) *(×1)*

- [JST-XH 2.54 connector, straight, 4-pin, male and female](https://makerbazar.in/products/male-female-connector-straight?variant=46134456320240) *(×1)*

- [JST-XH 2.54 connector, straight, 3-pin, male and female](https://makerbazar.in/products/male-female-connector-straight?variant=46134287663344) *(×3)*

- [JST-XH 2.54 connector, straight, 2-pin, male and female](https://makerbazar.in/products/male-female-connector-straight?variant=46134287630576) *(×1)*

- [Fresnel lens, to magnify the UNO Q's onboard LED matrix](https://www.amazon.in/oddpodTM-Fresnel-Flexible-Plastic-Magnifying/dp/B09MDH1LLH/ref=sr_1_1_sspa?crid=15NRC4QY9W8UQ&dib=eyJ2IjoiMSJ9.W6--pKYA_WKD0qSAiCZT9glSF_zmv35_dfK5xX253EV5jR1ZoGAlBQEJsIQSz7_2xbTHw-3cS-tj06nlukxe_n2u1fVmGhPvNDCu6DOzYzonENBbMeFeWE_KzRWtjJufpoChvXfRXhRSRnNHxdWgqWhqCTQgkszOXg1JWmJyMdL0LiP1N5hBc2p7At-_U0xdoRbF7yvdTOarWMMyfRzKiIUcHhABQ0EC6LFC_gQLHm1ymoryIIfwNPtTzu7GwN0DIAG-_Yrda5ghZFderH2tRcRgYbelGK41hEmud_x1c6o.DDgGXdvarAm-pnInA3autvQTVCgMVfv8MnOUKfqVrVQ&dib_tag=se&keywords=Fresnel+lens%2C+credit-card+size&qid=1788412959&sprefix=fresnel+lens%2C+credit-card+size%2Caps%2C302&sr=8-1-spons&aref=OBkEda8tdN&sp_csd=d2lkZ2V0TmFtZT1zcF9hdGY&psc=1) *(×1)*

- [Neodymium ring magnet, N51, OD15×ID7×5mm](https://patelmagnets.com/shop/od15-x-id7-x-5mm-neodymium-magnet/) *(×1)*

- [USB-C cable](http://amazon.in/Ambrane-Unbreakable-Charging-Braided-Cable/dp/B098NS6PVG/ref=sr_1_2_sspa?crid=M9BW0AHOF6AB&dib=eyJ2IjoiMSJ9.lDE-98HKFWMOuV00UnZQHOVN8B6e1ItwlYioDRiJ4nXc2cuPCVFJBMaA1G48WfOmEbeSAwVOVVhkcdW9b3WzRGnPE_8BkUl1oUJ8cLlUCwQXWekKeqnNFT_7oQHZcoiAMP4k5K8eQDaJ1X4DwO1Z0L_zVQRjBarYWDu3Tqc3THQ5dbIoLQ96lzcQZ7Ggn6-nGWAbqr8RRERKQcV9Ay8ncqR_jk8qbLiEd00Ca5Kj_3Q.i-EkloM_9_VIAI6bsmrCfu1s_p7_eu7Rwzp0FTOlt9E&dib_tag=se&keywords=usb+c&qid=1788413060&sprefix=usb+%2Caps%2C295&sr=8-2-spons&aref=zH7AD8n1Nm&sp_csd=d2lkZ2V0TmFtZT1zcF9hdGY&psc=1) *(×1)*

**Satellite node components** *(for 2 nodes)*

- [Seeed XIAO ESP32-S3, satellite node MCU](https://robu.in/product/seeed-studio-xiao-esp32s3-2-4ghz-wifi-ble-5-0/) *(×2)*

- [KX134-1211 SPI accelerometer](https://robu.in/product/smartelex-triple-axis-accelerometer-breakout-kx134/) *(×2)*

- [INMP441 I2S MEMS microphone](https://robu.in/product/inmp441-mems-high-precision-omnidirectional-microphone-module-i2s/) *(×2)*

- [8 Bit WS2812B 5050 addressable RGB LED](https://robu.in/product/8bit-ws2812-5050-rgb-led-built-full-color-driving-lights-circular-development-board/) *(×2)*

- [9W LED bulb, for status dome diffuser](https://www.amazon.in/Crompton-Dyna-Round-Cool-Light/dp/B0B6FJNH97/ref=sr_1_7?crid=1WFL4GPO6P5LR&dib=eyJ2IjoiMSJ9.XyZtwgJUEsfIg3kZ7nFTImsLPe5r1mEJa_H20SUz3olKYCbFgZnkmbyhoA-Wla8CEQkV5t__DHhqnMopYsJL-56zZykWqQbgYFST9za5VwhseFFIIwoWvLKliusMA28RmbJe0Dy1pFeW7hyzVbY_tmK4HiCHaGcKcNTdNRIQsBpR1Pkqf18r6akvuweK5o3geswtYcHDecuRpcsAYa0WPkUe2RR2veMZwjC00RlY0zwl7nMjOlcNKf0uPb91fHCUvCpKs_Pn4DdT7N3zQZ6mRXZNQLF_SAy_OqcL-PW_6ls.a3O5yF9NfaExtQIvBHhLP1AZ89IPo-RTNP8CqGYTZdQ&dib_tag=se&keywords=bulb&qid=1788412570&sprefix=bul%2Caps%2C285&sr=8-7&th=1) *(×2)*

- [JST-XH 2.54 connector, straight, 6-pin, male and female](https://makerbazar.in/products/male-female-connector-straight?variant=46137886703856) *(×6)*

- [JST-XH 2.54 connector, straight, 4-pin, male and female](https://makerbazar.in/products/male-female-connector-straight?variant=46134456320240) *(×2)*

- [JST-XH 2.54 connector, straight, 3-pin, male and female](https://makerbazar.in/products/male-female-connector-straight?variant=46134287663344) *(×4)*

- [Neodymium ring magnet, N51, OD15×ID7×5mm](https://patelmagnets.com/shop/od15-x-id7-x-5mm-neodymium-magnet/) *(×2)*

- [USB-C cable](http://amazon.in/Ambrane-Unbreakable-Charging-Braided-Cable/dp/B098NS6PVG/ref=sr_1_2_sspa?crid=M9BW0AHOF6AB&dib=eyJ2IjoiMSJ9.lDE-98HKFWMOuV00UnZQHOVN8B6e1ItwlYioDRiJ4nXc2cuPCVFJBMaA1G48WfOmEbeSAwVOVVhkcdW9b3WzRGnPE_8BkUl1oUJ8cLlUCwQXWekKeqnNFT_7oQHZcoiAMP4k5K8eQDaJ1X4DwO1Z0L_zVQRjBarYWDu3Tqc3THQ5dbIoLQ96lzcQZ7Ggn6-nGWAbqr8RRERKQcV9Ay8ncqR_jk8qbLiEd00Ca5Kj_3Q.i-EkloM_9_VIAI6bsmrCfu1s_p7_eu7Rwzp0FTOlt9E&dib_tag=se&keywords=usb+c&qid=1788413060&sprefix=usb+%2Caps%2C295&sr=8-2-spons&aref=zH7AD8n1Nm&sp_csd=d2lkZ2V0TmFtZT1zcF9hdGY&psc=1) *(×2)*

**Motor test rig components** *(validation only)*

- [Arduino Uno R3](https://robu.in/product/arduino-uno-r3/) *(×1)*

- [CNC Shield V3](https://robu.in/product/cnc-shield-v3-engraving-machine-3d-printer-a4988-drv8825-driver-expansion-board/) *(×1)*

- [A4988 stepper driver](https://robu.in/product/a4988-driver-stepper-motor-driver/) *(×3)*

- [NEMA-17 stepper motor, JK42HS48](https://robu.in/product/nema17-4-2-kgcm-stepper-motor/) *(×3)*

- [12–24V DC power supply, ≥3A](https://robu.in/product/mean-well-lrs-150-12-12v-12-5a-150w-smps/) *(×1)*

- M6 × 18mm nut and bolt — flywheel mass and magnetic mounting of sensor nodes to the rig *(×51)*

- [Bearing, 6201 — pump rig](https://in.misumi-ec.com/vona2/detail/110310367019/?HissuCode=C-E6201ZZ&lisid=lisid_13082024_01&utm_source=google&utm_medium=ads&utm_campaign=Economy_Series_Commodity_Products_(IND)&utm_id=21522706028&gad_source=1&gad_campaignid=21522706028&gbraid=0AAAAAC-g747psV-_FD2obaK7wO82jWASa&gclid=Cj0KCQjwkt_UBhDMARIsALpnOAzC5870Ut7FRxvU0EEJXA2nIqoyIsDh_XwEncrZcWIqAkYYs-rjDvYaAmxNEALw_wcB) *(×2)*

- [Bearing, 6004 — turbine rig](https://in.misumi-ec.com/vona2/detail/110310367019/?HissuCode=C-E6004ZZ&lisid=lisid_13082024_01&utm_source=google&utm_medium=ads&utm_campaign=Economy_Series_Commodity_Products_(IND)&utm_id=21522706028&gad_source=1&gad_campaignid=21522706028&gbraid=0AAAAAC-g747psV-_FD2obaK7wO82jWASa&gclid=Cj0KCQjwkt_UBhDMARIsALpnOAzoKeNz1912JANW-ZiETjOcf7qacKxKeHTyOLZcVev89tfoTkthYAEaAt_oEALw_wcB) *(×1)*

- Wooden block, 200×100×20mm — motor rig base *(×3)*

**Shared / common components**

- [eSUN PLA+ Silver, 1.75mm spool — node enclosures](https://robu.in/product/esun-pla-1-75mm-3d-printing-filament-1kg-silver/) *(×1)*

- [eSUN PLA+ Orange, 1.75mm spool — node enclosures](https://robu.in/product/esun-pla-1-75mm-3d-printing-filament-1kg-orange/) *(×1)*

- [eSUN PLA+ Grey, 1.75mm spool — motor rig ](https://robu.in/product/esun-pla-1-75mm-3d-printing-filament-1kg-grey/) *(×2)*

- [eSUN PLA+ Gold, 1.75mm spool — motor rig](https://robu.in/product/esun-pla-1-75mm-3d-printing-filament-1kg-gold/) *(×1)*

- [Multicolor flat ribbon cable, 10-wire, 1 meter](https://robu.in/product/multicolor-flat-ribbon-cable-10-cond-1meter/) *(×1)*

- [2515 JST-XH crimp terminal, female pins](https://makerbazar.in/products/2515-jst-xh-crimp-terminal-female-pins) *(×~100)*

# Appendix B: Schematics

[KiCad ](https://www.kicad.org/)source files and PDF exports for every schematic are bundled into a zip under Attachments. The images below are for quick browsing only.

**Base station wiring**

[IMAGE: Arduino UNO Q — SPI accelerometer, I2S microphone, WS2812B status ring.]

**Satellite node wiring**

[IMAGE: XIAO ESP32S3 — SPI accelerometer, I2S microphone, WS2812B status ring.]

**Motor-driver rig wiring** *(validation only)*

[IMAGE: Arduino Uno + CNC Shield V3, one A4988/DRV8825 driver per stepper axis.]
