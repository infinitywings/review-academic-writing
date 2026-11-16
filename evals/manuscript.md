# Counting repeat visits at garden feeders

## 1 Introduction

Studies of bird feeding often report how many visits a feeder receives in a day. A visit count is easy to collect, but it does not say how many different birds came. One bird that returns twenty times and twenty birds that come once each produce the same count.

Sensors at a feeder register a bird each time it lands; we call each such record a detection. To separate one returning bird from many birds, a study needs a rule for deciding when two detections belong to the same visit. We call the shortest pause that separates two visits by the same bird the return visit gap. Detections of one bird that are closer together than the return visit gap are merged into one visit.

The choice of gap changes the result. A short gap splits one long feeding bout into several visits, and a long gap merges separate visits into one. This paper measures how much the visit count changes with the gap, using four feeders in one garden over ten days.

Before counting, we removed detections that could not be assigned to a bird. Section 2.3 gives the two rules we used for this.

## 2 Methods

### 2.1 Feeders and sensors

Each feeder had one perch. A bird on the perch interrupted a light beam, and an infrared break-beam (IRB) sensor recorded the time at which the beam was interrupted and the time at which it was restored. A tag reader under the perch recorded the identity of every bird that carried a leg tag.

The IRB sensor and the tag reader kept separate clocks. The IRB sensor's clock drifted by up to two seconds per day, so we corrected it each morning against the tag reader's clock, which was set from the network every night.

### 2.2 Choosing the gap

We set the return visit gap to 5 seconds for the main analysis. This value is the median pause between two detections of the same tagged bird in a pilot recording of one day.

The main analysis uses a single gap, but the introduction argued that the count depends on the gap. We therefore repeated the count with gaps of 2 seconds and 10 seconds to see how far the result moves.

### 2.3 Exclusions

We used two exclusion rules. First, we removed detections with no tag reading, because they cannot be assigned to a bird. Second, we removed detections shorter than 0.2 seconds, which the pilot recording showed to be leaves or insects crossing the beam.

## 3 Results

The four feeders recorded 5,120 detections over the ten days. The two exclusion rules removed 1,010 of them, leaving 4,110 detections of tagged birds.

With the return visit gap of 5 seconds set in Section 2.2, the 4,110 detections merged into 1,284 visits. Repeated visitors, meaning tagged birds with more than one visit on the same day, accounted for 63 percent of the visits.

The count moved with the gap. A gap of 2 seconds gave 1,655 visits and a gap of 10 seconds gave 1,102 visits, which is 29 percent more and 14 percent fewer than at 5 seconds. The share of repeated visitors was less sensitive to the return visit gap: it stayed between 60 and 66 percent.

All four feeders showed the same direction of change. At each feeder, the shorter gap raised the count and the longer gap lowered it.

## 4 Discussion

A visit count depends on the return visit gap by tens of percent, so two studies that use different gaps cannot compare their visit counts directly. The share of repeated visitors changed little, which makes it a safer quantity to compare across studies.

The study covers one garden and ten days in one season. We did not test whether the 5-second median pause holds for other species or other seasons.
