# Counting repeat visits at garden feeders

## 1 Introduction

Studies of bird feeding often report how many visits a feeder receives in a day. A sensor at a feeder does not record visits, however. It records each stretch of time during which a bird is on the perch, and a bird that hops off the perch and back within a second is recorded twice. The number of visits therefore depends on how these records are grouped.

We call each such record a detection. Detections are grouped separately at each feeder, and we call the time from the end of one detection to the start of the next detection of the same bird at the same feeder a pause. To group detections into visits, a study needs a rule for deciding when two detections belong to the same visit. The rule used here has one parameter, the return visit gap: the pause length, chosen by the analyst, at or above which the next detection starts a new visit. Detections separated by a pause shorter than the return visit gap are merged into one visit.

The choice of gap changes the result. A short gap splits one long feeding bout into several visits, and a long gap merges separate visits into one. This paper measures how much the visit count changes with the gap, using four feeders in one garden over ten days.

All counts in this paper refer to great tits (Parus major) that carried a leg tag from an earlier ringing project. Before counting, we removed detections that were not visits by a tagged bird. Section 2.3 gives the two rules we used for this.

## 2 Methods

### 2.1 Feeders and sensors

Each feeder had one perch. A bird on the perch interrupted a light beam, and a beam sensor recorded the time at which the beam was interrupted and the time at which it was restored. A tag reader under the perch recorded the identity of every bird that carried a leg tag.

The beam sensor and the tag reader kept separate clocks. The beam sensor's clock drifted by less than 0.01 seconds per day, and we reset it each morning against the tag reader's clock, which was set from the network every night. A detection was assigned to the bird whose tag was read while the beam was interrupted.

### 2.2 Choosing the gap

We set the return visit gap to 5 seconds for the main analysis. In a pilot recording of one day, the pauses fell into two groups: pauses under 3 seconds, when a bird hopped off the perch and back, and pauses over 8 seconds, when it left the feeder. Five seconds lies between the two groups.

The main analysis uses a single gap, but the introduction argued that the count depends on the gap. We therefore repeated the count with gaps of 2 seconds and 10 seconds to see how far the result moves.

### 2.3 Exclusions

We used two exclusion rules. First, we removed detections during which no tag was read, because they cannot be assigned to a tagged bird. Second, we removed detections shorter than 0.2 seconds, which the pilot recording showed to be leaves or insects crossing the beam.

## 3 Results

The four feeders recorded 5,120 detections over the ten days. The two exclusion rules removed 1,010 of them, leaving 4,110 detections of tagged great tits.

With the return visit gap of 5 seconds set in Section 2.2, the 4,110 detections merged into 1,284 visits, summed over the four feeders and the ten days. For each bird and each day we counted its visits at all four feeders together. Visits by birds with two or more visits that day made up 63 percent of the 1,284 visits.

The count moved with the gap. A gap of 2 seconds gave 1,655 visits and a gap of 10 seconds gave 1,102 visits, which is 29 percent more and 14 percent fewer than the 1,284 visits at 5 seconds.

The share of visits made by birds with two or more visits a day was less sensitive to the return visit gap: across the three gaps it stayed between 60 and 66 percent.

The size of the change was similar at all four feeders. With the 2-second gap, the count at each feeder rose by 26 to 31 percent.

## 4 Discussion

A visit count depends on the return visit gap by tens of percent, so two studies that use different gaps cannot compare their visit counts directly. The share of visits made by birds with two or more visits a day changed little, which makes it a safer quantity to compare across studies.

The study covers tagged great tits in one garden over ten days in one season. We did not test whether the 5-second gap suits other species or other seasons.
