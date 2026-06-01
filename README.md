# HB-Tech---Hotel-Mapping
Hotel mapping engine design for HummingBird tech

# HB Hotel Mapping Engine

This project builds a hotel mapping engine that identifies when hotels from different suppliers refer to the same physical hotel.

## Goal

Different suppliers may return the same hotel with different names, addresses, or IDs. This system will create one master hotel record and map all supplier records to it.

## Example

- GRN: The Leela Palace Chennai
- Booking.com: Leela Palace Chennai
- Sabre: The Leela Palace, Adyar

Expected output: all three should map to one master hotel.

## Planned Tech Stack

- Python
- FastAPI
- PostgreSQL
- PostGIS
- pg_trgm
- RapidFuzz

## Main Modules

- Supplier hotel import
- Hotel name normalization
- Location-based matching
- Fuzzy name/address matching
- Match score calculation
- Manual review queue
- Master hotel mapping
