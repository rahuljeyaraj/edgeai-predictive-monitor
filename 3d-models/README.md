# 3D-printed parts

Thirteen printable parts, each supplied as both **3MF** (plated and ready to
slice, with the colour already assigned) and **STL** (plain mesh, if you would
rather plate it yourself).

Print the 3MF unless you have a reason not to. The plates are already oriented
so nothing needs supports.

File names encode the assembly and the filament colour: `a` is the base station,
`b` is the satellite, `c` is the validation rig.

| File | Contains | Colour | Needed for |
|---|---|---|---|
| `a1_base_station_silver` | Shell, `main_front` + `main_back`, snap-fit halves with a front panel cutout | Silver | Base station |
| `a2_base_station_silver` | Mount kit: `left_plate` + `right_plate` backplates, a standoff `leg`, two `stem_connector` pieces joining leg to plate | Silver | Base station |
| `a3_base_station_orange` | Front bezel: `left_rim` + `right_rim` framing the **lens insert** over the status window, plus a separate `foot` standoff | Orange | Base station |
| `b1_satellite_silver` | Shell, same two-piece pattern as `a1`, scaled down for the XIAO ESP32-S3 pod | Silver | Each satellite |
| `b2_satellite_silver` | Mount kit, same as `a2`, scaled for the satellite shell | Silver | Each satellite |
| `b3_satellite_orange` | Front bezel: `left_rim` + `right_rim` plus `foot`. **No lens insert**, since a satellite has no LED matrix | Orange | Each satellite |
| `c1_belt_drive_rig_grey` | Upright L-bracket (`bracket_main`) with a large bearing bore and base mounting holes | Grey | Belt-drive rig |
| `c2_belt_drive_rig_gold` | Three retaining rings: `bearing_ring_1`, `bearing_ring_2`, and an oblong `stepper_ring` | Gold | Belt-drive rig |
| `c3_belt_drive_rig_gold` | Toothed pulley with hex bore, matching hex shaft with pin holes, and a `bearing_holder` hub coupler | Gold | Belt-drive rig |
| `c6_direct_drive_rig_grey` | U-shaped pillow-block `bracket` with a large bearing bore, mounting holes on base and side | Grey | Direct-drive rig |
| `c7_direct_drive_rig_gold` | `bearing_ring_1`, `bearing_ring_2`, `stepper_ring` and the coupling shaft | Gold | Direct-drive rig |
| `c4_fly_wheel_grey` | Rotor disc (`fly_wheel`) with hex hub and a bolt circle | Grey | **Both** rigs |
| `c5_fly_wheel_ring_gold` | Bolt-on ring (`fly_wheel_ring_1`) matching the flywheel's hole pattern | Gold | **Both** rigs |

## What you actually need to print

- **One base station:** `a1`, `a2`, `a3`
- **Each satellite node:** `b1`, `b2`, `b3`
- **Nothing else is needed for a deployment.** The `c` parts are the validation
  rig used to induce and measure faults on the bench.
- **One rig:** pick belt-drive (`c1`, `c2`, `c3`) or direct-drive (`c6`, `c7`),
  then add the shared flywheel (`c4`, `c5`) either way.

## Print settings

| | |
|---|---|
| Material | PLA+ |
| Nozzle | 0.4 mm |
| Layer height | 0.2 mm |
| Infill | 20% for shells and bezels, 40% or more for rig brackets and the flywheel |
| Supports | none; the plates are already oriented |

Colours are what this build used, not a requirement. Any PLA works.

## The flywheel is the fault injector

`c4` + `c5` carry a bolt circle taking M6 × 18 mm bolts. Adding, removing or
moving bolts around that circle produces a repeatable, measurable imbalance,
which is how the labelled fault recordings behind the classifier were made.

## Optional: the embossed wordmark

[`hardware/enclosure-logo/`](../hardware/enclosure-logo/) has emboss-ready SVGs
for the shell faces, sized for the base station and the satellite separately,
with its own README on slicer settings. Engrave recessed at 0.3 to 0.4 mm, not
raised.

Assembly order is in [`docs/BUILD_GUIDE.md`](../docs/BUILD_GUIDE.md); the parts
that go inside these shells are in
[`docs/BILL_OF_MATERIALS.md`](../docs/BILL_OF_MATERIALS.md).
