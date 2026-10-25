---
title: SLA Definitions (SAMPLE)
doc_type: sla
sample: true
---
# SLA Definitions (SAMPLE)

> SAMPLE DOCUMENT — written for demonstration. Not an actual company SLA.

## OR2A assignment SLA
OR2A (order ready to assignment) is the time between an order being marked ready at the store and the first rider assignment.
For this sample, an order breaches the assignment SLA when OR2A is greater than 0 minutes, matching the client-specific threshold in the OR2A metric definition.

## Breach rate
Breach rate for a store-hour is breached orders divided by total orders. Store-day and city roll-ups must be weighted by order volume: sum of breached orders divided by sum of total orders.

## Severity bands for a store-day
- Green: weighted breach rate at or below 5%.
- Amber: weighted breach rate above 5% and at or below 20%.
- Red: weighted breach rate above 20%. Red store-days require an RCA within one working day.

## Escalation timing
A store-hour with a sustained pileup (pileup flagged for three or more consecutive hours) should be escalated to the city operations lead during the shift.
