# I2C Remittance Extraction — Integration Design

## Purpose

This module is the email-extraction layer of an end-to-end I2C cash
application system for Adani Ports & SEZ.

It reads remittance emails from the corporate mailbox, extracts structured
remittance advice, and emits `RemittanceExtraction` records that the
matching agent (Project 2) consumes to apply payments against the open AR
ledger.

## Position in the system
