#!/usr/bin/env python3
"""Seed script for fleet-dispatch-manager Firestore database."""

from google.cloud import firestore

# Hardcoded project ID as specified to avoid Agent Platform project number issues
PROJECT_ID = "qwiklabs-gcp-04-e9c25e635f84"
COLLECTION_NAME = "box_trucks"

SEEDED_TRUCKS = [
    {
        "truck_id": "BT-101",
        "model": "Freightliner M2 106 (26ft Box Truck)",
        "status": "available",
        "driver": "Alex Rivera",
        "odometer_km": 124500.0,
        "rate_per_km": 1.85,
        "capacity_kg": 4500,
        "fuel_level": "85%",
        "last_service_odometer_km": 115000.0,
        "next_service_odometer_km": 130000.0,
        "active_trip": None,
    },
    {
        "truck_id": "BT-102",
        "model": "Isuzu NPR-HD (16ft Box Truck)",
        "status": "dispatched",
        "driver": "Marcus Vance",
        "odometer_km": 89240.0,
        "rate_per_km": 1.55,
        "capacity_kg": 3200,
        "fuel_level": "60%",
        "last_service_odometer_km": 80000.0,
        "next_service_odometer_km": 95000.0,
        "active_trip": {
            "trip_id": "TRIP-882",
            "destination": "Regional Fulfillment Hub, Newark",
            "route_distance_km": 180.0,
            "dispatched_at": "2026-10-13T04:30:00Z",
        },
    },
    {
        "truck_id": "BT-103",
        "model": "International MV607 (24ft Box Truck)",
        "status": "available",
        "driver": "Sarah Jenkins",
        "odometer_km": 64100.0,
        "rate_per_km": 1.75,
        "capacity_kg": 4000,
        "fuel_level": "95%",
        "last_service_odometer_km": 55000.0,
        "next_service_odometer_km": 70000.0,
        "active_trip": None,
    },
    {
        "truck_id": "BT-104",
        "model": "Hino 268A (26ft Liftgate Box Truck)",
        "status": "maintenance",
        "driver": "Unassigned",
        "odometer_km": 152000.0,
        "rate_per_km": 1.90,
        "capacity_kg": 4800,
        "fuel_level": "40%",
        "last_service_odometer_km": 135000.0,
        "next_service_odometer_km": 150000.0,
        "maintenance_notes": "Brake pad replacement and hydraulic lift inspection",
        "active_trip": None,
    },
]


def seed():
    print(f"Connecting to Firestore for project: {PROJECT_ID}...")
    db = firestore.Client(project=PROJECT_ID)
    collection = db.collection(COLLECTION_NAME)

    for truck in SEEDED_TRUCKS:
        doc_ref = collection.document(truck["truck_id"])
        doc_ref.set(truck)
        print(f"✓ Seeded truck {truck['truck_id']} ({truck['model']}) - Status: {truck['status']}")

    print(f"\nSuccessfully seeded {len(SEEDED_TRUCKS)} box trucks into '{COLLECTION_NAME}'.")


if __name__ == "__main__":
    seed()
