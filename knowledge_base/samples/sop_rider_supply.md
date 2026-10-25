---
title: SOP — Rider Supply Gaps (SAMPLE)
doc_type: sop
sample: true
---
# SOP — Rider Supply Gaps (SAMPLE)

> SAMPLE DOCUMENT — illustrative standard operating procedure, not company policy.

## Booking gap response
A booking gap exists when current_capacity_booked is below 90% for a slot. The store manager should open additional slots at least 24 hours before peak slots, and review incentive levels for slots that are repeatedly under-booked.
Note that current_capacity_booked is the source column; when it disagrees with booked_size divided by current_size, raise a data-quality ticket.

## Utilization gap and no-shows
A utilization gap exists when man_hour (rider minutes on ground divided by booked minutes) is below 0.85. Check noshow_count for the slot. Riders with repeated no-shows should be contacted by the fleet team before their next booked slot.
If booked minutes are zero, man_hour cannot be computed; record the utilization check as not evaluated instead of assuming a gap.

## Information to collect
Rider login and logout times, slot cancellation reasons, and no-show counts for the affected slots.
